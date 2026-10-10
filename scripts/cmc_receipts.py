"""Strict CMC receipts and persisted transaction evidence (stdlib only).

ChainMaker protobuf JSON may omit zero codes. Such omission is accepted only
inside a structurally identified response containing an actual contract result.
An exit code, a success message, or a JSON payload alone never proves success.
"""
import base64
import binascii
from dataclasses import dataclass
import json
import re

from summarize_chain_probe import _read_json, _reject_constant, _unique_object


class ReceiptError(ValueError):
    pass


@dataclass
class Receipt:
    exit_code: int
    chain_code: object
    contract_code: int
    tx_id: str
    data: object
    contract_message: str
    chain_message: str
    raw_object: dict


def _field(value, snake, camel=None, default=None):
    if camel and snake in value and camel in value and value[snake] != value[camel]:
        raise ReceiptError("回执同一字段的两种命名存在矛盾：" + snake)
    return value.get(snake, value.get(camel, default) if camel else default)


def _json(text):
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def _payload(value):
    if isinstance(value, (dict, list)) or value is None:
        return value
    if not isinstance(value, str):
        raise ReceiptError("合约返回内容不是字符串、对象或数组。")
    if not value:
        return None
    try:
        return _json(value)
    except ValueError:
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return value
        try:
            return _json(decoded)
        except ValueError:
            # DOCKER_GO puts sdk.Error text in the protobuf bytes result while
            # its message can be only "Fail". Decode that text as well as JSON.
            # require_success still requires the complete business JSON object.
            return decoded


def _code(value, *, chain=False):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str):
        if re.fullmatch(r"[0-9]+", value):
            return int(value)
        if chain and value == "SUCCESS":
            return 0
        if chain and value == "CONTRACT_FAIL":
            return 4
        if chain and re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
            return value  # Preserved as a failure, never treated as success.
    raise ReceiptError("回执状态码类型无效。")


def _parse_object(obj, exit_code):
    if not isinstance(obj, dict):
        raise ReceiptError("回执不是对象。")
    if obj.get("error") or obj.get("errors") or obj.get("success") is False:
        raise ReceiptError("CMC 输出包含错误。")
    contract = _field(obj, "contract_result", "contractResult")
    if not isinstance(contract, dict) or not contract:
        raise ReceiptError("回执没有完整的 contract_result，不能确认合约执行。")
    # Both zero-valued fields carry json:...,omitempty in pb-go v2.4.0.
    chain_code = _code(obj.get("code", 0), chain=True)
    contract_code = _code(contract.get("code", 0))
    result = contract.get("result")
    # Some CMC releases decode the result and print it alongside the protobuf.
    alternative = _field(obj, "contract_result_result", "contractResultResult")
    if alternative is not None:
        decoded = _payload(alternative)
        if result not in (None, "") and _payload(result) != decoded:
            raise ReceiptError("回执出现两个不一致的合约返回内容。")
    else:
        decoded = _payload(result)
    if contract_code == 0 and decoded is None:
        raise ReceiptError("合约成功回执缺少返回内容，不能把提交确认当成执行成功。")
    tx_id = _field(obj, "tx_id", "txId", "")
    if not isinstance(tx_id, str):
        raise ReceiptError("交易 ID 类型无效。")
    message, contract_message = obj.get("message", ""), contract.get("message", "")
    if not isinstance(message, str) or not isinstance(contract_message, str):
        raise ReceiptError("回执消息类型无效。")
    return Receipt(exit_code, chain_code, contract_code, tx_id, decoded,
                   contract_message, message, obj)


def parse_receipt(raw, exit_code):
    if len(raw.encode("utf-8")) > 8 * 1024 * 1024:
        raise ReceiptError("CMC 输出超出解析上限。")
    try:
        obj = _read_json(raw)
    except ValueError as exc:
        raise ReceiptError("CMC 未返回单个完整 JSON 回执：" + str(exc)) from exc
    # Known wrappers only; do not search arbitrary nested objects for success.
    for key in ("tx_response", "txResponse", "response"):
        if key in obj:
            if "contract_result" in obj or "contractResult" in obj:
                raise ReceiptError("CMC 输出存在多个候选回执。")
            if obj.get("error") or obj.get("success") is False or _code(obj.get("code", 0), chain=True) != 0:
                raise ReceiptError("外层回执报告失败。")
            obj = obj[key]
            break
    return _parse_object(obj, exit_code)


def require_success(receipt, *, require_data=True):
    if receipt.exit_code != 0:
        raise ReceiptError("CMC 退出码非 0，尚未确认本次操作成功。")
    if receipt.chain_code != 0 or receipt.contract_code != 0:
        detail = receipt.contract_message
        if isinstance(receipt.data, str) and receipt.data != detail:
            detail += ": " + receipt.data
        raise ReceiptError(f"链/合约执行失败：{receipt.chain_code}/{receipt.contract_code}；"
                           + detail[:800])
    if not require_data:
        return receipt.data
    data = receipt.data
    if not isinstance(data, dict) or data.get("ok") is not True or not isinstance(data.get("data"), dict):
        raise ReceiptError("合约返回内容不符合学历存证协议。")
    return data["data"]


def require_rejection(receipt, tag):
    if receipt.exit_code != 0:
        raise ReceiptError("命令执行错误不能当成权限/业务拒绝验证通过。")
    if receipt.chain_code not in (0, 4) or receipt.contract_code == 0:
        raise ReceiptError("没有收到明确的合约执行拒绝回执。")
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", tag):
        raise ReceiptError("预期的合约拒绝标识无效。")
    # Accept only explicit contract errors, never a token found in an arbitrary
    # log or transport message. The two observed layouts are a message error
    # (optionally prefixed by "contract failed:") or a result error + VM "Fail".
    # If both carry meaningful content, both must agree on the exact error.
    evidence = []
    if receipt.data is not None:
        evidence.append(receipt.data)
    if receipt.contract_message.strip() not in ("", "Fail"):
        evidence.append(receipt.contract_message)
    expected = re.compile(r"(?:contract failed:\s*)?" + re.escape(tag))
    if not evidence or any(not isinstance(value, str) or not expected.fullmatch(value.strip())
                           for value in evidence):
        raise ReceiptError("合约拒绝原因与预期不一致：" + receipt.contract_message[:800])


def parse_transaction(raw, exit_code, *, tx_id, contract_name, method, params=None, expected_sender_org=None):
    """Validate query tx evidence, binding it to the submitted call and arguments.

This is also used when invoke prints an error instead of a JSON receipt: only
an actual persisted transaction with matching payload can resolve that outcome.
"""
    if exit_code != 0:
        raise ReceiptError("按交易 ID 查询未成功，保留待确认状态。")
    try:
        obj = _read_json(raw)
    except ValueError as exc:
        raise ReceiptError("交易查询输出不是完整 JSON。") from exc
    if obj.get("error") or obj.get("success") is False or _code(obj.get("code", 0), chain=True) != 0:
        raise ReceiptError("交易查询报告失败。")
    for key in ("transaction_info", "transactionInfo", "tx_info", "txInfo", "data"):
        if key in obj:
            if "transaction" in obj:
                raise ReceiptError("交易查询存在多个候选结果。")
            obj = obj[key]
            break
    if not isinstance(obj, dict):
        raise ReceiptError("交易查询结果不是对象。")
    height = _field(obj, "block_height", "blockHeight")
    if type(height) is not int and not (isinstance(height, str) and height.isdigit()):
        raise ReceiptError("交易没有区块高度，尚不能证明已入链。")
    if int(height) <= 0:
        raise ReceiptError("本次业务交易的区块高度无效。")
    transaction = obj.get("transaction")
    if not isinstance(transaction, dict):
        raise ReceiptError("交易查询缺少 transaction。")
    if expected_sender_org is not None:
        if not isinstance(expected_sender_org, str) or not expected_sender_org:
            raise ReceiptError("预期的交易发送机构无效。")
        sender = transaction.get("sender")
        signer = sender.get("signer") if isinstance(sender, dict) else None
        if not isinstance(signer, dict) or _field(signer, "org_id", "orgId") != expected_sender_org:
            raise ReceiptError("链上交易发送机构与本次调用身份不一致。")
    payload = transaction.get("payload")
    result = transaction.get("result")
    if not isinstance(payload, dict) or not isinstance(result, dict):
        raise ReceiptError("已查询交易缺少 payload/result。")
    if (_field(payload, "tx_id", "txId") != tx_id or
            _field(payload, "chain_id", "chainId") != "chain1" or
            _field(payload, "contract_name", "contractName") != contract_name or
            payload.get("method") != method):
        raise ReceiptError("查询到的交易与本次调用的 ID、链、合约或方法不一致。")
    tx_type = _field(payload, "tx_type", "txType", 0)
    if type(tx_type) not in (int, str) or tx_type not in (0, "0", "INVOKE_CONTRACT"):
        raise ReceiptError("查询到的交易不是写入交易。")
    if params is not None:
        pairs = payload.get("parameters")
        if not isinstance(pairs, list):
            raise ReceiptError("交易缺少参数，无法核对本次调用。")
        values = {}
        for pair in pairs:
            if not isinstance(pair, dict) or not isinstance(pair.get("key"), str) or pair["key"] in values:
                raise ReceiptError("交易参数结构异常或重复。")
            values[pair["key"]] = pair.get("value")
        if set(values) != set(params):
            raise ReceiptError("链上交易参数集合与发送参数不一致。")
        for key, expected in params.items():
            actual = values.get(key)
            encoded = base64.b64encode(expected.encode("utf-8")).decode("ascii")
            if actual not in (expected, encoded):
                raise ReceiptError("链上交易参数与发送参数不一致：" + key)
    bound_result = dict(result)
    bound_result["tx_id"] = tx_id
    return _parse_object(bound_result, 0)
