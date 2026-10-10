#!/usr/bin/env bash
set -u
set -o pipefail
umask 077
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 1
if [[ "$(uname -s)" != Linux || $EUID -eq 0 || "$(id -un)" != wsc || "$PROJECT_DIR" != /home/wsc/degree-cert-system ]]; then
    printf '请在 Ubuntu /home/wsc/degree-cert-system 中以 wsc 执行，不要 sudo bash。\n' >&2
    exit 1
fi
for command in flock mktemp tee python3; do
    command -v "$command" >/dev/null || exit 1
done
mkdir -p "$PROJECT_DIR/reports" || exit 1
exec 9>"$PROJECT_DIR/instance/stage07.lock" || exit 1
flock -n 9 || { printf '另一个备份正在运行。\n'; exit 1; }
RUN_DIR="$(mktemp -d "$PROJECT_DIR/reports/stage07-$(date +%Y%m%d-%H%M%S)-XXXXXX")" || exit 1
python3 -B "$PROJECT_DIR/scripts/backup_application.py" 2>&1 | tee "$RUN_DIR/report.txt"
codes=("${PIPESTATUS[@]}")
printf '\n结果码：%s\n报告：%s/report.txt\n' "${codes[0]}" "$RUN_DIR"
[[ ${codes[1]} -eq 0 ]] || exit 1
exit "${codes[0]}"
