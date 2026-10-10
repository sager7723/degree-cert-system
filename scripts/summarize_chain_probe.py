#!/usr/bin/env python3
"""Validate read-only CMC block-query evidence without echoing its payload.

Only envelope fields and block headers are examined. Transaction results inside
the returned block are deliberately not interpreted as query status.
"""

import argparse
import json
from pathlib import Path
import re
import sys


MAX_OUTPUT_BYTES = 8 * 1024 * 1024
WRAPPERS = ("block", "block_info", "blockInfo", "data", "result", "response")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonstandard JSON constant")


def _read_json(raw):
    text = ANSI_ESCAPE.sub("", raw).lstrip("\ufeff")
    start = text.find("{")
    if start < 0:
        raise ValueError("missing JSON object")
    # Decode the outer object once. Never retry at nested braces: that could
    # accidentally accept a complete header inside an incomplete response.
    decoder = json.JSONDecoder(
        object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    result, end = decoder.raw_decode(text, start)
    if text[end:].strip():
        raise ValueError("trailing output or multiple responses")
    if not isinstance(result, dict):
        raise ValueError("response is not an object")
    return result


def _envelope_failed(node):
    for name in ("error", "errors"):
        if name in node and node[name] not in (None, "", False, 0, [], {}):
            return True
    if node.get("success") is False:
        return True
    status = node.get("status")
    if isinstance(status, str) and status.lower() in ("error", "failed", "failure"):
        return True
    for name in ("code", "status_code", "statusCode"):
        if name not in node:
            continue
        code = node[name]
        if type(code) is int and code == 0:
            continue
        if isinstance(code, str) and code.strip().upper() in ("0", "SUCCESS", "OK"):
            continue
        return True
    return False


def _headers(node, depth=0):
    if depth > 12 or _envelope_failed(node):
        raise ValueError("query envelope reports failure or is too deeply nested")
    result = []
    if "header" in node:
        if not isinstance(node["header"], dict):
            raise ValueError("invalid block header")
        result.append(node["header"])
    for name in WRAPPERS:
        child = node.get(name)
        if isinstance(child, dict):
            result.extend(_headers(child, depth + 1))
    return result


def _height(value):
    if type(value) is int and value >= 0:
        return value
    # Protobuf JSON may encode a uint64 as a decimal string.
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        return int(value)
    raise ValueError("height is not a nonnegative integer")


def assess_output(raw, exit_code, chain_id):
    """Return (verified, public_message); never include raw response text."""
    if exit_code != 0:
        return False, "链读取验证未通过：CMC 命令退出码非 0，请检查本地原始结果。"
    if not chain_id:
        return False, "链读取验证未通过：未指定目标链 ID。"
    try:
        headers = _headers(_read_json(raw))
        if not headers:
            return False, "链读取验证未通过：返回结果中没有可识别的区块头。"
        heights = []
        for header in headers:
            chain_values = [header[key] for key in ("chain_id", "chainId") if key in header]
            if not chain_values or any(value != chain_id for value in chain_values):
                return False, "链读取验证未通过：区块头中的链 ID 缺失或与目标链不一致。"
            height_values = [
                _height(header[key])
                for key in ("block_height", "blockHeight")
                if key in header
            ]
            if not height_values or len(set(height_values)) != 1:
                return False, "链读取验证未通过：区块高度缺失或存在矛盾。"
            heights.extend(height_values)
        if len(set(heights)) != 1:
            return False, "链读取验证未通过：结果包含多个不同区块，无法确定本次查询结果。"
    except (ValueError, TypeError, RecursionError):
        return False, "链读取验证未通过：返回错误、JSON 不完整或区块数据格式不符合要求。"
    return True, (
        f"链读取验证通过：区块高度 {heights[0]}。\n"
        "只证明区块查询成功，尚未验证 DOCKER_GO 执行。"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="判定 CMC 只读区块查询是否成功，不输出交易数据。")
    parser.add_argument("outputfile", type=Path)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--chain-id", required=True)
    args = parser.parse_args(argv)
    try:
        with args.outputfile.open("rb") as handle:
            content = handle.read(MAX_OUTPUT_BYTES + 1)
        if len(content) > MAX_OUTPUT_BYTES:
            print("链读取验证未通过：原始结果超过 8 MiB，未自动解析。")
            return 1
        raw = content.decode("utf-8-sig")
    except (OSError, UnicodeError):
        print("链读取验证未通过：无法读取 UTF-8 原始结果文件。")
        return 1
    verified, message = assess_output(raw, args.exit_code, args.chain_id)
    print(message)
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
