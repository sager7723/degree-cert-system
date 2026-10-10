#!/usr/bin/env bash
# Deploy once, then exercise the actual ledger with fictitious certificates.
set -u
set -o pipefail
umask 077

if [[ "$(uname -s)" != Linux || $EUID -eq 0 ]]; then
    printf '请在 Ubuntu 中以 wsc 普通用户执行；不要 sudo bash 本脚本。\n' >&2
    exit 1
fi
for program in python3 sudo flock tee; do
    command -v "$program" >/dev/null 2>&1 || { printf '缺少命令：%s\n' "$program"; exit 1; }
done
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)" || exit 1
if [[ "$PROJECT_DIR" != /home/wsc/degree-cert-system ]]; then
    printf '请把文件放在 /home/wsc/degree-cert-system 后执行。当前：%s\n' "$PROJECT_DIR"
    exit 1
fi
mkdir -p -- "$PROJECT_DIR/reports" "$PROJECT_DIR/instance" || exit 1
exec 9>"$PROJECT_DIR/instance/stage05.lock" || exit 1
flock -n 9 || { printf '另一个第五步脚本正在运行，请等待它结束。\n'; exit 1; }
RUN_DIR="$(mktemp -d "$PROJECT_DIR/reports/stage05-$(date +%Y%m%d-%H%M%S)-XXXXXX")" || exit 1
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
python3 -B "$SCRIPT_DIR/deploy_and_verify.py" --project "$PROJECT_DIR" --report-dir "$RUN_DIR" "$@" 2>&1 | tee "$RUN_DIR/report.txt"
statuses=("${PIPESTATUS[@]}")
printf '\n结果码：%s\n报告：%s/report.txt\n' "${statuses[0]}" "$RUN_DIR"
if [[ ${statuses[1]} -ne 0 ]]; then exit 1; fi
exit "${statuses[0]}"
