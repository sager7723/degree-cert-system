"""SQLite and browser-route acceptance with a test-only in-memory chain."""
from contextlib import closing, contextmanager
import copy
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from urllib.parse import parse_qs, urlparse

from flask import template_rendered
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from degreeapp import create_app, db
from degreeapp.chain import ChainError, ROLE_ORGS, SCHEMA
from degreeapp.domain import canonical_document, canonical_json, content_hash
from degreeapp.service import ApplicationError
from degreeapp.setup import initialize


def document(cert_id="WEB_TEST_A", **changes):
    value = {"schema_version": SCHEMA, "cert_id": cert_id, "student_id": "DEMO-STUDENT-001",
             "student_name": "演示同学", "school_name": "演示大学", "major": "计算机科学与技术",
             "degree": "学士", "graduation_date": "2026-06-30"}
    value.update(changes)
    return value


class FakeChain:
    """This fake is confined to tests and injected through the test factory."""
    def __init__(self):
        self.records, self.receipts = {}, {}
        self.submissions, self.queries = [], []
        self.outage = self.uncertain = self.reject = False
        self.before_submit = None

    def _available(self):
        if self.outage:
            raise ChainError("unavailable", "测试节点不可达。")

    def health(self):
        self._available()
        return {"contract": "degreecert", "initialized": True, "schema_version": SCHEMA,
                "issuer_org": ROLE_ORGS["uni"], "regulator_org": ROLE_ORGS["edu"]}

    def seed(self, source, revoked=False):
        record = {"schema_version": SCHEMA, "cert_id": source["cert_id"], "content_hash": content_hash(source),
            "status": "revoked" if revoked else "valid", "issuer_org": ROLE_ORGS["uni"],
            "issued_txid": uuid.uuid4().hex + uuid.uuid4().hex, "issued_at": "1790498330"}
        if revoked:
            record.update(revoked_by_org=ROLE_ORGS["edu"], revoked_txid="d" * 64,
                          revoked_at="1790498430", revoke_reason="教学演示撤销")
        self.records[source["cert_id"]] = record

    def get_certificate(self, cert_id, role="query"):
        self._available()
        if cert_id not in self.records:
            raise ChainError("not_found", "链上没有该证书。")
        return copy.deepcopy(self.records[cert_id])

    def verify_certificate(self, cert_id, hashed):
        record = self.get_certificate(cert_id)
        match = record["content_hash"] == hashed
        return {"schema_version": SCHEMA, "cert_id": cert_id, "content_hash": record["content_hash"],
                "status": record["status"], "content_match": match, "valid": match and record["status"] == "valid"}

    def submit(self, role, method, params, tx_id):
        self.submissions.append((role, method, copy.deepcopy(params), tx_id))
        if self.before_submit:
            self.before_submit(role, method, params, tx_id)
        self._available()
        if self.reject:
            raise ChainError("rejected", "测试：链上明确拒绝。", tx_id=tx_id)
        cert_id = params["cert_id"]
        if method == "issueCertificate":
            if role != "uni" or cert_id in self.records:
                raise ChainError("rejected", "测试：越权或重复证书。", tx_id=tx_id)
            self.records[cert_id] = {"schema_version": SCHEMA, "cert_id": cert_id,
                "content_hash": params["content_hash"], "status": "valid", "issuer_org": ROLE_ORGS["uni"],
                "issued_txid": tx_id, "issued_at": "1790498330"}
        else:
            if role != "edu" or self.records[cert_id]["status"] != "valid":
                raise ChainError("rejected", "测试：越权或重复撤销。", tx_id=tx_id)
            self.records[cert_id].update(status="revoked", revoked_by_org=ROLE_ORGS["edu"],
                revoked_txid=tx_id, revoked_at="1790498430", revoke_reason=params["reason"])
        self.receipts[tx_id] = copy.deepcopy(self.records[cert_id])
        if self.uncertain:
            raise ChainError("unavailable", "提交后响应超时，请查询原交易。", uncertain=True, tx_id=tx_id)
        return copy.deepcopy(self.receipts[tx_id])

    def transaction(self, role, method, params, tx_id):
        self.queries.append((role, method, copy.deepcopy(params), tx_id))
        self._available()
        if tx_id not in self.receipts:
            raise ChainError("unavailable", "交易尚未查到。", uncertain=True, tx_id=tx_id)
        return copy.deepcopy(self.receipts[tx_id])


@contextmanager
def rendered_context(app):
    contexts = []
    def record(sender, template, context, **extra):
        contexts.append((template.name, context))
    template_rendered.connect(record, app)
    try:
        yield contexts
    finally:
        template_rendered.disconnect(record, app)


class SearchFormParser(HTMLParser):
    """Read the form a browser would submit without assuming its field name."""
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None
        self.select = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action", ""),
                            "method": attrs.get("method", "get").lower(), "inputs": [], "selects": []}
        elif tag == "input" and self.current is not None:
            self.current["inputs"].append(attrs)
        elif tag == "select" and self.current is not None:
            self.select = dict(attrs, options=[])
        elif tag == "option" and self.select is not None:
            self.select["options"].append(attrs)
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None
        elif tag == "select" and self.select is not None:
            self.current["selects"].append(self.select)
            self.select = None


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Login still uses Werkzeug's real hash verifier; cheap fixture hashing
        # keeps tests fast without changing the application's production cost.
        cls.password_hash = generate_password_hash("Test-password-123!", method="pbkdf2:sha256:1000")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.database = self.project / "instance/degree-cert.sqlite3"
        db.initialize(self.database)
        (self.project / "instance/web-secret.key").write_text("f" * 64, encoding="ascii")
        with db.transaction(self.database) as connection:
            for role, label in (("uni", "高校签发员"), ("edu", "监管机构"), ("query", "查询机构")):
                connection.execute("INSERT INTO users(username,password_hash,display_name,role) VALUES(?,?,?,?)",
                                   (role, self.password_hash, label, role))
        self.chain = FakeChain()
        self.app = create_app(self.project, testing=True, chain=self.chain)
        self.client = self.app.test_client()
        self.registry = self.app.extensions["registry"]

    def csrf(self):
        self.client.get("/login")
        with self.client.session_transaction() as current:
            return current["csrf"]

    def login(self, role):
        response = self.client.post("/login", data={"csrf_token": self.csrf(), "username": role,
                                                  "password": "Test-password-123!"})
        self.assertEqual(response.status_code, 302)

    def post(self, path, data=None, **kwargs):
        values = dict(data or {})
        values["csrf_token"] = self.csrf()
        return self.client.post(path, data=values, **kwargs)

    def issue(self, source=None, request_id=None):
        values = dict(source or document())
        values["request_id"] = request_id or uuid.uuid4().hex
        return self.post("/certificates/new", values)

    def actor(self, role):
        with closing(db.connect(self.database)) as connection:
            return dict(connection.execute("SELECT * FROM users WHERE role=?", (role,)).fetchone())

    def submit_certificate_search_form(self, term, *, source="/certificates", field=None, order=None):
        page = self.client.get(source)
        self.assertEqual(page.status_code, 200)
        parser = SearchFormParser()
        parser.feed(page.get_data(as_text=True))
        forms = [form for form in parser.forms
                 if any(field.get("type") == "search" for field in form["inputs"])]
        self.assertEqual(len(forms), 1)
        form = forms[0]
        self.assertEqual(form["method"], "get")
        search_fields = [field for field in form["inputs"] if field.get("type") == "search"]
        self.assertEqual(len(search_fields), 1)
        self.assertTrue(search_fields[0].get("name"))
        values = {field["name"]: field.get("value", "") for field in form["inputs"]
                  if field.get("name") and "disabled" not in field}
        for select in form["selects"]:
            selected = next((item for item in select["options"] if "selected" in item), select["options"][0])
            values[select["name"]] = selected.get("value", "")
        for desired in (field, order):
            if desired is None:
                continue
            matches = [select for select in form["selects"]
                       if desired in {option.get("value") for option in select["options"]}]
            self.assertEqual(len(matches), 1, f"missing or ambiguous selector for {desired}")
            values[matches[0]["name"]] = desired
        values[search_fields[0]["name"]] = term
        return self.client.get(form["action"] or source, query_string=values)

    def seed_certificates(self, cases):
        """Fast local fixtures, avoiding hundreds of irrelevant mock transactions."""
        with db.transaction(self.database) as connection:
            for case in cases:
                source = case["document"]
                status = case.get("status", "valid")
                ledger = None
                if status in ("valid", "revoked"):
                    self.chain.seed(source, revoked=status == "revoked")
                    ledger = self.chain.records[source["cert_id"]]
                    if case.get("issued_at") is None:
                        ledger.pop("issued_at", None)
                    else:
                        ledger["issued_at"] = str(case["issued_at"])
                    if status == "revoked":
                        ledger["revoked_at"] = "900"
                connection.execute(
                    "INSERT INTO certificates(cert_id,student_id,student_name,document_json,content_hash,"
                    "local_status,ledger_json,created_at,confirmed_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (source["cert_id"], source["student_id"], source["student_name"], canonical_json(source),
                     content_hash(source), status, canonical_json(ledger) if ledger is not None else None,
                     case.get("created_at", "2026-01-01T00:00:00+00:00"),
                     case.get("confirmed_at", "2026-01-01T00:00:00+00:00")))

    def test_login_requires_csrf_and_rejects_bad_password(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.post("/login", data={"username": "uni", "password": "Test-password-123!"}).status_code, 400)
        self.assertEqual(self.post("/login", {"username": "uni", "password": "wrong"}).status_code, 401)
        self.login("uni")
        self.assertEqual(self.client.get("/").status_code, 200)
        response = self.client.post("/certificates/new", data=document())
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.chain.submissions)

    def test_direct_post_cannot_bypass_role_checks(self):
        for role in ("query", "edu"):
            with self.subTest(role=role):
                self.login(role)
                self.assertEqual(self.issue().status_code, 403)
        self.login("uni")
        self.assertEqual(self.post("/certificates/WEB_TEST_A/revoke", {"reason": "越权", "request_id": uuid.uuid4().hex}).status_code, 403)
        self.assertFalse(self.chain.submissions)

    def test_two_certificates_for_same_student_keep_distinct_documents(self):
        self.login("uni")
        a, b = document(), document("WEB_TEST_B", major="软件工程", degree="硕士", graduation_date="2028-06-30")
        self.assertEqual(self.issue(a).status_code, 302)
        self.assertEqual(self.issue(b).status_code, 302)
        records = self.registry.search("DEMO-STUDENT-001")
        self.assertEqual({r["cert_id"] for r in records}, {"WEB_TEST_A", "WEB_TEST_B"})
        self.assertEqual(self.registry.get("WEB_TEST_A")["major"], a["major"])
        self.assertEqual(self.registry.get("WEB_TEST_B")["major"], b["major"])
        for _, method, params, tx_id in self.chain.submissions:
            record = self.registry.get(params["cert_id"])
            self.assertEqual(method, "issueCertificate")
            self.assertEqual(set(params), {"cert_id", "content_hash"})
            self.assertEqual(params["content_hash"], content_hash(record["document"]))
            self.assertEqual(record["local_status"], "valid")

    def test_intent_is_committed_before_chain_submit_without_write_lock(self):
        self.login("uni")
        def before_submit(role, method, params, tx_id):
            with db.transaction(self.database) as connection:
                row = connection.execute("SELECT * FROM operations WHERE tx_id=?", (tx_id,)).fetchone()
                self.assertEqual(row["status"], "pending")
                self.assertEqual(row["params_json"], canonical_json(params))
                db.audit(connection, None, "测试期间独立写入", params["cert_id"], "证明调用链时未持有 SQLite 写锁")
        self.chain.before_submit = before_submit
        self.assertEqual(self.issue().status_code, 302)

    def test_duplicate_form_submits_once_and_changed_content_rejected(self):
        self.login("uni")
        request_id = uuid.uuid4().hex
        self.assertEqual(self.issue(request_id=request_id).status_code, 302)
        self.assertEqual(self.issue(request_id=request_id).status_code, 302)
        self.assertEqual(self.issue(document(major="改动专业"), request_id=request_id).status_code, 400)
        self.assertEqual(len(self.chain.submissions), 1)
        self.assertEqual(self.registry.get("WEB_TEST_A")["major"], "计算机科学与技术")

    def test_full_verify_tamper_revoke_flow_keeps_second_certificate_valid(self):
        self.login("uni")
        a, b = document(), document("WEB_TEST_B", major="软件工程", degree="硕士")
        self.issue(a)
        self.issue(b)
        with rendered_context(self.app) as contexts:
            self.assertEqual(self.post("/verify", a).status_code, 200)
        self.assertTrue(contexts[-1][1]["result"]["valid"])
        with rendered_context(self.app) as contexts:
            self.post("/verify", dict(a, major="被篡改的专业"))
        self.assertFalse(contexts[-1][1]["result"]["content_match"])
        self.assertFalse(contexts[-1][1]["result"]["valid"])
        self.login("edu")
        self.assertEqual(self.post("/certificates/WEB_TEST_A/revoke", {"reason": "录屏演示撤销", "request_id": uuid.uuid4().hex}).status_code, 302)
        self.login("query")
        with rendered_context(self.app) as contexts:
            self.post("/verify", a)
        result = contexts[-1][1]["result"]
        self.assertTrue(result["content_match"])
        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "revoked")
        with rendered_context(self.app) as contexts:
            self.post("/verify", b)
        self.assertTrue(contexts[-1][1]["result"]["valid"])
        self.assertEqual(self.registry.get("WEB_TEST_A")["local_status"], "revoked")
        self.assertEqual(self.registry.get("WEB_TEST_B")["local_status"], "valid")

    def test_blank_certificate_id_reuses_same_form_without_second_transaction(self):
        self.login("uni")
        request_id = uuid.uuid4().hex
        source = document(cert_id="")
        first = self.issue(source, request_id=request_id)
        second = self.issue(source, request_id=request_id)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first.location, second.location)
        self.assertEqual(len(self.chain.submissions), 1)
        self.assertEqual(len(self.registry.search()), 1)
        self.assertIsNotNone(self.registry.get("DEG_" + request_id))

    def test_unicode_csrf_is_rejected_without_server_error(self):
        self.csrf()
        response = self.client.post("/login", data={"csrf_token": "无效标识", "username": "uni", "password": "x"})
        self.assertEqual(response.status_code, 400)

    def test_chain_only_certificate_can_be_verified_without_broken_detail_link(self):
        source = document("EXTERNAL_CERT")
        self.chain.seed(source)
        self.login("query")
        with rendered_context(self.app) as contexts:
            response = self.post("/verify", source)
        self.assertTrue(contexts[-1][1]["result"]["valid"])
        self.assertFalse(contexts[-1][1]["result"]["local_available"])
        self.assertNotIn(b'href="/certificates/EXTERNAL_CERT"', response.data)

    def test_local_document_tampering_detected_by_detail_and_verify(self):
        self.login("uni")
        self.issue()
        tampered = document(major="本地数据库中被修改的专业")
        with db.transaction(self.database) as connection:
            connection.execute("UPDATE certificates SET document_json=? WHERE cert_id=?", (canonical_json(tampered), "WEB_TEST_A"))
        with rendered_context(self.app) as contexts:
            self.assertEqual(self.client.get("/certificates/WEB_TEST_A").status_code, 200)
        self.assertTrue(contexts[-1][1]["integrity"]["available"])
        self.assertFalse(contexts[-1][1]["integrity"]["matches"])
        with rendered_context(self.app) as contexts:
            self.post("/verify", tampered)
        self.assertFalse(contexts[-1][1]["result"]["valid"])

    def test_chain_outage_never_uses_local_success_as_live_verification(self):
        self.login("uni")
        self.issue()
        self.chain.outage = True
        with rendered_context(self.app) as contexts:
            self.post("/verify", document())
        self.assertIsNone(contexts[-1][1]["result"])
        self.assertTrue(contexts[-1][1]["error"])
        with rendered_context(self.app) as contexts:
            self.client.get("/certificates/WEB_TEST_A")
        self.assertFalse(contexts[-1][1]["integrity"]["available"])
        self.assertIsNone(contexts[-1][1]["chain_record"])
        with rendered_context(self.app) as contexts:
            self.client.get("/")
        self.assertFalse(contexts[-1][1]["chain_status"]["available"])

    def test_uncertain_operation_survives_restart_and_reconciles_readonly(self):
        self.login("uni")
        self.chain.uncertain = True
        self.issue()
        pending = self.registry.pending("WEB_TEST_A")
        self.assertIsNotNone(pending)
        self.assertEqual(self.registry.get("WEB_TEST_A")["local_status"], "pending")
        original_tx = pending["tx_id"]
        self.app = create_app(self.project, testing=True, chain=self.chain)
        self.client = self.app.test_client()
        self.registry = self.app.extensions["registry"]
        self.login("uni")
        self.assertEqual(self.post(f'/operations/{pending["id"]}/reconcile').status_code, 302)
        self.assertIsNone(self.registry.pending("WEB_TEST_A"))
        self.assertEqual(self.registry.get("WEB_TEST_A")["local_status"], "valid")
        self.assertEqual(len(self.chain.submissions), 1)
        self.assertEqual(len(self.chain.queries), 1)
        self.assertEqual(self.chain.queries[0][3], original_tx)

    def test_foreign_account_cannot_reconcile_another_accounts_operation(self):
        self.login("uni")
        self.chain.uncertain = True
        self.issue()
        pending = self.registry.pending("WEB_TEST_A")
        with self.assertRaises(ApplicationError):
            self.registry.reconcile(self.actor("edu"), pending["id"])
        self.login("edu")
        self.assertEqual(self.post(f'/operations/{pending["id"]}/reconcile').status_code, 302)
        self.assertEqual(self.chain.queries, [])
        self.assertIsNotNone(self.registry.pending("WEB_TEST_A"))

    def test_explicit_rejection_is_failed_but_network_error_stays_pending(self):
        self.login("uni")
        self.chain.reject = True
        self.issue()
        self.assertEqual(self.registry.get("WEB_TEST_A")["local_status"], "failed")
        self.assertEqual(self.registry.operations(self.actor("uni")["id"])[0]["status"], "failed")
        self.chain.reject = False
        self.chain.outage = True
        self.issue(document("WEB_TEST_B"))
        self.assertEqual(self.registry.get("WEB_TEST_B")["local_status"], "pending")
        self.assertIsNotNone(self.registry.pending("WEB_TEST_B"))

    def test_query_role_sees_exact_certificate_lookup_without_student_directory(self):
        self.login("uni")
        self.issue()
        self.login("query")
        with rendered_context(self.app) as contexts:
            self.client.get("/certificates")
        self.assertEqual(contexts[-1][1]["certificates"], [])
        with rendered_context(self.app) as contexts:
            self.client.get("/certificates?q=DEMO-STUDENT-001")
        self.assertEqual(contexts[-1][1]["certificates"], [])
        with rendered_context(self.app) as contexts:
            self.client.get("/certificates?q=WEB_TEST_A")
        self.assertEqual(len(contexts[-1][1]["certificates"]), 1)
        self.assertEqual(self.client.get("/audit").status_code, 403)
        self.assertEqual(self.client.get("/operations").status_code, 403)

    def test_query_certificate_search_submits_rendered_form_and_requires_exact_id(self):
        self.login("uni")
        self.issue(document("WEB_TEST_A"))
        self.issue(document("WEB_TEST_B"))
        self.login("query")
        for source in ("/", "/certificates"):
            for term, expected in (("WEB_TEST_A", ["WEB_TEST_A"]),
                                   ("WEB_TEST_B", ["WEB_TEST_B"]),
                                   ("WEB_TEST", []), ("UNKNOWN_CERT", []),
                                   ("DEMO-STUDENT-001", [])):
                with self.subTest(source=source, term=term), rendered_context(self.app) as contexts:
                    response = self.submit_certificate_search_form(term, source=source)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(contexts[-1][0], "certificates.html")
                    self.assertEqual(contexts[-1][1]["search"], term)
                    self.assertEqual([row["cert_id"] for row in contexts[-1][1]["certificates"]], expected)
                    for cert_id in expected:
                        self.assertIn(f'href="/certificates/{cert_id}"', response.get_data(as_text=True))

    def test_issuer_certificate_search_submits_rendered_form_and_filters_results(self):
        self.login("uni")
        self.issue(document("WEB_TEST_A"))
        self.issue(document("WEB_TEST_B", student_name="另一位同学", student_id="SECOND-STUDENT"))
        for term, expected in (("WEB_TEST_A", ["WEB_TEST_A"]),
                               ("另一位", ["WEB_TEST_B"]),
                               ("SECOND-STUDENT", ["WEB_TEST_B"]),
                               ("UNKNOWN_CERT", [])):
            with self.subTest(term=term), rendered_context(self.app) as contexts:
                response = self.submit_certificate_search_form(term)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(contexts[-1][1]["search"], term)
                self.assertEqual([row["cert_id"] for row in contexts[-1][1]["certificates"]], expected)

    def test_certificate_search_legacy_url_alias_and_canonical_parameter_precedence(self):
        self.login("uni")
        self.issue(document("WEB_TEST_A"))
        self.issue(document("WEB_TEST_B"))
        self.login("query")
        for params, term, expected in (
                ({"search": "WEB_TEST_A"}, "WEB_TEST_A", ["WEB_TEST_A"]),
                ({"q": "WEB_TEST_B", "search": "WEB_TEST_A"}, "WEB_TEST_B", ["WEB_TEST_B"]),
                ({"q": "", "search": "WEB_TEST_A"}, "", [])):
            with self.subTest(params=params), rendered_context(self.app) as contexts:
                response = self.client.get("/certificates", query_string=params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(contexts[-1][1]["search"], term)
                self.assertEqual([row["cert_id"] for row in contexts[-1][1]["certificates"]], expected)

    def test_query_name_search_returns_every_certificate_for_distinct_same_name_students(self):
        self.seed_certificates([
            {"document": document("SAME_NAME_A", student_name="同名学生", student_id="STUDENT-1"), "issued_at": 10},
            {"document": document("SAME_NAME_B", student_name="同名学生", student_id="STUDENT-2"), "issued_at": 20},
            {"document": document("SAME_NAME_C", student_name="同名学生", student_id="STUDENT-1", degree="硕士"), "issued_at": 30},
            {"document": document("OTHER_NAME", student_name="同名学生甲", student_id="STUDENT-3"), "issued_at": 40},
        ])
        self.login("query")
        for source in ("/", "/certificates"):
            with self.subTest(source=source), rendered_context(self.app) as contexts:
                response = self.submit_certificate_search_form("  同名学生  ", source=source, field="student_name")
                self.assertEqual(response.status_code, 200)
                result = contexts[-1][1]["search_result"]
                self.assertEqual(result["total"], 3)
                self.assertEqual([item["cert_id"] for item in result["items"]],
                                 ["SAME_NAME_A", "SAME_NAME_B", "SAME_NAME_C"])
                self.assertEqual({item["student_id"] for item in result["items"]}, {"STUDENT-1", "STUDENT-2"})
        with rendered_context(self.app) as contexts:
            self.submit_certificate_search_form("同名", field="student_name")
        self.assertEqual(contexts[-1][1]["search_result"]["total"], 0)
        with rendered_context(self.app) as contexts:
            self.submit_certificate_search_form("STUDENT-1", field="student_id")
        self.assertEqual([item["cert_id"] for item in contexts[-1][1]["certificates"]],
                         ["SAME_NAME_A", "SAME_NAME_C"])
        with rendered_context(self.app) as contexts:
            self.submit_certificate_search_form(" ", field="student_name")
        self.assertEqual(contexts[-1][1]["certificates"], [])
        self.assertEqual(self.issue().status_code, 403)
        self.assertEqual(self.post("/certificates/SAME_NAME_A/revoke",
                                  {"reason": "越权", "request_id": uuid.uuid4().hex}).status_code, 403)
        self.assertFalse(self.chain.submissions)

    def test_exact_text_search_normalizes_unicode_and_whitespace(self):
        self.seed_certificates([{"document": document("UNICODE_NAME", student_name="Caf\u00e9",
                                                         school_name="\u00c9cole"), "issued_at": 10}])
        self.login("query")
        for field, term in (("student_name", "  Cafe\u0301  "), ("school_name", " E\u0301cole ")):
            with self.subTest(field=field), rendered_context(self.app) as contexts:
                response = self.submit_certificate_search_form(term, field=field)
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row["cert_id"] for row in contexts[-1][1]["certificates"]], ["UNICODE_NAME"])

    def test_school_search_paginates_all_matches_beyond_old_200_record_limit(self):
        cases = [{"document": document(f"SCHOOL_{index:03}", student_id=f"STUDENT-{index}",
                                        school_name="共同大学"), "issued_at": 1000 + index}
                 for index in range(205)]
        cases.append({"document": document("OTHER_SCHOOL", school_name="共同大学分校"), "issued_at": 999})
        self.seed_certificates(cases)
        self.login("query")
        with rendered_context(self.app) as contexts:
            first = self.submit_certificate_search_form("共同大学", field="school_name", order="asc")
        self.assertEqual(first.status_code, 200)
        result = contexts[-1][1]["search_result"]
        self.assertEqual((result["total"], result["page"], result["pages"], result["per_page"]), (205, 1, 11, 20))
        parser = SearchFormParser()
        parser.feed(first.get_data(as_text=True))
        next_links = [href for href in parser.links if parse_qs(urlparse(href).query).get("page") == ["2"]]
        self.assertTrue(next_links, "Page 2 must be reachable from the rendered pagination links")
        second_params = parse_qs(urlparse(next_links[0]).query)
        self.assertEqual(second_params.get("q"), ["共同大学"])
        self.assertEqual(second_params.get("field"), ["school_name"])
        self.assertEqual(second_params.get("order"), ["asc"])
        seen = [item["cert_id"] for item in result["items"]]
        for page in range(2, result["pages"] + 1):
            with rendered_context(self.app) as contexts:
                response = self.client.get("/certificates", query_string={
                    "q": "共同大学", "field": "school_name", "order": "asc", "page": page})
            self.assertEqual(response.status_code, 200)
            current = contexts[-1][1]["search_result"]
            self.assertEqual(current["total"], 205)
            self.assertEqual(current["page"], page)
            self.assertLessEqual(len(current["items"]), 20)
            seen.extend(item["cert_id"] for item in current["items"])
        self.assertEqual(seen, [f"SCHOOL_{index:03}" for index in range(205)])
        self.assertEqual(len(set(seen)), 205)

    def test_issuance_sort_is_numeric_stable_and_unaffected_by_revocation_or_local_times(self):
        self.seed_certificates([
            {"document": document("TIME_TWO"), "issued_at": 2, "created_at": "2099-01-01", "confirmed_at": "2099-01-01"},
            {"document": document("TIME_TEN_A"), "issued_at": 10, "status": "revoked", "confirmed_at": "2099-01-01"},
            {"document": document("TIME_TEN_B"), "issued_at": 10, "created_at": "1900-01-01"},
            {"document": document("TIME_HUNDRED"), "issued_at": 100, "created_at": "1900-01-01"},
            {"document": document("UNKNOWN_ISSUED_AT")},
            {"document": document("UNISSUED_PENDING"), "status": "pending"},
        ])
        self.login("query")
        for order, known in (("asc", ["TIME_TWO", "TIME_TEN_A", "TIME_TEN_B", "TIME_HUNDRED"]),
                             ("desc", ["TIME_HUNDRED", "TIME_TEN_A", "TIME_TEN_B", "TIME_TWO"])):
            with self.subTest(order=order), rendered_context(self.app) as contexts:
                response = self.submit_certificate_search_form("演示大学", field="school_name", order=order)
                self.assertEqual(response.status_code, 200)
                self.assertEqual([item["cert_id"] for item in contexts[-1][1]["certificates"]],
                                 known + ["UNISSUED_PENDING", "UNKNOWN_ISSUED_AT"])

    def test_dashboard_preserves_staff_recent_records_and_gives_query_search_entry(self):
        self.seed_certificates([{"document": document(), "issued_at": 10}])
        for role in ("uni", "edu"):
            self.login(role)
            page = self.client.get("/").get_data(as_text=True)
            self.assertIn("证书动态", page)
            self.assertIn("WEB_TEST_A", page)
        self.login("query")
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("证书动态", page)
        self.assertNotIn("WEB_TEST_A", page)
        with rendered_context(self.app) as contexts:
            self.submit_certificate_search_form("WEB_TEST_A", source="/", field="cert_id", order="desc")
        self.assertEqual([item["cert_id"] for item in contexts[-1][1]["certificates"]], ["WEB_TEST_A"])

    def test_export_and_json_upload_verify_roundtrip(self):
        self.login("uni")
        self.issue()
        exported = self.client.get("/certificates/WEB_TEST_A/export")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(json.loads(exported.data), document())
        with rendered_context(self.app) as contexts:
            self.post("/verify", {"document_file": (io.BytesIO(exported.data), "certificate.json")}, content_type="multipart/form-data")
        self.assertTrue(contexts[-1][1]["result"]["valid"])

    def test_json_upload_rejects_duplicate_extra_or_nonobject_documents(self):
        self.login("query")
        for raw in (b'[]', b'{"cert_id":"A","cert_id":"B"}', json.dumps(dict(document(), extra="x")).encode()):
            with self.subTest(raw=raw), rendered_context(self.app) as contexts:
                response = self.post("/verify", {"document_file": (io.BytesIO(raw), "certificate.json")}, content_type="multipart/form-data")
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(contexts[-1][1]["result"])
            self.assertTrue(contexts[-1][1]["error"])

    def test_repeated_setup_preserves_passwords_and_imports_without_transactions(self):
        project = self.project / "setup-fixture"
        (project / "instance").mkdir(parents=True)
        a, b = document("DEMO_A"), document("DEMO_B", major="软件工程", degree="硕士")
        chain = FakeChain()
        chain.seed(a, revoked=True)
        chain.seed(b)
        state = {"phase": "complete", "pending": None, "contract": "degree_cert_v1",
                 "cert_ids": ["DEMO_A", "DEMO_B"], "documents": {"DEMO_A": a, "DEMO_B": b},
                 "hashes": {"DEMO_A": content_hash(a), "DEMO_B": content_hash(b)}}
        (project / "instance/stage05-state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(initialize(project, chain)["imported"], 2)
        credentials = (project / "instance/demo-accounts.json").read_bytes()
        secret = (project / "instance/web-secret.key").read_bytes()
        path = project / "instance/degree-cert.sqlite3"
        with closing(db.connect(path)) as connection:
            hashes_before = [tuple(r) for r in connection.execute("SELECT username,password_hash FROM users ORDER BY id")]
        self.assertEqual(initialize(project, chain)["imported"], 0)
        self.assertEqual(credentials, (project / "instance/demo-accounts.json").read_bytes())
        self.assertEqual(secret, (project / "instance/web-secret.key").read_bytes())
        with closing(db.connect(path)) as connection:
            self.assertEqual(hashes_before, [tuple(r) for r in connection.execute("SELECT username,password_hash FROM users ORDER BY id")])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM certificates").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 0)
        self.assertEqual(chain.submissions, [])
        self.assertEqual(chain.queries, [])


if __name__ == "__main__":
    unittest.main()
