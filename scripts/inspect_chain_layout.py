#!/usr/bin/env python3
"""Read selected ChainMaker deployment metadata; never execute or rewrite config.

This is a field excerpt tool, not a general YAML parser. Anchors and includes
are deliberately not resolved. Its output is for reviewing the next command.
"""

import argparse
import os
from pathlib import Path
import re


SCALARS = {
    "chain_id", "org_id", "auth_type", "node_addr", "listen_addr",
    "listen_ip", "listen_port", "port", "host", "enable_tls", "tls_host_name",
    "enable_dockervm", "enable_docker_vm", "enable_external_dockervm",
    "docker_vm_host", "docker_vm_port", "docker_host", "docker_port",
    "protocol", "image", "container_name", "uds_open", "uds_path",
    "connection_protocol", "runtime_server_host", "runtime_server_port",
    "contract_engine_host", "contract_engine_port", "max_send_msg_size",
    "enable_rustvm", "enable_wasmer", "enable_gasm", "enable_evm",
    "enable_socket", "enable_uds", "enable", "enabled", "type", "mode", "alias",
}
PATHS = {
    "user_key_file_path", "user_crt_file_path", "user_sign_key_file_path",
    "user_sign_crt_file_path", "trust_root_paths", "chainconfig",
    "config_path", "dockervm_config_path", "docker_vm_config_path", "vm_config", "vm_config_path",
    "data_mount_path", "log_mount_path",
}
SECTIONS = {"rpc", "tls", "vm", "docker_vm", "dockervm", "consensus", "blockchain"}
KEY = re.compile(r"^(\s*)(?:-\s+)?([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")


def path_status(value, working_dir):
    plain = value.strip("\"'")
    if not working_dir or not plain or any(c in plain for c in "{}[]&*#$"):
        return []
    target = Path(plain)
    candidate = target if target.is_absolute() else working_dir / target
    state = "存在且当前用户可读" if candidate.exists() and os.access(candidate, os.R_OK) else "不存在或当前用户不可读"
    return [f"    按 CMC 工作目录解析：{candidate} [{state}]"]


def excerpt(path, working_dir=None, limit=40):
    """Return allowlisted field lines; do not include free-form log/error bodies."""
    output = []
    try:
        if path.stat().st_size > 1024 * 1024:
            return ["配置超过 1 MiB，跳过自动摘录。"]
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"无法读取：{type(exc).__name__}"]
    stack = []
    block_indent = None
    for number, line in enumerate(lines, 1):
        if block_indent is not None:
            if not line.strip() or len(line) - len(line.lstrip()) > block_indent:
                continue
            block_indent = None
        match = KEY.match(line)
        if not match:
            if stack and stack[-1][1] == "trust_root_paths" and not any(
                re.search(r"password|secret|kms|pkcs11|archive", item[1], re.I) for item in stack
            ):
                text = line.strip()
                if text.startswith("- ") and len(line) - len(line.lstrip()) >= stack[-1][0]:
                    value = text[2:].split(" #", 1)[0].strip()
                    if value and len(value) <= 240 and not value.startswith("#"):
                        output.append(f"  L{number} trust_root_paths[] = {value}")
                        output.extend(path_status(value, working_dir))
                        if len(output) >= limit:
                            output.append(f"  已达到本文件摘录上限 {limit} 行。")
                            break
            continue
        indent, key, raw = match.groups()
        level = len(indent)
        while stack and stack[-1][0] >= level:
            stack.pop()
        # Comments and complex YAML values are not evaluated by this reader.
        value = raw.split(" #", 1)[0].strip()
        if value.startswith(("|", ">")):
            block_indent = level
            continue
        if not value or value.startswith("#"):
            if key in SECTIONS:
                output.append(f"  L{number} {key}:")
            stack.append((level, key))
            continue
        if key not in SCALARS and key not in PATHS:
            continue
        # Never report values nested in credentials/provider configuration.
        ancestors = [item[1] for item in stack]
        if any(re.search(r"password|secret|kms|pkcs11|archive", name, re.I) for name in ancestors):
            continue
        display = value if len(value) <= 240 else value[:240] + "…"
        output.append(f"  L{number} {'.'.join(ancestors + [key])} = {display}")
        if key in PATHS and working_dir:
            output.extend(path_status(value, working_dir))
        if len(output) >= limit:
            output.append(f"  已达到本文件摘录上限 {limit} 行。")
            break
    return output or ["未发现白名单字段；需要进一步核对文件格式。"]


def show(path, working_dir=None, limit=40, compact=False):
    print(f"\n{path}")
    lines = excerpt(path, working_dir, limit)
    if compact:
        lines = [line for line in lines if any(token in line for token in (
            "org_id =", "rpc.", "vm.go.enable =", "runtime_server.port =",
            "contract_engine.host =", "contract_engine.port =", "无法读取", "上限",
        ))]
    for line in lines:
        print(line)


def inspect(root):
    print(f"目标 ChainMaker 目录：{root}")
    print("字段摘录供人工核对，不解析 YAML 锚点，也不证明配置已加载或链在线。")
    if not root.is_dir():
        print("目录不存在或不可访问。")
        return False
    for path in (root, root / "build", root / "build/release", root / "tools/cmc"):
        try:
            stat = path.stat()
            print(f"目录 {path}: uid={stat.st_uid}, gid={stat.st_gid}, 权限={stat.st_mode & 0o777:o}")
        except OSError as exc:
            print(f"目录 {path}: {type(exc).__name__}")

    print("\n--- 发布节点配置（优先；最多 4 节点）---")
    nodes = sorted((root / "build/release").glob("*/config/*/chainmaker.y*ml"))
    if not nodes:
        print("未在 build/release 的标准布局下发现发布节点配置。")
        nodes = sorted((root / "build/config").glob("*/chainmaker.y*ml"))
        if nodes:
            print("以下仅为 build/config 构建配置，不代表已经打包或运行。")
    if not nodes:
        print("build/config 中也未发现标准节点配置；下一步需确认构建/部署布局。")
    for node in nodes[:4]:
        show(node, limit=80, compact=node != nodes[0])
        # The first node supplies chain/VM details; other nodes expose endpoints.
        if node != nodes[0]:
            continue
        for config in sorted((node.parent / "chainconfig").glob("bc*.y*ml"))[:2]:
            show(config, limit=16)
        vm_files = [p for p in node.parent.glob("*.y*ml") if "vm" in p.name.lower()]
        for vm_file in sorted(vm_files)[:3]:
            show(vm_file, limit=22)
    print(f"发现 {len(nodes)} 份节点配置，最多显示 4 份。")

    print("\n--- 默认 CMC 配置与身份路径 ---")
    cmc_dir = root / "tools/cmc"
    sdk = cmc_dir / "testdata/sdk_config.yml"
    if sdk.is_file():
        show(sdk, working_dir=cmc_dir, limit=44)
    else:
        print(f"未发现 {sdk}")
    print("相对身份路径暂按 CMC 工作目录解读；最终须以该版本实际行为为准。")
    print("仅检查路径可读性，不打开私钥或证书文件。")

    print("\n--- 现成的启动文件（列出路径，不执行）---")
    paths = [root / "scripts/cluster_quick_start.sh", root / "scripts/cluster_quick_stop.sh"]
    paths += sorted((root / "build/release").glob("*/bin/start.sh"))[:4]
    for path in paths:
        print(f"{path}: {'存在' if path.is_file() else '未找到'}")
    print("\n--- 顶层 build 目录（最多 15 项）---")
    try:
        for path in sorted((root / "build").iterdir())[:15]:
            print(f"{'目录' if path.is_dir() else '文件'} {path.name}")
    except OSError as exc:
        print(type(exc).__name__)
    return bool(nodes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/home/wsc/chainmaker-go"))
    parser.add_argument("--fallback-root", type=Path)
    args = parser.parse_args()
    found_nodes = inspect(args.root)
    if not found_nodes and args.fallback_root and args.fallback_root.is_dir():
        print("\n=== 首选目录未发现标准节点配置；补查已有备用链（不会启动或切换链）===")
        inspect(args.fallback_root)
