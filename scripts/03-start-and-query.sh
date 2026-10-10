#!/usr/bin/env bash
# Start the four existing release nodes once, then query a real block.
# Run as wsc after sudo -v. Reports remain owned by the ordinary user.
set -u
set -o pipefail
umask 077

if [[ "$(uname -s)" != Linux || $EUID -eq 0 ]]; then
    printf '请在 Ubuntu 中以 wsc 执行本脚本，不要在整条 bash 命令前加 sudo。\n' >&2
    exit 1
fi
for program in sudo timeout python3 ss pgrep tee mktemp sha256sum; do
    command -v "$program" >/dev/null 2>&1 || { printf '缺少命令：%s\n' "$program"; exit 1; }
done
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)" || exit 1
CHAIN_DIR=/home/wsc/chainmaker-go
CMC_DIR="$CHAIN_DIR/tools/cmc"
SDK_CONFIG="$PROJECT_DIR/config/sdk-stage03.yml"
SUMMARY_SCRIPT="$SCRIPT_DIR/summarize_chain_probe.py"
[[ -r "$SDK_CONFIG" && -r "$SUMMARY_SCRIPT" ]] || { printf '缺少 sdk-stage03.yml 或 summarize_chain_probe.py，请先复制本步全部文件。\n'; exit 1; }
if ! sudo -n true 2>/dev/null; then
    printf '请先在当前终端执行 sudo -v，再运行本脚本。\n'
    exit 1
fi
mkdir -p -- "$PROJECT_DIR/reports" || exit 1
RUN_DIR="$(mktemp -d "$PROJECT_DIR/reports/stage03-$(date +%Y%m%d-%H%M%S)-XXXXXX")" || exit 1
REPORT="$RUN_DIR/report.txt"
NODE_DIRS=()
for number in 1 2 3 4; do
    NODE_DIRS+=("$CHAIN_DIR/build/release/chainmaker-v2.4.0_alpha-wx-org${number}.chainmaker.org")
done

section() { printf '\n========== %s ==========\n' "$1"; }
check() {
    local title="$1" status
    shift
    printf '\n[%s]\n' "$title"
    timeout 20s "$@" 2>&1
    status=$?
    [[ $status -eq 0 ]] || printf '此项退出码：%s\n' "$status"
    return 0
}
port_count() {
    ss -Hlnt | awk '$4 ~ /:1230[1-4]$/ { p=$4; sub(/^.*:/,"",p); seen[p]=1 } END { print length(seen) }'
}
go_engine_port_count() {
    ss -Hlnt | awk '$4 ~ /:2235[1-4]$/ { p=$4; sub(/^.*:/,"",p); seen[p]=1 } END { print length(seen) }'
}
runtime_status() {
    check '监听端口' ss -lnt
    check '节点进程（精确程序名）' pgrep -a -x chainmaker
    check 'Go VM 容器' sudo -n docker ps -a --filter ancestor=chainmakerofficial/chainmaker-vm-engine:v2.4.0 --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}'
    check 'Java VM 容器' sudo -n docker ps -a --filter ancestor=chainmakerofficial/chainmaker-vm-engine-java:v2.4.0 --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}'
    check '内存' free -h
}
failure_logs() {
    local node number=0 file vm_id
    section '定位问题的日志摘录'
    for node in "${NODE_DIRS[@]}"; do
        number=$((number + 1))
        for file in "$node/bin/panic.log" "$node/log/wx-org${number}.chainmaker.org/system.log"; do
            if sudo -n test -f "$file"; then
                check "日志末尾 $file" sudo -n tail -n 16 "$file"
            fi
        done
    done
    printf '\n最近的 Go VM 容器（可能包含历史容器，以开始/结束时间为准）：\n'
    while IFS= read -r vm_id; do
        [[ -n "$vm_id" ]] || continue
        check 'VM 状态与错误' sudo -n docker inspect --format '{{.Name}} Started={{.State.StartedAt}} Finished={{.State.FinishedAt}} Exit={{.State.ExitCode}} OOM={{.State.OOMKilled}} Error={{.State.Error}}' "$vm_id"
        check 'VM 标准输出末尾' sudo -n docker logs --tail 16 "$vm_id"
    done < <(sudo -n docker ps -aq --filter ancestor=chainmakerofficial/chainmaker-vm-engine:v2.4.0 | head -n 4)
}

run_stage() {
    local node number=0 file ports status attempt probe_status
    printf '学历证书存证系统 / 第 3 步启动与真实区块查询\n'
    printf '开始时间：%s\n结果目录：%s\n' "$(date --iso-8601=seconds)" "$RUN_DIR"
    printf '将调用四个现有发布目录的 bin/start.sh；保留原四节点拓扑。\n'
    printf '不调用 prepare/build/cluster_quick_start，不重新解包发布目录。\n'
    printf '最终区块查询不部署合约；DOCKER_GO 仍须另做合约验收。\n'

    section '1. 启动前核对'
    for node in "${NODE_DIRS[@]}"; do
        number=$((number + 1))
        for file in "$node/bin/start.sh" "$node/bin/chainmaker" "$node/config/wx-org${number}.chainmaker.org/chainmaker.yml"; do
            if ! sudo -n test -r "$file"; then
                printf '缺少或无法读取：%s。未开始启动。\n' "$file"
                return 1
            fi
        done
        if ! sudo -n bash -n "$node/bin/start.sh"; then
            printf '节点 %s 的启动脚本语法检查失败。未开始启动。\n' "$number"
            return 1
        fi
        # Preserve the exact startup script as evidence, without changing it.
        sudo -n cat "$node/bin/start.sh" > "$RUN_DIR/start-org${number}.sh.txt" || return 1
        printf '节点 %s 启动脚本 SHA256：' "$number"
        sha256sum "$RUN_DIR/start-org${number}.sh.txt"
    done
    for file in client1.tls.key client1.tls.crt client1.sign.key client1.sign.crt; do
        if sudo -n test -r "$CMC_DIR/testdata/crypto-config/wx-org1.chainmaker.org/user/client1/$file"; then
            printf 'root 可读取客户端身份文件：%s（不输出内容）\n' "$file"
        else
            printf 'root 也无法读取客户端身份文件：%s；先修复身份路径，未开始启动。\n' "$file"
            return 1
        fi
    done
    if ! sudo -n test -x "$CMC_DIR/cmc"; then
        printf 'CMC 不存在或不可执行。\n'
        return 1
    fi
    if ! timeout 20s sudo -n docker info --format 'Docker={{.ServerVersion}} Cgroup={{.CgroupVersion}}'; then
        printf 'Docker 服务不可访问，未开始启动。\n'
        return 1
    fi
    check '首节点二进制版本（以输出为准）' sudo -n "${NODE_DIRS[0]}/bin/chainmaker" version
    check '系统 cgroup' stat -fc %T /sys/fs/cgroup/

    section '2. 启动现有四节点'
    ports="$(port_count)"
    if [[ "$ports" -eq 4 ]]; then
        printf '四个 RPC 端口已监听，本次跳过启动，直接验证链查询。\n'
    elif [[ "$ports" -gt 0 ]] || pgrep -x chainmaker >/dev/null; then
        printf '发现部分端口或已有 chainmaker 进程，停止自动启动，避免重复拉起。\n'
        runtime_status
        failure_logs
        return 1
    else
        number=0
        for node in "${NODE_DIRS[@]}"; do
            number=$((number + 1))
            printf '\n启动节点 %s：%s\n' "$number" "$node"
            timeout 60s sudo -n bash -c 'cd -- "$1" && exec bash ./start.sh' stage03 "$node/bin" < /dev/null > "$RUN_DIR/start-org${number}.output.txt" 2>&1
            status=$?
            sed -n '1,70p' "$RUN_DIR/start-org${number}.output.txt"
            printf '启动脚本退出码：%s（退出码不代表节点/VM已经可用）\n' "$status"
            if [[ $status -eq 124 ]]; then
                printf '该启动步骤超时，停止后续自动启动，请反馈日志。\n'
                runtime_status
                failure_logs
                return 1
            fi
        done
        printf '\n等待四个 RPC 端口，最多约 45 秒……\n'
        for attempt in {1..15}; do
            [[ "$(port_count)" -eq 4 ]] && break
            sleep 3
        done
    fi
    section '3. 启动后状态'
    runtime_status

    section '4. 查询 chain1 的真实区块'
    (
        cd -- "$CMC_DIR" || exit 1
        sudo -n timeout 35s ./cmc query block-by-height \
            --chain-id=chain1 \
            --sdk-conf-path="$SDK_CONFIG" \
            --enable-cert-hash=false \
            --with-rw-set=false \
            --truncate-value=true
    ) > "$RUN_DIR/cmc-query.txt" 2>&1
    probe_status=$?
    printf 'CMC 退出码：%s\n原始响应：%s\n' "$probe_status" "$RUN_DIR/cmc-query.txt"
    python3 "$SUMMARY_SCRIPT" "$RUN_DIR/cmc-query.txt" --exit-code "$probe_status" --chain-id chain1
    status=$?
    if [[ $status -ne 0 ]]; then
        printf '\n未确认区块查询成功。原始响应前 6000 字节：\n'
        head -c 6000 "$RUN_DIR/cmc-query.txt"
        printf '\n'
        failure_logs
        return 1
    fi
    if [[ "$(port_count)" -ne 4 || "$(go_engine_port_count)" -ne 4 ]]; then
        printf '\n区块读取成功，但四个节点 RPC 或 Go VM 引擎端口尚未全部监听；本步骤仍需排查。\n'
        failure_logs
        return 1
    fi
    printf '\n区块读取已经验证，四个 RPC 和四个 Go 引擎端口均在监听。\n'
    printf '端口状态不证明合约能执行；下一步仍需部署并执行 Go 合约。\n'
    return 0
}

run_stage 2>&1 | tee "$REPORT"
stage_codes=("${PIPESTATUS[@]}")
if [[ ${stage_codes[1]} -ne 0 ]]; then
    printf '报告写入失败。\n' >&2
    exit 1
fi
printf '\n本步骤结果码：%s\n报告：%s\n' "${stage_codes[0]}" "$REPORT"
printf '请反馈 report.txt；保留同目录的启动脚本副本、启动输出和 CMC 原始响应。\n'
exit "${stage_codes[0]}"
