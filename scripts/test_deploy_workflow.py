"""Offline orchestration tests. Fake CMC is used only in these tests."""
import base64
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cmc_receipts import ReceiptError
from deploy_and_verify import (
    CONTRACT, EXPECTED_ARCHIVE_SHA256, ISSUER, KNOWN_CERT_A, KNOWN_DEPLOYMENT_TXID,
    KNOWN_HASH_A, KNOWN_REJECTION_TXID, REGULATOR, ROLE_ORGS, RUNTIME, SCHEMA, VERSION,
    Stage, StageError, content_hash, demo_document,
)


class FakeCMCStage(Stage):
    def __init__(self, project, report):
        super().__init__(project, report)
        self.records = {}
        self.transactions = {}
        self.writes = 0
        self.write_labels = []
        self.exists = False
        self.break_query = False
        self.crash_first_invoke = False

    def preflight(self):
        self.string_flags = {key: [] for key in ("get", "invoke", "create", "tx")}

    def archive(self):
        return self.project / "fake.7z"

    def admin_paths(self):
        return "original-sign-keys", "original-sign-certificates"

    def command(self, label, args, *, root=False, timeout=55):
        self.last_label = label
        flags = dict(item[2:].split("=", 1) for item in args if item.startswith("--") and "=" in item)
        if args[:2] == ["query", "tx"]:
            if self.break_query:
                return "[ERROR] temporarily unavailable", 1
            return json.dumps(self.transactions[args[2]]), 0
        action = args[3]
        method = flags.get("method")
        params = json.loads(flags.get("params", "{}"))
        if action == "get":
            if method == "GET_CONTRACT_LIST":
                data = [{"name": CONTRACT}] if self.exists else []
            elif method == "GET_CONTRACT_INFO":
                data = {"name": CONTRACT, "version": "1.0", "runtime_type": 6}
            elif method == "health":
                data = {"contract": "degreecert", "initialized": True, "schema_version": SCHEMA,
                        "issuer_org": ISSUER, "regulator_org": REGULATOR}
            elif method == "getCertificate":
                data = self.records[params["cert_id"]]
            else:
                record = self.records[params["cert_id"]]
                match = record["content_hash"] == params["content_hash"]
                data = {"schema_version": SCHEMA, "cert_id": record["cert_id"], "content_hash": record["content_hash"],
                        "status": record["status"], "content_match": match, "valid": match and record["status"] == "valid"}
            if not method.startswith("GET_CONTRACT_"):
                data = {"ok": True, "data": data}
            return json.dumps({"contract_result": {"result": data}}), 0
        # Verify the durable journal is present *before* every submission.
        journal = json.loads(self.state_path.read_text(encoding="utf-8"))
        assert journal["pending"]["label"] == label
        self.writes += 1
        self.write_labels.append(label)
        if action == "create":
            assert root and flags["runtime-type"] == "DOCKER_GO"
            assert params == {"issuer_org": ISSUER, "regulator_org": REGULATOR}
            self.exists = True
            return json.dumps({"tx_id": "d" * 64, "contract_result": {"result": {"name": CONTRACT}}}), 0
        assert not root
        if self.crash_first_invoke:
            raise OSError("simulated lost connection")
        tx_id = flags["tx-id"]
        assert journal["pending"]["detail"]["tx_id"] == tx_id
        role = Path(flags["sdk-conf-path"]).stem.replace("sdk-", "")
        cert_id = params["cert_id"]
        tag = None
        if (method == "issueCertificate" and role != "uni") or (method == "revokeCertificate" and role != "edu"):
            tag = "UNAUTHORIZED"
        elif method == "issueCertificate" and cert_id in self.records:
            tag = "CERTIFICATE_ALREADY_EXISTS"
        elif method == "revokeCertificate" and self.records[cert_id]["status"] == "revoked":
            tag = "CERTIFICATE_ALREADY_REVOKED"
        elif method == "issueCertificate":
            self.records[cert_id] = {"schema_version": SCHEMA, "cert_id": cert_id, "content_hash": params["content_hash"],
                                     "status": "valid", "issuer_org": ISSUER, "issued_at": "1790460000", "issued_txid": tx_id}
        else:
            self.records[cert_id].update(status="revoked", revoked_by_org=REGULATOR, revoked_txid=tx_id,
                                         revoked_at="1790460100", revoke_reason=params["reason"])
        if tag:
            # The real SDK's sandbox moves Error.Message into Result and uses
            # the generic Message="Fail". Protobuf JSON base64-encodes Result.
            result = {"code": 4, "contract_result": {
                "code": 1, "message": "Fail", "result": base64.b64encode(tag.encode()).decode()}}
        else:
            data = {"ok": True, "data": copy.deepcopy(self.records[cert_id])}
            result = {"contract_result": {"result": base64.b64encode(json.dumps(data).encode()).decode()}}
        self.transactions[tx_id] = {"block_height": self.writes + 8, "transaction": {
            "payload": {"chain_id": "chain1", "tx_id": tx_id, "contract_name": CONTRACT, "method": method,
                        "parameters": [{"key": key, "value": base64.b64encode(value.encode()).decode()} for key, value in params.items()]},
            "sender": {"signer": {"org_id": ROLE_ORGS[role]}},
            "result": result}}
        # Exercise fallback from unstructured text errors to persisted evidence.
        if tag:
            return "[ERROR] invoke contract failed: " + tag, 0
        return json.dumps(dict(result, tx_id=tx_id)), 0


class StageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / "instance").mkdir()
        self.report = self.project / "reports/stage05-test"
        self.report.mkdir(parents=True)
        self.stage = FakeCMCStage(self.project, self.report)
        self.quiet = redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)
        self.free = patch("deploy_and_verify.subprocess.run", return_value=subprocess.CompletedProcess([], 0))
        self.free.start()
        self.addCleanup(self.free.stop)


class WorkflowTests(StageTestCase):
    def test_entire_workflow_and_readonly_repeat(self):
        self.stage.run()
        self.assertEqual(self.stage.state["phase"], "complete")
        self.assertIsNone(self.stage.state["pending"])
        self.assertEqual(self.stage.writes, 10)  # Create, 3 success writes, 6 expected rejections.
        info = json.loads((self.project / "instance/chain-deployment.json").read_text())
        self.assertEqual(info["runtime_type"], "DOCKER_GO")
        self.assertEqual(len(self.stage.records), 2)
        before = self.stage.writes
        self.stage.run()
        self.assertEqual(self.stage.writes, before)

    def test_existing_contract_is_not_overwritten(self):
        self.stage.exists = True
        with self.assertRaises(StageError):
            self.stage.run()
        self.assertEqual(self.stage.writes, 0)
        self.assertFalse(self.stage.state_path.exists())

    def test_interruption_keeps_intent_and_blocks_repeat(self):
        self.stage.crash_first_invoke = True
        with self.assertRaises(OSError):
            self.stage.run()
        journal = json.loads(self.stage.state_path.read_text(encoding="utf-8"))
        self.assertTrue(journal["pending"]["detail"]["tx_id"])
        previous_writes = self.stage.writes
        with self.assertRaises(StageError):
            self.stage.run()
        self.assertEqual(previous_writes, self.stage.writes)

    def test_unconfirmed_query_stops_remaining_writes(self):
        self.stage.break_query = True
        with self.assertRaises(ValueError):
            self.stage.run()
        self.assertEqual(self.stage.writes, 2)
        self.assertIsNotNone(self.stage.state["pending"])
        self.assertNotEqual(self.stage.state["phase"], "complete")
        self.assertFalse((self.project / "instance/chain-deployment.json").exists())


class RecoveryTests(StageTestCase):
    def setUp(self):
        super().setUp()
        self.stage.resume_requested = True
        self.stage.exists = True
        self.report_count = 0
        ids = [KNOWN_CERT_A, KNOWN_CERT_A[:-1] + "B"]
        documents = {cert_id: demo_document(cert_id, letter)
                     for cert_id, letter in zip(ids, ("A", "B"))}
        hashes = {key: content_hash(value) for key, value in documents.items()}
        self.assertEqual(hashes[KNOWN_CERT_A], KNOWN_HASH_A)
        detail = {"role": "query", "method": "issueCertificate",
                  "params": {"cert_id": KNOWN_CERT_A, "content_hash": KNOWN_HASH_A},
                  "tx_id": KNOWN_REJECTION_TXID, "expected_rejection": "UNAUTHORIZED"}
        self.original_state = {
            "contract": CONTRACT, "runtime": RUNTIME, "version": VERSION,
            "phase": "needs_review", "created_at": "2026-09-27T08:38:46+00:00",
            "report_dir": "/home/wsc/degree-cert-system/reports/stage05-20260927-083844-CrqIWC",
            "archive_sha256": EXPECTED_ARCHIVE_SHA256, "deployment_txid": KNOWN_DEPLOYMENT_TXID,
            "cert_ids": ids, "documents": documents, "hashes": hashes,
            "steps": [{"label": "create-contract", "tx_id": KNOWN_DEPLOYMENT_TXID,
                       "expected_rejection": None, "verified_at": "2026-09-27T08:38:49+00:00"}],
            "pending": {"label": "query-cannot-issue", "detail": detail,
                        "report_dir": "/home/wsc/degree-cert-system/reports/stage05-20260927-083844-CrqIWC",
                        "time": "2026-09-27T08:38:50+00:00"},
            "last_error": "合约拒绝原因与预期不一致：Fail",
        }
        # Capture the actual receipt structure from the user report; no private
        # certificate bytes or signatures are needed for these offline tests.
        self.original_transaction = {
            "block_height": 10, "block_timestamp": 1790498330,
            "rw_set": {"tx_id": KNOWN_REJECTION_TXID, "tx_reads": []},
            "transaction": {
                "payload": {"chain_id": "chain1", "contract_name": CONTRACT,
                            "method": "issueCertificate", "timestamp": 1790498330,
                            "tx_id": KNOWN_REJECTION_TXID,
                            "parameters": [
                                {"key": "cert_id", "value": "REVNT18yMDI2MDkyNzA4Mzg0Nl9jMDNmNDU5MV9B"},
                                {"key": "content_hash", "value": "N2FkMjZjMDczMjc3ODc3NDE2NTcwYWNjMjU4NzU2ZWUxNzQwNDkyNzI5ZjM2ODQwZDMwMTVlNjdmODg5ODc1MA=="},
                            ]},
                "result": {"code": 4, "contract_result": {
                    "code": 1, "gas_used": 136, "message": "Fail", "result": "VU5BVVRIT1JJWkVE"}},
                "sender": {"signer": {"org_id": "wx-org3.chainmaker.org"}},
            },
        }
        self.stage.transactions[KNOWN_REJECTION_TXID] = copy.deepcopy(self.original_transaction)
        self.persist(self.original_state)

    def persist(self, state):
        # Deliberately use different formatting so backup verification detects
        # reserialization rather than exact byte preservation.
        raw = (json.dumps(state, ensure_ascii=False, indent=3) + "\n").encode("utf-8")
        self.stage.state_path.write_bytes(raw)
        return raw

    def fresh_report(self):
        self.report_count += 1
        self.stage.report = self.project / "reports" / ("stage05-resume-" + str(self.report_count))
        self.stage.report.mkdir()
        return self.stage.report

    def assert_stopped_without_writes(self, original_bytes):
        with self.assertRaises((StageError, ReceiptError, ValueError)):
            self.stage.run()
        self.assertEqual(self.stage.writes, 0)
        self.assertEqual(self.stage.write_labels, [])
        self.assertEqual(self.stage.state_path.read_bytes(), original_bytes)
        self.assertFalse((self.project / "instance/chain-deployment.json").exists())

    def test_exact_observed_rejection_resumes_once_and_preserves_backup(self):
        before = self.stage.state_path.read_bytes()
        self.stage.run()
        self.assertEqual(self.stage.writes, 8)
        self.assertNotIn("create-contract", self.stage.write_labels)
        self.assertNotIn("query-cannot-issue", self.stage.write_labels)
        self.assertEqual((self.report / "state-before-resume.json").read_bytes(), before)
        self.assertEqual(self.stage.state["phase"], "complete")
        self.assertIsNone(self.stage.state["pending"])
        self.assertNotIn("last_error", self.stage.state)
        first_steps = [step for step in self.stage.state["steps"]
                       if step["label"] == "query-cannot-issue"]
        self.assertEqual(len(first_steps), 1)
        self.assertEqual(first_steps[0]["tx_id"], KNOWN_REJECTION_TXID)
        self.assertEqual(len(self.stage.records), 2)
        self.assertEqual(self.stage.state["deployment_txid"], KNOWN_DEPLOYMENT_TXID)
        report = self.fresh_report()
        self.stage.run()
        self.assertEqual(self.stage.writes, 8)
        self.assertFalse((report / "state-before-resume.json").exists())

    def test_transaction_query_failure_keeps_pending_and_exact_backup(self):
        before = self.stage.state_path.read_bytes()
        self.stage.break_query = True
        self.assert_stopped_without_writes(before)
        self.assertEqual((self.report / "state-before-resume.json").read_bytes(), before)

    def test_mismatched_transaction_evidence_never_sends_new_writes(self):
        for case in ("sender", "params", "extra-param", "tag", "tx-id", "chain", "method", "success-code"):
            with self.subTest(case=case):
                self.fresh_report()
                transaction = copy.deepcopy(self.original_transaction)
                payload = transaction["transaction"]["payload"]
                if case == "sender":
                    transaction["transaction"]["sender"]["signer"]["org_id"] = ISSUER
                elif case == "params":
                    payload["parameters"][0]["value"] = "different-certificate"
                elif case == "extra-param":
                    payload["parameters"].append({"key": "unexpected", "value": "eA=="})
                elif case == "tag":
                    transaction["transaction"]["result"]["contract_result"]["result"] = base64.b64encode(
                        b"CERTIFICATE_ALREADY_EXISTS").decode()
                elif case == "tx-id":
                    payload["tx_id"] = "0" * 64
                elif case == "chain":
                    payload["chain_id"] = "other-chain"
                elif case == "method":
                    payload["method"] = "revokeCertificate"
                else:
                    transaction["transaction"]["result"]["contract_result"]["code"] = 0
                self.stage.transactions[KNOWN_REJECTION_TXID] = transaction
                before = self.persist(self.original_state)
                self.assert_stopped_without_writes(before)

    def test_modified_journals_are_not_recovered(self):
        for case in ("version", "archive", "document", "hash", "pending", "deployment", "steps", "shape"):
            with self.subTest(case=case):
                self.fresh_report()
                state = copy.deepcopy(self.original_state)
                if case == "version":
                    state["version"] = "2.0"
                elif case == "archive":
                    state["archive_sha256"] = "0" * 64
                elif case == "document":
                    state["documents"][KNOWN_CERT_A]["student_name"] = "不同同学"
                elif case == "hash":
                    state["hashes"][KNOWN_CERT_A] = "0" * 64
                elif case == "pending":
                    state["pending"]["detail"]["role"] = "uni"
                elif case == "deployment":
                    state["deployment_txid"] = "0" * 64
                elif case == "steps":
                    state["steps"].append({"label": "issuer-issues-A", "tx_id": "a" * 64})
                else:
                    state = [state]
                before = self.persist(state)
                self.assert_stopped_without_writes(before)
                self.assertFalse((self.stage.report / "state-before-resume.json").exists())

    def test_resume_without_original_state_does_not_deploy(self):
        self.stage.state_path.unlink()
        with self.assertRaises(StageError):
            self.stage.run()
        self.assertEqual(self.stage.writes, 0)
        self.assertFalse(self.stage.state_path.exists())

    def test_completed_first_rejection_checkpoint_can_continue_without_resubmission(self):
        state = copy.deepcopy(self.original_state)
        state["pending"] = None
        state["steps"].append({"label": "query-cannot-issue", "tx_id": KNOWN_REJECTION_TXID,
                               "expected_rejection": "UNAUTHORIZED", "verified_at": "2026-09-27T09:00:00+00:00"})
        before = self.persist(state)
        self.stage.run()
        self.assertEqual(self.stage.writes, 8)
        self.assertEqual(len(self.stage.state["steps"]), 10)
        self.assertNotIn("query-cannot-issue", self.stage.write_labels)
        self.assertEqual((self.report / "state-before-resume.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
