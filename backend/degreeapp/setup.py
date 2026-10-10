"""Idempotent local setup; imports only chain-confirmed stage-05 demo records."""
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
from werkzeug.security import generate_password_hash

from . import db
from .domain import canonical_document, canonical_json, content_hash

ACCOUNTS = (("uni_admin", "uni", "高校签发员"), ("edu_admin", "edu", "教育主管部门"), ("query_user", "query", "查询机构"))


def exclusive_json(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def prepare_accounts(project, database):
    instance = project / "instance"
    instance.mkdir(mode=0o700, parents=True, exist_ok=True)
    instance.chmod(0o700)
    secret_path = instance / "web-secret.key"
    if secret_path.is_symlink():
        raise RuntimeError("会话密钥路径不能为符号链接。")
    if not secret_path.exists():
        fd = os.open(secret_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(32))
    secret_path.chmod(0o600)
    path = instance / "demo-accounts.json"
    if path.is_symlink():
        raise RuntimeError("账号文件路径不能为符号链接。")
    if not path.exists():
        with closing(db.connect(database)) as connection:
            if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise RuntimeError("数据库已有账号但原账号文件缺失。不会重置密码，请保留数据库并反馈。")
        exclusive_json(path, {"created_at": db.now(), "accounts": [
            {"username": name, "role": role, "display_name": label, "password": secrets.token_urlsafe(12)}
            for name, role, label in ACCOUNTS]})
    path.chmod(0o600)
    entries = json.loads(path.read_text(encoding="utf-8")).get("accounts", [])
    if len(entries) != 3 or any(not isinstance(item, dict) for item in entries):
        raise RuntimeError("演示账号文件格式不正确，未修改现有账号。")
    with db.transaction(database) as connection:
        for name, role, label in ACCOUNTS:
            matches = [item for item in entries if item.get("username") == name and item.get("role") == role]
            if len(matches) != 1 or not isinstance(matches[0].get("password"), str) or len(matches[0]["password"]) < 12:
                raise RuntimeError("演示账号文件不完整，未重置任何密码。")
            existing = connection.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
            if existing:
                if existing["role"] != role:
                    raise RuntimeError("已有账号角色与本项目角色不一致。")
                continue
            connection.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,?,?)",
                               (name, generate_password_hash(matches[0]["password"], method="pbkdf2:sha256:600000"), role, label))
    return path


def initialize(project, chain):
    chain.health()  # Ensure the existing contract is reachable before local setup.
    state_path = project / "instance/stage05-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("phase") != "complete" or state.get("pending") is not None or state.get("contract") != "degree_cert_v1":
        raise RuntimeError("第五步尚未全部通过，保留原状态并停止网页初始化。")
    ids = state.get("cert_ids", [])
    documents, hashes = state.get("documents", {}), state.get("hashes", {})
    if not isinstance(ids, list) or len(ids) != 2 or len(set(ids)) != 2 or set(ids) != set(documents) or set(ids) != set(hashes):
        raise RuntimeError("第五步演示证书记录不完整。")
    imports = []
    for cert_id in ids:
        document = canonical_document(documents[cert_id])
        hashed = content_hash(document)
        ledger = chain.get_certificate(cert_id)
        if document["cert_id"] != cert_id or hashed != hashes[cert_id] or ledger["content_hash"] != hashed:
            raise RuntimeError("演示证书全文与链上哈希不一致，停止导入：" + cert_id)
        imports.append((document, hashed, ledger))
    database = project / "instance/degree-cert.sqlite3"
    db.initialize(database)
    credentials = prepare_accounts(project, database)
    imported = 0
    with db.transaction(database) as connection:
        user_id = connection.execute("SELECT id FROM users WHERE username='uni_admin'").fetchone()[0]
        for document, hashed, ledger in imports:
            cert_id = document["cert_id"]
            existing = connection.execute("SELECT document_json,content_hash FROM certificates WHERE cert_id=?", (cert_id,)).fetchone()
            if existing:
                if existing["document_json"] != canonical_json(document) or existing["content_hash"] != hashed:
                    raise RuntimeError("本地已有不同的证书全文，保留数据并停止：" + cert_id)
                continue
            connection.execute("INSERT INTO certificates(cert_id,student_id,student_name,document_json,content_hash,local_status,ledger_json,issued_by,created_at,confirmed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (cert_id, document["student_id"], document["student_name"], canonical_json(document), hashed,
                                ledger["status"], canonical_json(ledger), user_id, db.now(), db.now()))
            db.audit(connection, None, "导入已验证演示证书", cert_id, "已读取真实链上记录并比对全文哈希；未发送新交易。")
            imported += 1
    return {"database": str(database), "credentials": str(credentials), "imported": imported}
