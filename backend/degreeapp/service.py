"""Application consistency: durable intent -> one submission -> verified receipt.

Uncertain transactions remain pending. Reconciliation only queries the saved
transaction ID, never repeats a submission or pretends local state is the chain.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
import unicodedata
import uuid

from . import db
from .chain import ChainError
from .domain import canonical_document, canonical_json, content_hash, revocation_reason


class ApplicationError(ValueError):
    pass


class Registry:
    def __init__(self, database, chain):
        self.database, self.chain = database, chain

    def get(self, cert_id):
        with closing(db.connect(self.database)) as connection:
            return db.certificate(connection.execute("SELECT * FROM certificates WHERE cert_id=?", (cert_id,)).fetchone())

    def search(self, search="", can_browse=True):
        # Existing internal callers (dashboard, tests) need a compact recent list.
        return self.search_page(search, can_browse, field="all" if can_browse else "cert_id",
                                order="desc", per_page=200)["items"]

    def search_page(self, search="", can_browse=True, *, field="cert_id", order="asc", page=1, per_page=20):
        search = unicodedata.normalize("NFC", search).strip()
        fields = {"cert_id": "cert_id", "student_name": "student_name",
                  "student_id": "student_id", "school_name": "school_name_search"}
        if field not in fields and not (can_browse and field == "all"):
            raise ApplicationError("请选择证书编号、姓名、学校或学号查询。")
        if order not in ("asc", "desc") or not isinstance(page, int) or page < 1:
            raise ApplicationError("排序方式或页码无效。")
        if not 1 <= per_page <= 200:
            raise ApplicationError("每页记录数无效。")
        if len(search) > 100:
            raise ApplicationError("查询内容不能超过 100 个字符。")
        params = []
        if not search:
            where = "1=1" if can_browse else "1=0"
        elif field == "all":
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where = " OR ".join(column + " LIKE ? ESCAPE '\\'" for column in fields.values())
            params = ["%" + escaped + "%"] * len(fields)
        else:
            # Exact matches keep same-name students and all school certificates;
            # never group by student name or silently select just one row.
            where = fields[field] + " = ?"
            params = [search]
        indexed = """WITH documents AS (
            SELECT *,
              CASE WHEN json_valid(document_json) THEN json_extract(document_json,'$.school_name') END AS school_name_search,
              CASE WHEN json_valid(ledger_json) THEN json_extract(ledger_json,'$.issued_at') END AS issued_value
            FROM certificates
        ), indexed AS (
            SELECT *, CASE WHEN local_status IN ('valid','revoked')
              AND typeof(issued_value) IN ('text','integer')
              AND length(CAST(issued_value AS TEXT)) BETWEEN 1 AND 20
              AND CAST(issued_value AS TEXT) NOT GLOB '*[^0-9]*'
              AND CAST(issued_value AS INTEGER)>0
              THEN CAST(issued_value AS INTEGER) END AS issued_sort FROM documents
        ) """
        with closing(db.connect(self.database)) as connection:
            connection.execute("BEGIN")  # Count and page use one consistent read snapshot.
            total = connection.execute(indexed + "SELECT COUNT(*) FROM indexed WHERE " + where, params).fetchone()[0]
            pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, pages)
            rows = connection.execute(indexed + "SELECT * FROM indexed WHERE " + where
                + " ORDER BY issued_sort IS NULL, issued_sort " + order.upper()
                + ", cert_id ASC LIMIT ? OFFSET ?", params + [per_page, (page - 1) * per_page]).fetchall()
        items = [db.certificate(row) for row in rows]
        for item in items:
            timestamp = item.get("issued_sort")
            try:
                item["issued_time"] = datetime.fromtimestamp(timestamp, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S") if timestamp else "尚无已确认签发时间"
            except (ValueError, OverflowError, OSError):
                item["issued_time"] = "签发时间待核对"
        return {"items": items, "total": total, "page": page, "pages": pages, "per_page": per_page,
                "start": (page - 1) * per_page + 1 if total else 0,
                "end": min(page * per_page, total), "has_prev": page > 1, "has_next": page < pages}

    def pending(self, cert_id):
        with closing(db.connect(self.database)) as connection:
            row = connection.execute("SELECT * FROM operations WHERE cert_id=? AND status='pending'", (cert_id,)).fetchone()
            return dict(row) if row else None

    def _existing(self, connection, request_id, actor, kind, params):
        if not re.fullmatch(r"[0-9a-f]{32}", request_id or ""):
            raise ApplicationError("表单提交标识无效，请重新打开操作页面。")
        row = connection.execute("SELECT * FROM operations WHERE request_id=?", (request_id,)).fetchone()
        if row:
            if row["actor_id"] != actor["id"] or row["kind"] != kind or row["params_json"] != canonical_json(params):
                raise ApplicationError("这个表单标识已经用于其他内容，请重新打开页面。")
            return dict(row)
        return None

    def issue(self, actor, values, request_id):
        if actor["role"] != "uni":
            raise ApplicationError("仅高校签发账号可以签发证书。")
        if not str(values.get("cert_id", "")).strip():
            if not re.fullmatch(r"[0-9a-f]{32}", request_id or ""):
                raise ApplicationError("表单提交标识无效，请重新打开操作页面。")
            # The same form always generates the same ID, including a retry
            # after a lost HTTP response. It cannot create a second certificate.
            values = dict(values, cert_id="DEG_" + request_id)
        document = canonical_document(values)
        cert_id = document["cert_id"]
        hashed = content_hash(document)
        params = {"cert_id": cert_id, "content_hash": hashed}
        try:
            with db.transaction(self.database) as connection:
                existing = self._existing(connection, request_id, actor, "issue", params)
                if existing:
                    return existing
                if connection.execute("SELECT 1 FROM certificates WHERE cert_id=?", (cert_id,)).fetchone():
                    raise ApplicationError("此证书编号已登记。请查看现有记录，或使用新的证书编号；待确认交易请先查证。")
                connection.execute("INSERT INTO certificates(cert_id,student_id,student_name,document_json,content_hash,local_status,issued_by,created_at) VALUES(?,?,?,?,?,'pending',?,?)",
                                   (cert_id, document["student_id"], document["student_name"], canonical_json(document), hashed, actor["id"], db.now()))
                operation = self._insert_operation(connection, actor, "issue", params, request_id)
        except sqlite3.IntegrityError as exc:
            raise ApplicationError("该证书已有操作正在处理，请先查看操作记录。") from exc
        return self._resolve(operation, submit=True)

    def revoke(self, actor, cert_id, reason, request_id):
        if actor["role"] != "edu":
            raise ApplicationError("仅教育主管部门账号可以撤销证书。")
        params = {"cert_id": cert_id, "reason": revocation_reason(reason)}
        with db.transaction(self.database) as connection:
            existing = self._existing(connection, request_id, actor, "revoke", params)
            if existing:
                return existing
            row = connection.execute("SELECT * FROM certificates WHERE cert_id=?", (cert_id,)).fetchone()
            if not row:
                raise ApplicationError("本地未登记该证书，不能发起撤销。")
            if row["local_status"] != "valid":
                raise ApplicationError("此证书的本地状态不允许撤销，请核对链上状态和待确认操作。")
            if connection.execute("SELECT 1 FROM operations WHERE cert_id=? AND status='pending'", (cert_id,)).fetchone():
                raise ApplicationError("该证书已有待确认操作，请先查证原交易。")
            operation = self._insert_operation(connection, actor, "revoke", params, request_id)
        return self._resolve(operation, submit=True)

    def _insert_operation(self, connection, actor, kind, params, request_id):
        tx_id = uuid.uuid4().hex + uuid.uuid4().hex
        cursor = connection.execute("INSERT INTO operations(request_id,kind,actor_id,role,cert_id,params_json,tx_id,status,created_at) VALUES(?,?,?,?,?,?,?,'pending',?)",
                                    (request_id, kind, actor["id"], actor["role"], params["cert_id"], canonical_json(params), tx_id, db.now()))
        db.audit(connection, actor["id"], "发起签发" if kind == "issue" else "发起撤销", params["cert_id"], "交易已登记待确认：" + tx_id)
        return dict(connection.execute("SELECT * FROM operations WHERE id=?", (cursor.lastrowid,)).fetchone())

    def reconcile(self, actor, op_id):
        with closing(db.connect(self.database)) as connection:
            row = connection.execute("SELECT * FROM operations WHERE id=? AND actor_id=?", (op_id, actor["id"])).fetchone()
        if not row:
            raise ApplicationError("找不到你有权查证的操作。")
        operation = dict(row)
        if operation["status"] != "pending":
            return operation
        return self._resolve(operation, submit=False)

    def _resolve(self, operation, *, submit):
        method = "issueCertificate" if operation["kind"] == "issue" else "revokeCertificate"
        params = json.loads(operation["params_json"])
        try:
            action = self.chain.submit if submit else self.chain.transaction
            data = action(operation["role"], method, params, operation["tx_id"])
            cert = self.get(operation["cert_id"])
            expected_state = "valid" if operation["kind"] == "issue" else "revoked"
            tx_field = "issued_txid" if operation["kind"] == "issue" else "revoked_txid"
            if (data.get("cert_id") != operation["cert_id"] or data.get("content_hash") != cert["content_hash"]
                    or data.get("status") != expected_state or data.get(tx_field) != operation["tx_id"]):
                raise ChainError("mismatch", "交易回执与本地登记内容不一致，请保留记录进一步核对。", uncertain=True, tx_id=operation["tx_id"])
        except ChainError as exc:
            rejected = exc.code == "rejected" and not exc.uncertain
            status = "failed" if rejected else "pending"
            with db.transaction(self.database) as connection:
                # A concurrent read-only reconciliation may have already completed.
                cursor = connection.execute("UPDATE operations SET status=?,error=? WHERE id=? AND status='pending'",
                                            (status, str(exc), operation["id"]))
                if cursor.rowcount:
                    if rejected and operation["kind"] == "issue":
                        connection.execute("UPDATE certificates SET local_status='failed' WHERE cert_id=? AND local_status='pending'", (operation["cert_id"],))
                    db.audit(connection, operation["actor_id"], "交易被拒绝" if rejected else "交易待确认", operation["cert_id"], str(exc))
        else:
            with db.transaction(self.database) as connection:
                cursor = connection.execute("UPDATE operations SET status='confirmed',error='',response_json=?,confirmed_at=? WHERE id=? AND status='pending'",
                                            (canonical_json(data), db.now(), operation["id"]))
                if cursor.rowcount:
                    connection.execute("UPDATE certificates SET local_status=?,ledger_json=?,confirmed_at=? WHERE cert_id=?",
                                       (data["status"], canonical_json(data), db.now(), operation["cert_id"]))
                    db.audit(connection, operation["actor_id"], "签发已确认" if operation["kind"] == "issue" else "撤销已确认",
                             operation["cert_id"], "链上交易：" + operation["tx_id"])
        with closing(db.connect(self.database)) as connection:
            return dict(connection.execute("SELECT * FROM operations WHERE id=?", (operation["id"],)).fetchone())

    def operations(self, actor_id):
        with closing(db.connect(self.database)) as connection:
            return [dict(row) for row in connection.execute("SELECT operations.*,users.username FROM operations JOIN users ON actor_id=users.id WHERE actor_id=? ORDER BY id DESC LIMIT 200", (actor_id,))]

    def audit_entries(self):
        with closing(db.connect(self.database)) as connection:
            return [dict(row) for row in connection.execute("SELECT audit_log.*,COALESCE(users.username,'系统') AS username FROM audit_log LEFT JOIN users ON actor_id=users.id ORDER BY audit_log.id DESC LIMIT 200")]

    def stats(self):
        with closing(db.connect(self.database)) as connection:
            result = {"total": 0, "valid": 0, "revoked": 0, "pending": 0, "failed": 0}
            for row in connection.execute("SELECT local_status,COUNT(*) AS n FROM certificates GROUP BY local_status"):
                result[row["local_status"]] = row["n"]
                result["total"] += row["n"]
            result["pending"] = connection.execute("SELECT COUNT(*) FROM operations WHERE status='pending'").fetchone()[0]
            return result
