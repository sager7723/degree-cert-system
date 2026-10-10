#!/usr/bin/env bash
# Run the web application in this terminal, bound only to Ubuntu loopback.
set -euo pipefail
umask 077

if [[ "$(uname -s)" != Linux || $EUID -eq 0 || "$(id -un)" != wsc ]]; then
    printf '请在 Ubuntu 中以 wsc 普通用户执行；不要 sudo bash 本脚本。\n' >&2
    exit 1
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
if [[ "$PROJECT_DIR" != /home/wsc/degree-cert-system ]]; then
    printf '请把文件放在 /home/wsc/degree-cert-system 后执行。当前：%s\n' "$PROJECT_DIR" >&2
    exit 1
fi
PYTHON="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" || ! -f "$PROJECT_DIR/backend/app.py" ]]; then
    printf '缺少网页程序或 Python 虚拟环境，请先执行第六步安装脚本。\n' >&2
    exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
"$PYTHON" -B - "$PROJECT_DIR" <<'PY'
from pathlib import Path
import socket
import sys
project = Path(sys.argv[1])
if (sys.version_info[:2] != (3, 12) or sys.prefix == sys.base_prefix
        or Path(sys.prefix).resolve() != (project / ".venv").resolve()):
    raise SystemExit("请使用本项目已有的 Python 3.12 虚拟环境。")
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", 8080))
    except OSError:
        raise SystemExit("127.0.0.1:8080 已被占用或无法绑定。请先检查已有网页终端；本脚本不会结束其他进程。")
PY
printf '学历证书存证系统网页正在启动。\n'
printf '请在 Ubuntu 浏览器打开：http://127.0.0.1:8080\n'
printf '账号文件：%s/instance/demo-accounts.json\n' "$PROJECT_DIR"
printf '请保留此终端。按 Ctrl+C 只停止本次网页服务。\n\n'
cd -- "$PROJECT_DIR"
exec "$PYTHON" -B "$PROJECT_DIR/backend/app.py" serve --host 127.0.0.1 --port 8080
