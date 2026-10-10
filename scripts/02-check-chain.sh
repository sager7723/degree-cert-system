#!/usr/bin/env bash
# Inspect the selected ChainMaker installation. Do not start services or submit transactions.
set -u
set -o pipefail
umask 077

if [[ "$(uname -s)" != Linux ]]; then
    printf '请在 Ubuntu 虚拟机中运行。\n' >&2
    exit 1
fi
if [[ $EUID -eq 0 ]]; then
    printf '请以 wsc 普通用户运行 bash scripts/02-check-chain.sh，不要在整条命令前加 sudo。\n' >&2
    exit 1
fi
for command_name in timeout python3 tee mktemp; do
    command -v "$command_name" >/dev/null 2>&1 || { printf '缺少命令：%s\n' "$command_name"; exit 1; }
done
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)" || exit 1
CHAIN_DIR="${CHAINMAKER_DIR:-/home/wsc/chainmaker-go}"
mkdir -p -- "$PROJECT_DIR/reports" || exit 1
REPORT="$(mktemp "$PROJECT_DIR/reports/chain-check-$(date +%Y%m%d-%H%M%S)-XXXXXX.txt")" || exit 1

check() {
    local label="$1" status
    shift
    printf '\n[%s]\n' "$label"
    timeout 20s "$@" 2>&1
    status=$?
    if [[ $status -eq 124 ]]; then
        printf '此项等待超时，已跳过。\n'
    elif [[ $status -ne 0 ]]; then
        printf '此项退出码：%s，继续收集。\n' "$status"
    fi
    return 0
}

collect() {
    printf '学历证书存证系统 / 第 2 步链部署检查\n'
    printf '时间：%s\n目标目录：%s\n' "$(date --iso-8601=seconds)" "$CHAIN_DIR"
    printf '本次仅提取配置字段和运行状态；未启动节点或发送交易。\n'

    printf '\n========== 1. 项目 Python 环境 ==========\n'
    if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        check '项目 Python' "$PROJECT_DIR/.venv/bin/python" --version
        check '项目 pip' "$PROJECT_DIR/.venv/bin/python" -m pip --version
    else
        printf '项目 .venv 尚未建立；不影响下列链检查。\n'
    fi
    if [[ -x /usr/local/go/bin/go ]]; then
        check '/usr/local/go 是否另有工具链（不下载）' env GOTOOLCHAIN=local /usr/local/go/bin/go version
    fi

    printf '\n========== 2. 部署配置与路径 ==========\n'
    check '发布配置的白名单字段' python3 "$SCRIPT_DIR/inspect_chain_layout.py" --root "$CHAIN_DIR" --fallback-root /home/wsc/chainmaker/chainmaker-go

    printf '\n========== 3. Docker 服务和已有容器 ==========\n'
    printf '以下 sudo -n 仅用于 Docker 只读命令；没有 sudo 凭据时立即报错，不会等待密码。\n'
    check '系统 cgroup 文件系统' stat -fc %T /sys/fs/cgroup/
    check 'Docker 服务' sudo -n docker info --format 'Server={{.ServerVersion}}; Architecture={{.Architecture}}; Memory={{.MemTotal}}; CgroupDriver={{.CgroupDriver}}; CgroupVersion={{.CgroupVersion}}'
    check '容器/状态/端口' sudo -n docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
    check '本地镜像（不拉取）' sudo -n docker image ls --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'

    printf '\n========== 4. CMC 支持的只读查询命令 ==========\n'
    if [[ -x "$CHAIN_DIR/tools/cmc/cmc" ]]; then
        (
            cd -- "$CHAIN_DIR/tools/cmc" || exit 1
            check 'CMC query 帮助（尚未连接链）' ./cmc query --help
            check '最高区块查询参数' ./cmc query block-by-height --help
        )
    else
        printf '目标目录没有可执行 CMC。\n'
    fi
    printf '\n========== 5. 监听端口 ==========\n'
    check 'TCP 端口' ss -lnt
    printf '\n========== 完成 ==========\n'
    printf '请反馈此报告，用于选定匹配的节点启动方式和链查询配置。\n'
}

collect 2>&1 | tee "$REPORT"
result_codes=("${PIPESTATUS[@]}")
if [[ ${result_codes[1]} -ne 0 ]]; then
    printf '报告写入失败，请反馈终端输出。\n' >&2
    exit 1
fi
printf '\n报告位置：%s\n' "$REPORT"
