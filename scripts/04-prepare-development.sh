#!/usr/bin/env bash
# Prepare ordinary-user client identity copies, prove chain reads, build contract.
# This step does not deploy a contract or modify the existing chain directories.
set -u
set -o pipefail
umask 077

if [[ "$(uname -s)" != Linux || $EUID -eq 0 ]]; then
    printf '请在 Ubuntu 中以 wsc 普通用户执行，先 sudo -v；不要 sudo bash 本脚本。\n' >&2
    exit 1
fi
for program in sudo timeout python3 openssl sha256sum cmp go 7z; do
    command -v "$program" >/dev/null 2>&1 || { printf '缺少命令：%s\n' "$program"; exit 1; }
done
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)" || exit 1
CHAIN_DIR=/home/wsc/chainmaker-go
CMC="$CHAIN_DIR/tools/cmc/cmc"
SOURCE_IDENTITIES="$CHAIN_DIR/tools/cmc/testdata/crypto-config"
IDENTITIES="$PROJECT_DIR/instance/chain-identities"
WORK_DIR="$PROJECT_DIR/instance/cmc-work"
RUN_UID="$(id -u)"
RUN_GID="$(id -g)"
sudo -n true || { printf '请先在当前终端执行 sudo -v。\n'; exit 1; }
mkdir -p -- "$PROJECT_DIR/reports" || exit 1
RUN_DIR="$(mktemp -d "$PROJECT_DIR/reports/stage04-$(date +%Y%m%d-%H%M%S)-XXXXXX")" || exit 1

safe_dir() {
    [[ ! -L "$1" ]] || { printf '目录是符号链接，停止写入：%s\n' "$1"; return 1; }
    mkdir -p -- "$1" && chmod 700 -- "$1"
}
copy_identity() {
    local source="$1" target="$2"
    [[ ! -L "$target" ]] || { printf '身份目标是符号链接，停止：%s\n' "$target"; return 1; }
    sudo -n test -r "$source" || { printf '原身份文件不可读：%s\n' "$source"; return 1; }
    if [[ -e "$target" ]]; then
        if ! sudo -n cmp -s -- "$source" "$target"; then
            printf '目标身份与原链身份不同，保留现有文件并停止：%s\n' "$target"
            return 1
        fi
        [[ -O "$target" && -r "$target" ]] || { printf '现有身份副本不归当前用户所有：%s\n' "$target"; return 1; }
        chmod 600 -- "$target" || return 1
    else
        sudo -n install -o "$RUN_UID" -g "$RUN_GID" -m 600 -- "$source" "$target" || return 1
    fi
}
check_pair() {
    local cert="$1" key="$2" public_cert public_key
    public_cert="$(openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum)" || return 1
    public_key="$(openssl pkey -in "$key" -passin pass: -pubout -outform DER | sha256sum)" || return 1
    [[ "$public_cert" == "$public_key" ]] || { printf '证书与私钥不匹配：%s\n' "$cert"; return 1; }
}
write_sdk() {
    local role="$1" org="$2" target="$PROJECT_DIR/config/sdk-${1}.yml" tmp
    tmp="$(mktemp "$PROJECT_DIR/config/.sdk-${role}-XXXXXX")" || return 1
    cat > "$tmp" <<EOF
chain_client:
  chain_id: chain1
  org_id: $org
  user_key_file_path: $IDENTITIES/$org/client1.tls.key
  user_crt_file_path: $IDENTITIES/$org/client1.tls.crt
  user_sign_key_file_path: $IDENTITIES/$org/client1.sign.key
  user_sign_crt_file_path: $IDENTITIES/$org/client1.sign.crt
  alias: ""
  node_only_async: false
  retry_limit: 20
  retry_interval: 500
  optimize_detection: -1
  nodes:
    - node_addr: 127.0.0.1:12301
      conn_cnt: 1
      enable_tls: true
      trust_root_paths:
        - $IDENTITIES/wx-org1.chainmaker.org/ca
      tls_host_name: chainmaker.org
  rpc_client:
    max_receive_message_size: 100
    max_send_message_size: 100
    send_tx_timeout: 30
    get_tx_timeout: 30
EOF
    if [[ -e "$target" || -L "$target" ]]; then
        if [[ ! -L "$target" ]] && cmp -s -- "$tmp" "$target"; then
            rm -- "$tmp"
        else
            printf '已有配置与本步生成配置不同，保留它并停止：%s\n候选配置：%s\n' "$target" "$tmp"
            return 1
        fi
    else
        mv -n -- "$tmp" "$target" || return 1
    fi
}

prepare() {
    local role number org kind code build_dir
    printf '学历证书存证系统 / 第 4 步应用身份与合约构建\n时间：%s\n' "$(date --iso-8601=seconds)"
    free -h
    printf '\n身份复制使用 sudo；以下链查询和 Go 构建以普通用户执行。\n'
    for path in "$PROJECT_DIR/instance" "$PROJECT_DIR/config" "$IDENTITIES" "$WORK_DIR"; do
        safe_dir "$path" || return 1
    done
    for number in 1 2 3; do
        org="wx-org${number}.chainmaker.org"
        safe_dir "$IDENTITIES/$org" && safe_dir "$IDENTITIES/$org/ca" || return 1
        for kind in tls sign; do
            for extension in key crt; do
                copy_identity "$SOURCE_IDENTITIES/$org/user/client1/client1.$kind.$extension" "$IDENTITIES/$org/client1.$kind.$extension" || return 1
            done
        done
        copy_identity "$SOURCE_IDENTITIES/$org/ca/ca.crt" "$IDENTITIES/$org/ca/ca.crt" || return 1
        for kind in tls sign; do
            check_pair "$IDENTITIES/$org/client1.$kind.crt" "$IDENTITIES/$org/client1.$kind.key" || return 1
            openssl verify -CAfile "$IDENTITIES/$org/ca/ca.crt" "$IDENTITIES/$org/client1.$kind.crt" || return 1
        done
        printf '\n组织 %s：两套证书/密钥匹配，CA 验证通过；签名证书信息：\n' "$org"
        openssl x509 -in "$IDENTITIES/$org/client1.sign.crt" -noout -subject -issuer -dates -fingerprint -sha256 || return 1
    done
    write_sdk uni wx-org1.chainmaker.org || return 1
    write_sdk edu wx-org2.chainmaker.org || return 1
    write_sdk query wx-org3.chainmaker.org || return 1

    printf '\n========== 三个角色以普通用户查询真实区块 ==========\n'
    for role in uni edu query; do
        printf '\n角色：%s\n' "$role"
        (cd -- "$WORK_DIR" && timeout 40s "$CMC" query block-by-height \
            --chain-id=chain1 --sdk-conf-path="$PROJECT_DIR/config/sdk-$role.yml" \
            --enable-cert-hash=false --with-rw-set=false --truncate-value=true) > "$RUN_DIR/block-$role.txt" 2>&1
        code=$?
        if ! python3 "$SCRIPT_DIR/summarize_chain_probe.py" "$RUN_DIR/block-$role.txt" --exit-code "$code" --chain-id chain1; then
            head -c 6000 "$RUN_DIR/block-$role.txt"
            printf '\n身份查询尚未通过，停止后续步骤。\n'
            return 1
        fi
    done

    printf '\n========== 当前链允许的合约运行时 ==========\n'
    (cd -- "$WORK_DIR" && timeout 40s "$CMC" client chainconfig query \
        --sdk-conf-path="$PROJECT_DIR/config/sdk-uni.yml" --enable-cert-hash=false) > "$RUN_DIR/chain-config.txt" 2>&1
    code=$?
    if [[ $code -eq 0 ]]; then
        python3 "$SCRIPT_DIR/summarize_chain_config.py" "$RUN_DIR/chain-config.txt" || true
    else
        printf '链配置查询退出码 %s，已保存原始输出，本次仍可构建源码。\n' "$code"
        head -c 3000 "$RUN_DIR/chain-config.txt"
        printf '\n'
    fi

    printf '\n========== 测试并构建合约（不部署） ==========\n'
    build_dir="$(mktemp -d "$PROJECT_DIR/contracts/degreecert/build-XXXXXX")" || return 1
    (
        cd -- "$PROJECT_DIR/contracts/degreecert" || exit 1
        export GOMAXPROCS=2 CGO_ENABLED=0
        export GOPROXY=off
        go version || exit 1
        go test -mod=vendor -p 1 ./core || exit 1
        GOOS=linux GOARCH=amd64 go build -mod=vendor -p 1 -trimpath -ldflags='-s -w' -o "$build_dir/degree_cert_v1" . || exit 1
        cd -- "$build_dir" || exit 1
        7z a -t7z degree_cert_v1.7z degree_cert_v1 > "$RUN_DIR/pack.txt" || exit 1
        7z t degree_cert_v1.7z >> "$RUN_DIR/pack.txt" || exit 1
        sha256sum degree_cert_v1 degree_cert_v1.7z
    ) || { printf '构建或打包未成功，请反馈当前报告。\n'; return 1; }
    printf '%s\n' "$build_dir/degree_cert_v1.7z" > "$PROJECT_DIR/contracts/degreecert/latest-build.txt"
    printf '\n构建包：%s/degree_cert_v1.7z\n' "$build_dir"
    printf '本步骤完成：普通用户身份查询通过、核心测试与 Linux 合约构建打包通过。尚未部署或执行该合约。\n'
}

prepare 2>&1 | tee "$RUN_DIR/report.txt"
statuses=("${PIPESTATUS[@]}")
if [[ ${statuses[1]} -ne 0 ]]; then exit 1; fi
printf '\n结果码：%s\n报告：%s/report.txt\n' "${statuses[0]}" "$RUN_DIR"
exit "${statuses[0]}"
