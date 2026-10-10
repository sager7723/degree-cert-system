#!/usr/bin/env bash
# Install the offline web dependencies and initialize the local application.
set -u
set -o pipefail
umask 077

if [[ "$(uname -s)" != Linux || $EUID -eq 0 || "$(id -un)" != wsc ]]; then
    printf '请在 Ubuntu 中以 wsc 普通用户执行；不要 sudo bash 本脚本。\n' >&2
    exit 1
fi
for program in flock tee mktemp; do
    command -v "$program" >/dev/null 2>&1 || { printf '缺少命令：%s\n' "$program" >&2; exit 1; }
done
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)" || exit 1
if [[ "$PROJECT_DIR" != /home/wsc/degree-cert-system ]]; then
    printf '请把文件放在 /home/wsc/degree-cert-system 后执行。当前：%s\n' "$PROJECT_DIR" >&2
    exit 1
fi
mkdir -p -- "$PROJECT_DIR/reports" "$PROJECT_DIR/instance" || exit 1
exec 9>"$PROJECT_DIR/instance/stage06.lock" || exit 1
flock -n 9 || { printf '另一个第六步安装脚本正在运行，请等待它结束。\n' >&2; exit 1; }
RUN_DIR="$(mktemp -d "$PROJECT_DIR/reports/stage06-$(date +%Y%m%d-%H%M%S)-XXXXXX")" || exit 1
PYTHON="$PROJECT_DIR/.venv/bin/python"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

install_web() {
    set -e
    printf '学历证书存证系统 / 第 6 步网页安装与链连接检查\n时间：%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '项目目录：%s\n依赖安装范围：本项目已有的 .venv 虚拟环境。\n' "$PROJECT_DIR"
    printf '本脚本不部署合约，不发送签发或撤销交易。\n\n'
    if [[ ! -x "$PYTHON" || ! -x "$PROJECT_DIR/.venv/bin/python3.12" ]]; then
        printf '缺少已配置的 Python 3.12 虚拟环境，请保留本报告并反馈。\n' >&2
        exit 1
    fi
    "$PYTHON" -B - "$PROJECT_DIR" <<'PY'
from pathlib import Path
import platform
import sys
project = Path(sys.argv[1])
if sys.version_info[:2] != (3, 12) or sys.prefix == sys.base_prefix:
    raise SystemExit("需要本项目的 Python 3.12 虚拟环境。")
if Path(sys.prefix).resolve() != (project / ".venv").resolve():
    raise SystemExit("Python 虚拟环境路径与当前项目不一致。")
if platform.machine() != "x86_64":
    raise SystemExit("本离线依赖包适用于 Ubuntu x86_64。")
for relative in ("backend/app.py", "backend/requirements-linux.lock", "instance/chain-deployment.json"):
    if not (project / relative).is_file():
        raise SystemExit("缺少所需文件：" + relative)
if not (project / "backend/wheels").is_dir():
    raise SystemExit("离线依赖文件夹 backend/wheels 不存在，请重新解压第六步压缩包。")
print("Python 虚拟环境确认：" + sys.version.split()[0])
PY
    printf '\n正在校验并安装离线依赖……\n'
    "$PYTHON" -B -m pip --isolated --disable-pip-version-check install \
        --no-index --find-links "$PROJECT_DIR/backend/wheels" \
        --require-hashes --only-binary=:all: -r "$PROJECT_DIR/backend/requirements-linux.lock"
    printf '\n正在初始化网页账号与 SQLite 数据库……\n'
    "$PYTHON" -B "$PROJECT_DIR/backend/app.py" init
    printf '\n正在读取真实链状态……\n'
    "$PYTHON" -B "$PROJECT_DIR/backend/app.py" doctor
    printf '\n第六步完成。账号文件：%s/instance/demo-accounts.json\n' "$PROJECT_DIR"
    printf '接下来执行：bash scripts/07-start-web.sh\n'
}

install_web 2>&1 | tee "$RUN_DIR/report.txt"
statuses=("${PIPESTATUS[@]}")
printf '\n结果码：%s\n报告：%s/report.txt\n' "${statuses[0]}" "$RUN_DIR"
if [[ ${statuses[1]} -ne 0 ]]; then exit 1; fi
exit "${statuses[0]}"
