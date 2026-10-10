"""Verify the archive contains committed WAL data and recoverable source files."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_application import backup, digest, ROOT_DIRS

# Loading this standard-library-only module avoids importing Flask in these tests.
spec = importlib.util.spec_from_file_location("backup_test_db", Path(__file__).resolve().parents[1] / "backend/degreeapp/db.py")
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.output = Path(self.temp.name) / "backups"
        for directory in ROOT_DIRS:
            (self.project / directory).mkdir(parents=True)
        for name in ("instance/chain-deployment.json", "instance/stage05-state.json", "instance/demo-accounts.json", "instance/web-secret.key",
                     "config/sdk-uni.yml", "config/sdk-edu.yml", "config/sdk-query.yml", "backend/requirements-linux.lock"):
            (self.project / name).write_text("test-only placeholder", encoding="utf-8")
        (self.project / "instance/stage07.lock").write_text("runtime lock")
        (self.project / ".venv").mkdir()
        (self.project / ".venv/excluded.txt").write_text("not portable")
        self.database = self.project / "instance/degree-cert.sqlite3"
        db.initialize(self.database)
        self.writer = db.connect(self.database)
        self.addCleanup(self.writer.close)
        self.writer.execute("PRAGMA wal_autocheckpoint=0")
        self.writer.execute("INSERT INTO users(id,username,password_hash,display_name,role) VALUES(1,'demo','test-only','演示账号','uni')")
        self.writer.execute("INSERT INTO certificates(cert_id,student_id,student_name,document_json,content_hash,local_status,created_at) VALUES('CERT_A','S1','演示学生','{}',?,'valid','2026-09-28')", ("f" * 64,))
        self.writer.commit()

    def test_archive_restores_committed_wal_data_and_pinned_dependencies(self):
        # Keep the writer open so committed rows still reside in the WAL.
        self.assertGreater(Path(str(self.database) + "-wal").stat().st_size, 0)
        before = self.database.read_bytes()
        result = backup(self.project, self.output)
        self.assertEqual(self.database.read_bytes(), before)
        archive_path = Path(result["archive"])
        self.assertEqual(digest(archive_path), result["sha256"])
        self.assertEqual(result["counts"]["certificates"], 1)
        with tarfile.open(archive_path, "r:gz") as archive:
            names = archive.getnames()
            self.assertIn("degree-cert-system/backend/requirements-linux.lock", names)
            self.assertIn("degree-cert-system/instance/web-secret.key", names)
            self.assertFalse(any(name.endswith(("-wal", "-shm", "stage07.lock")) or "/.venv/" in name for name in names))
            # Read one known member into a new location; never extract arbitrary paths.
            restored = self.output / "restored.sqlite3"
            restored.write_bytes(archive.extractfile("degree-cert-system/instance/degree-cert.sqlite3").read())
            manifest = json.load(archive.extractfile("BACKUP-MANIFEST.json"))
            self.assertTrue(manifest["contains_secrets"])
        with closing(sqlite3.connect(restored)) as connection:
            self.assertEqual(connection.execute("SELECT cert_id,student_name FROM certificates").fetchall(), [("CERT_A", "演示学生")])
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])

    def test_pending_transaction_prevents_completed_backup(self):
        self.writer.execute("INSERT INTO operations(request_id,kind,actor_id,role,cert_id,params_json,tx_id,status,created_at) VALUES('req','issue',1,'uni','CERT_A','{}',?,'pending','2026-09-28')", ("e" * 64,))
        self.writer.commit()
        with self.assertRaisesRegex(RuntimeError, "待确认"):
            backup(self.project, self.output)
        self.assertFalse(list(self.output.rglob("application.tar.gz")))

    def test_repeated_backups_keep_both_archives_and_reject_nested_destination(self):
        first = backup(self.project, self.output)
        second = backup(self.project, self.output)
        self.assertNotEqual(first["archive"], second["archive"])
        self.assertTrue(Path(first["archive"]).is_file())
        self.assertTrue(Path(second["archive"]).is_file())
        with self.assertRaisesRegex(RuntimeError, "项目目录以外"):
            backup(self.project, self.project / "backups")


if __name__ == "__main__":
    unittest.main()
