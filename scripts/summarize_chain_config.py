#!/usr/bin/env python3
"""Extract runtime capabilities from a read-only CMC response; never print certificates."""
import argparse
import base64
import json
from pathlib import Path
from summarize_chain_probe import _read_json, _envelope_failed


def find_chain_config(node, depth=0):
    if depth > 8 or not isinstance(node, dict) or _envelope_failed(node):
        return None
    if node.get("chain_id", node.get("chainId")) == "chain1" and "vm" in node:
        return node
    for key in ("data", "result", "response", "chain_config", "chainConfig", "contract_result", "contractResult"):
        value = node.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                try:
                    value = json.loads(base64.b64decode(value, validate=True))
                except (ValueError, TypeError, UnicodeError):
                    continue
        found = find_chain_config(value, depth + 1)
        if found is not None:
            return found
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        with args.path.open("rb") as stream:
            data = stream.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("response too large")
        config = find_chain_config(_read_json(data.decode("utf-8-sig")))
        if config is None:
            raise ValueError("no chain config")
        print("链 ID：chain1")
        print("链配置中的 VM 字段：" + json.dumps(config["vm"], ensure_ascii=False))
        consensus = config.get("consensus", {})
        print("共识类型：" + str(consensus.get("type", "未提取")))
        print("部署 runtime-type 将根据实际支持列表选定；GO 与 DOCKER_GO 不预设为同一枚举。")
    except (OSError, ValueError, UnicodeError, RecursionError):
        print("未自动提取 VM 支持列表；保留原始响应供下一步核对。不要因此重新建链。")
        raise SystemExit(1)
