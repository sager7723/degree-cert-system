#!/usr/bin/env python3
"""Local application backup. Does not copy or modify the ChainMaker ledger."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import stat
import sys
import tarfile
import tempfile

ROOT_FILES = ("README.md", ".gitignore")
ROOT_DIRS = ("backend", "config", "contracts", "docs", "instance", "scripts")
DB_RELATIVE = "instance/degree-cert.sqlite3"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def inventory(project):
    """Explicit application scope; never follow links to other directories."""
    paths = []
    def visit(path):
        relative = path.relative_to(project).as_posix()
        if relative in {DB_RELATIVE, DB_RELATIVE + "-wal", DB_RELATIVE + "-shm", DB_RELATIVE + "-journal", "instance/cmc-work"}:
            return
        if path.name in {"__pycache__", ".git", ".venv"} or path.suffix in {".pyc", ".lock"}:
            # Dependency locks are source files, whereas instance/*.lock are runtime locks.
            if not (path.suffix == ".lock" and not relative.startswith("instance/")):
                return
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise RuntimeError("备份范围内有符号链接或特殊文件，请先核对：" + relative)
        paths.append(path)
        if stat.S_ISDIR(mode):
            for child in sorted(path.iterdir()):
                visit(child)
    for name in ROOT_FILES:
        if (project / name).exists():
            visit(project / name)
    for name in ROOT_DIRS:
        visit(project / name)
    return paths


def backup(project, output_parent):
    if Path(output_parent).is_symlink():
        raise RuntimeError("备份目录不能是符号链接。")
    project, output_parent = Path(project).resolve(), Path(output_parent).resolve()
    if output_parent == project or project in output_parent.parents:
        raise RuntimeError("备份位置须在项目目录以外。")
    database = project / DB_RELATIVE
    if not database.is_file() or database.is_symlink():
        raise RuntimeError("未找到正常的网页数据库。")
    for name in ("instance/chain-deployment.json", "instance/stage05-state.json", "instance/demo-accounts.json", "instance/web-secret.key",
                 "config/sdk-uni.yml", "config/sdk-edu.yml", "config/sdk-query.yml"):
        if not (project / name).is_file():
            raise RuntimeError("缺少应用运行文件：" + name)
    paths = inventory(project)
    source_bytes = sum(p.stat().st_size for p in paths if p.is_file()) + database.stat().st_size
    wal = Path(str(database) + "-wal")
    if wal.is_file():
        source_bytes += wal.stat().st_size
    output_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_parent.chmod(0o700)
    if shutil.disk_usage(output_parent).free < 2 * source_bytes + 64 * 1024 * 1024:
        raise RuntimeError("备份磁盘可用空间不足，保留项目后反馈。")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run = Path(tempfile.mkdtemp(prefix="application-" + stamp + "-", dir=output_parent))
    temporary_archive = run / "application.tar.gz.partial"
    archive_path = run / "application.tar.gz"
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=run) as temporary:
        snapshot = Path(temporary) / "degree-cert.sqlite3"
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)) as source:
            with closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise RuntimeError("数据库快照完整性检查未通过。")
                if destination.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise RuntimeError("数据库快照存在外键关系异常。")
                counts = {table: destination.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                          for table in ("users", "certificates", "operations", "audit_log")}
                pending = destination.execute("SELECT COUNT(*) FROM operations WHERE status='pending'").fetchone()[0]
                if pending:
                    raise RuntimeError("仍有待确认交易，请先在网页查证原交易后再制作验收备份。")
        file_hashes = {p.relative_to(project).as_posix(): digest(p) for p in paths if p.is_file()}
        file_hashes[DB_RELATIVE] = digest(snapshot)
        manifest = {"format": "degree-cert-application-backup/v1", "created_at_utc": stamp,
                    "scope": "Application only; ChainMaker ledger, node configuration and Docker images are NOT included.",
                    "excluded": [".venv", "reports", "instance/cmc-work", "SQLite journal files", "runtime locks", "Python bytecode"],
                    "contains_secrets": True, "counts": counts, "files_sha256": file_hashes}
        raw_manifest = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with tarfile.open(temporary_archive, "w:gz", dereference=False) as archive:
            for path in paths:
                relative = path.relative_to(project).as_posix()
                if path.is_symlink():
                    raise RuntimeError("备份过程中出现链接，已停止：" + relative)
                archive.add(path, arcname="degree-cert-system/" + relative, recursive=False)
            archive.add(snapshot, arcname="degree-cert-system/" + DB_RELATIVE, recursive=False)
            info = tarfile.TarInfo("BACKUP-MANIFEST.json")
            info.size, info.mode = len(raw_manifest), 0o600
            archive.addfile(info, io.BytesIO(raw_manifest))
        # Read every archived file, not just the gzip header or a file listing.
        verified, manifest_seen = set(), False
        with tarfile.open(temporary_archive, "r:gz") as archive:
            for member in archive:
                if member.isdir():
                    continue
                if not member.isfile():
                    raise RuntimeError("备份内出现非普通文件。")
                with archive.extractfile(member) as source:
                    if member.name == "BACKUP-MANIFEST.json":
                        if manifest_seen or source.read() != raw_manifest:
                            raise RuntimeError("备份清单校验失败。")
                        manifest_seen = True
                        continue
                    relative = member.name.removeprefix("degree-cert-system/")
                    if relative in verified or hashlib.file_digest(source, "sha256").hexdigest() != file_hashes.get(relative):
                        raise RuntimeError("备份内容校验失败或文件在备份时变化：" + relative)
                    verified.add(relative)
        if not manifest_seen or verified != set(file_hashes):
            raise RuntimeError("备份缺少预期文件。")
    temporary_archive.chmod(0o600)
    temporary_archive.rename(archive_path)
    checksum = digest(archive_path)
    (run / "application.tar.gz.sha256").write_text(checksum + "  application.tar.gz\n", encoding="ascii")
    return {"archive": str(archive_path), "sha256": checksum, "file_count": len(file_hashes),
            "bytes": archive_path.stat().st_size, "counts": counts}


def main():
    project = Path(__file__).resolve().parents[1]
    if sys.platform != "linux" or os.geteuid() == 0 or project != Path("/home/wsc/degree-cert-system"):
        raise SystemExit("请在 Ubuntu /home/wsc/degree-cert-system 中以普通用户运行。")
    os.umask(0o077)
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", 8080))
        except OSError:
            raise SystemExit("网页端口仍在使用。请先在网页终端按 Ctrl+C，再执行备份。")
    print("学历证书存证系统 / 第 7 步应用备份", flush=True)
    print("请保持网页停止、暂停项目文件编辑。链节点可以继续运行。", flush=True)
    print("本备份包含应用身份与账号，请仅保存在自己控制的存储中，不必发送备份包。", flush=True)
    try:
        result = backup(project, project.parent / "degree-cert-backups")
    except (OSError, RuntimeError, sqlite3.Error, tarfile.TarError) as exc:
        print("未完成：" + str(exc), file=sys.stderr)
        return 1
    print("通过：SQLite 快照完整性、外键及压缩包逐文件 SHA-256 校验。")
    print("数据库记录数量：" + json.dumps(result["counts"], ensure_ascii=False))
    print("归档文件数：", result["file_count"])
    print("备份文件：", result["archive"])
    print("SHA-256：", result["sha256"])
    print("这是应用备份，不包含链节点账本、节点管理员身份或 Docker 镜像。")
    print("完整环境还需在 Ubuntu 正常关机后备份整个虚拟机。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
