#!/usr/bin/env bash
# Ubuntu environment inventory. No installation, chain transactions, or service changes.
set -u
set -o pipefail
umask 077

if [[ "$(uname -s)" != Linux ]]; then
    printf '请在 Ubuntu 虚拟机的终端运行此脚本，不要在 Windows 中运行。\n' >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)" || exit 1
mkdir -p -- "$PROJECT_DIR/reports" || exit 1
REPORT="$(mktemp "$PROJECT_DIR/reports/environment-$(date +%Y%m%d-%H%M%S)-XXXXXX.txt")" || exit 1

section() { printf '\n========== %s ==========\n' "$1"; }
check() {
    local label="$1" code
    shift
    printf '\n[%s]\n' "$label"
    if ! command -v "$1" >/dev/null 2>&1; then
        printf '未找到命令：%s（继续检查其他项目）\n' "$1"
        return 0
    fi
    timeout 15s "$@" 2>&1
    code=$?
    if [[ $code -eq 124 ]]; then
        printf '检查超过 15 秒，已跳过。\n'
    elif [[ $code -ne 0 ]]; then
        printf '此项退出码：%s（不代表全部检查失败）\n' "$code"
    fi
    return 0
}

collect() {
    local root candidate file count line sdk_target sdk_path
    local -a roots=()
    printf '学历证书存证系统 / 第 1 步环境检查\n'
    printf '时间：%s\n用户：%s\n主目录：%s\n项目目录：%s\n' \
        "$(date --iso-8601=seconds)" "$(id -un)" "$HOME" "$PROJECT_DIR"
    printf '本报告只说明环境现状；尚未证明链连接或合约可用。\n'
    printf '本脚本仅写入 reports 下的检查报告，不安装软件、不启停服务、不发送交易。\n'

    section '1. 系统与资源'
    if [[ -r /etc/os-release ]]; then
        grep -E '^(NAME|VERSION|VERSION_ID|PRETTY_NAME)=' /etc/os-release
    fi
    check 'CPU 架构' uname -m
    check '内存' free -h
    check '项目所在磁盘' df -h "$PROJECT_DIR"
    check '用户组（用于核对 Docker 权限）' id -nG

    section '2. Python 与 Go'
    check 'Python' python3 --version
    check 'Python venv / pip 模块' python3 -c 'import importlib.util; print("venv=" + str(importlib.util.find_spec("venv") is not None)); print("ensurepip=" + str(importlib.util.find_spec("ensurepip") is not None)); print("pip=" + str(importlib.util.find_spec("pip") is not None))'
    if command -v go >/dev/null 2>&1; then
        printf '\nGo 路径：%s\n' "$(command -v go)"
        check 'Go（禁止自动下载工具链）' env GOTOOLCHAIN=local go version
    else
        printf '\n未找到 go 命令。\n'
    fi
    check 'Git' git --version
    printf '\n可用的压缩工具路径：\n'
    for candidate in 7z 7zz 7za tar unzip; do
        command -v "$candidate" || true
    done

    section '3. Docker（不使用 sudo，不改变用户权限）'
    check 'Docker 命令行版本' docker --version
    check 'Docker 服务可达性' docker info --format 'Server={{.ServerVersion}}; OS={{.OperatingSystem}}; Architecture={{.Architecture}}'
    check 'Docker Compose 插件' docker compose version
    if command -v docker-compose >/dev/null 2>&1; then
        check '独立 Docker Compose' docker-compose --version
    fi
    check '容器名称、镜像和状态' docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
    printf '\n如出现 permission denied，说明当前用户权限不足；不能据此判断 Docker 未安装。\n'
    printf '如出现 Cannot connect，需在下一步区分服务未启动和 Docker 上下文配置问题。\n'

    section '4. 已有 ChainMaker 目录'
    for root in "$HOME/chainmaker" "$HOME/chainmaker-go" \
        "$HOME/chainmaker-cryptogen" "$HOME/chainmaker-sdk-demo" \
        "$HOME/ChangAnChain" "$HOME/frontend"; do
        if [[ -d "$root" ]]; then
            printf '存在：%s\n' "$root"
            roots+=("$root")
        else
            printf '未找到：%s\n' "$root"
        fi
    done
    printf '\n主目录内可能的合约 SDK 目录：\n'
    for root in "$HOME"/*; do
        [[ -d "$root" ]] || continue
        case "$(basename -- "$root")" in
            *contract*sdk*|*sdk*go*|*docker*go*)
                printf '%s\n' "$root"
                roots+=("$root")
                ;;
        esac
    done
    printf '\n源码版本线索（不是运行节点版本的证明）：\n'
    if [[ -d "$HOME/chainmaker-go/.git" ]] && command -v git >/dev/null 2>&1; then
        check 'chainmaker-go 最近标签/提交' git -C "$HOME/chainmaker-go" describe --tags --always
        check 'chainmaker-go 当前分支' git -C "$HOME/chainmaker-go" branch --show-current
    fi

    section '5. CMC 与配置/构建文件的位置'
    if [[ ${#roots[@]} -gt 0 ]]; then
        count=0
        # Restrict discovery to known project roots; do not follow symlinks or read keys.
        while IFS= read -r -d '' file; do
            count=$((count + 1))
            if [[ $count -le 120 ]]; then printf '%s\n' "$file"; fi
        done < <(timeout 20s find "${roots[@]}" -maxdepth 9 \
            -type d \( -name .git -o -name node_modules -o -name .venv \
                -o -name vendor -o -name data -o -name log -o -name logs \
                -o -name storage -o -name .cache \) -prune -o \
            -type f \( -name cmc -o -name go.mod -o -name chainmaker.yml \
                -o -name chainmaker.yaml -o -name bc1.yml -o -name bc1.yaml \
                -o -name 'sdk_config*.yml' -o -name 'sdk_config*.yaml' \
                -o -name cluster_quick_start.sh -o -name build.sh \) -print0 2>/dev/null)
        printf '发现 %s 个候选文件（最多显示 120 个；搜索限时 20 秒，深度 9）。\n' "$count"
        printf '未列出不等于不存在：其他目录、权限或搜索上限可能影响结果。\n'
    fi
    candidate="$HOME/chainmaker-go/tools/cmc/cmc"
    if [[ -x "$candidate" ]]; then
        printf '\n已知 CMC 路径：%s\n' "$candidate"
        check 'CMC 版本（若版本子命令不支持，保留错误供排查）' "$candidate" version
    elif command -v cmc >/dev/null 2>&1; then
        printf '\nPATH 中的 CMC：%s\n' "$(command -v cmc)"
        check 'CMC 版本' cmc version
    else
        printf '\n已知路径和 PATH 中没有可执行 CMC；请结合上面的发现结果判断。\n'
    fi

    section '6. Go 模块与合约 SDK 线索'
    if [[ ${#roots[@]} -gt 0 ]]; then
        count=0
        while IFS= read -r -d '' file; do
            count=$((count + 1))
            [[ $count -le 35 ]] || continue
            printf '\n%s\n' "$file"
            # Only module names, Go versions and ChainMaker module versions, never full files.
            grep -E '^(module |go |toolchain )|^[[:space:]]*chainmaker\.org/[^[:space:]]+[[:space:]]+v[0-9]' "$file" | head -n 12
            while IFS= read -r line || [[ -n "$line" ]]; do
                if [[ "$line" == *chainmaker.org/chainmaker/contract-sdk-go*'=>'* ]]; then
                    read -r sdk_target _ <<< "${line#*=>}"
                    sdk_target="${sdk_target#\"}"
                    sdk_target="${sdk_target%\"}"
                    case "$sdk_target" in
                        ./*|../*|/*)
                            printf '合约 SDK 本地 replace 目标：%s\n' "$sdk_target"
                            if [[ "$sdk_target" == /* ]]; then
                                sdk_path="$sdk_target"
                            else
                                sdk_path="$(dirname -- "$file")/$sdk_target"
                            fi
                            if [[ -f "$sdk_path/go.mod" ]]; then
                                printf '目标 go.mod 存在；版本兼容性仍需核对。\n'
                            else
                                printf '目标 go.mod 不存在或不可访问，可能阻止合约构建。\n'
                            fi
                            ;;
                    esac
                fi
            done < "$file"
        done < <(timeout 20s find "${roots[@]}" -maxdepth 7 \
            -type d \( -name .git -o -name node_modules -o -name vendor \
                -o -name .venv -o -name data -o -name log -o -name logs \) -prune -o \
            -type f -name go.mod -print0 2>/dev/null)
        printf '\nGo 模块最多展示 35 份；目录名、链版本和合约 SDK 版本需要分别核对。\n'
    fi

    section '7. 节点进程与监听端口'
    printf '相关进程（只显示 PID 和程序名，不显示命令行参数）：\n'
    ps -eo pid,comm | grep -Ei 'chainmaker|dockerd|containerd|dockervm|docker_vm' || true
    check 'TCP 监听端口' ss -lnt
    printf '\n有监听端口不等于链可用；下一步需使用实际 SDK 配置做链查询。\n'

    section '8. 身份目录是否存在'
    for root in "$HOME/chainmaker-go/build/crypto-config" \
        "$HOME/chainmaker-go/tools/cmc/testdata/crypto-config" \
        "$HOME/chainmaker-go/release" "$HOME/chainmaker/crypto-config"; do
        if [[ -d "$root" ]]; then printf '存在：%s\n' "$root"; else printf '未找到：%s\n' "$root"; fi
    done
    printf '未读取证书正文、私钥、密码或完整 SDK 配置。目录存在不证明身份与运行链匹配。\n'
    section '检查结束'
    printf '请反馈本报告；根据实际环境安排链连接与 DOCKER_GO 验证。\n'
}

if ! command -v timeout >/dev/null 2>&1; then
    printf '缺少 Ubuntu 常见的 timeout 命令，请反馈此提示；未继续执行检查。\n' >&2
    exit 1
fi

collect 2>&1 | tee "$REPORT"
pipeline_status=("${PIPESTATUS[@]}")
if [[ ${pipeline_status[1]} -ne 0 ]]; then
    printf '\n报告写入失败，请检查磁盘空间或目录权限。\n' >&2
    exit 1
fi
printf '\n报告已保存：%s\n' "$REPORT"
printf '可用 Ubuntu 文本编辑器打开报告，把内容发回当前对话。\n'
