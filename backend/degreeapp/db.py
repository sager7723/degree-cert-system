"""SQLite persistence; chain calls must never run inside a DB write transaction."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
 display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('uni','edu','query')));
CREATE TABLE IF NOT EXISTS certificates (
 cert_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, student_name TEXT NOT NULL,
 document_json TEXT NOT NULL, content_hash TEXT NOT NULL,
 local_status TEXT NOT NULL CHECK(local_status IN ('pending','valid','revoked','failed')),
 ledger_json TEXT, issued_by INTEGER REFERENCES users(id),
 created_at TEXT NOT NULL, confirmed_at TEXT);
CREATE TABLE IF NOT EXISTS operations (
 id INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('issue','revoke')),
 actor_id INTEGER NOT NULL REFERENCES users(id), role TEXT NOT NULL, cert_id TEXT NOT NULL REFERENCES certificates(cert_id),
 params_json TEXT NOT NULL, tx_id TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL CHECK(status IN ('pending','confirmed','failed')),
 error TEXT NOT NULL DEFAULT '', response_json TEXT, created_at TEXT NOT NULL, confirmed_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_operation_per_certificate ON operations(cert_id) WHERE status='pending';
CREATE INDEX IF NOT EXISTS operations_actor ON operations(actor_id,created_at);
CREATE INDEX IF NOT EXISTS certificates_student ON certificates(student_id);
CREATE TABLE IF NOT EXISTS audit_log (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, actor_id INTEGER REFERENCES users(id),
 action TEXT NOT NULL, cert_id TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL);
PRAGMA user_version=1;
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path):
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError("数据库版本与本程序不兼容，保留原数据库并停止。")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA_SQL)
        connection.commit()
    path.chmod(0o600)


@contextmanager
def transaction(path):
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def audit(connection, actor_id, action, cert_id="", detail=""):
    connection.execute("INSERT INTO audit_log(created_at,actor_id,action,cert_id,detail) VALUES(?,?,?,?,?)",
                       (now(), actor_id, action, cert_id, detail))


def certificate(row):
    if row is None:
        return None
    result = dict(row)
    try:
        document = json.loads(result["document_json"])
        if not isinstance(document, dict):
            raise ValueError()
    except (ValueError, TypeError):
        document = {}
    result["document"] = document
    result.update({key: document.get(key, "") for key in ("student_id", "student_name", "school_name", "major", "degree", "graduation_date")})
    return result
