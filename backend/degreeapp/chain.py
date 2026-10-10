"""Real ChainMaker CMC calls with persisted-transaction confirmation.

The invocation acknowledgement is never a business success. An operation is
successful only after querying the ledger and binding its recorded payload to
the expected transaction, organization, method, and complete parameter set.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unicodedata

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from cmc_receipts import (ReceiptError, parse_receipt, parse_transaction,
                          require_rejection, require_success)

CONTRACT = "degree_cert_v1"
SCHEMA = "degree-cert/v1"
ROLE_ORGS = {"uni": "wx-org1.chainmaker.org", "edu": "wx-org2.chainmaker.org",
             "query": "wx-org3.chainmaker.org"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_OUTPUT = 8 * 1024 * 1024
_ERRORS = {
    "UNAUTHORIZED": "当前机构无权执行此操作。",
    "CERTIFICATE_ALREADY_EXISTS": "该证书编号已经存证，不能重复签发。",
    "CERTIFICATE_NOT_FOUND": "链上没有找到该证书。",
    "CERTIFICATE_ALREADY_REVOKED": "该证书已经撤销，不能重复撤销。",
    "NOT_INITIALIZED": "链上合约尚未初始化。",
}


class ChainError(Exception):
    """Safe public error; raw CLI output remains in private diagnostic files."""

    def __init__(self, code: str, detail: str, *, uncertain: bool = False,
                 tx_id: str | None = None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.uncertain = uncertain
        self.tx_id = tx_id


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_json(path: Path, limit: int = 65536):
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("configuration exceeds size limit")
    return json.loads(raw, object_pairs_hook=_strict_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))


class ChainClient:
    cmc = Path("/home/wsc/chainmaker-go/tools/cmc/cmc")

    def __init__(self, project_dir: Path):
        self.project = Path(project_dir).resolve()
        expected = {"chain_id": "chain1", "contract_name": CONTRACT, "version": "1.0",
                    "runtime_type": "DOCKER_GO", "schema_version": SCHEMA,
                    "issuer_org": ROLE_ORGS["uni"], "regulator_org": ROLE_ORGS["edu"],
                    "acceptance": "stage05-complete"}
        try:
            deployment = _load_json(self.project / "instance/chain-deployment.json")
            if not isinstance(deployment, dict) or any(
                    type(deployment.get(key)) is not type(value) or deployment.get(key) != value
                    for key, value in expected.items()):
                raise ValueError("deployment does not match")
        except (OSError, ValueError, TypeError) as exc:
            raise ChainError("invalid_response", "部署记录缺失或与第五步验收结果不一致，请先完成第五步。") from exc
        self.deployment = deployment
        self.work = self.project / "instance/cmc-work"
        self.logs = self.project / "instance/chain-calls"
        try:
            self.work.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.logs.chmod(0o700)
        except OSError as exc:
            raise ChainError("unavailable", "无法创建链调用记录目录，请检查项目目录权限。") from exc

    def _flags(self, role):
        if role not in ROLE_ORGS:
            raise ChainError("mismatch", "链调用角色无效。")
        config = self.project / f"config/sdk-{role}.yml"
        if not config.is_file():
            raise ChainError("unavailable", "机构身份配置缺失，请检查第四步生成的配置文件。")
        return [f"--sdk-conf-path={config}", "--enable-cert-hash=false"]

    def _command(self, label, args, timeout=35):
        """Keep output out of memory until bounded reading; never expose it in UI."""
        try:
            fd, filename = tempfile.mkstemp(prefix=label + "-", suffix=".txt", dir=self.logs)
            with os.fdopen(fd, "w+b") as output:
                try:
                    completed = subprocess.run([str(self.cmc)] + args, cwd=self.work,
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        timeout=timeout, check=False, shell=False)
                    code = completed.returncode
                except subprocess.TimeoutExpired:
                    code = 124
                except OSError as exc:
                    output.write(b"CMC could not be executed.\n")
                    raise ChainError("unavailable", "无法运行链客户端，请检查 CMC 路径及执行权限。") from exc
                output.flush()
                output.seek(0)
                raw = output.read(_MAX_OUTPUT + 1)
                if len(raw) > _MAX_OUTPUT:
                    output.seek(_MAX_OUTPUT)
                    output.truncate()
                    raise ChainError("invalid_response", "链返回内容超过处理上限，结果尚未确认。")
            if code != 0:
                raise ChainError("unavailable", "链调用未完成，请检查节点状态后重新查询结果。")
            return raw.decode("utf-8", errors="replace"), code
        except ChainError:
            raise
        except OSError as exc:
            raise ChainError("unavailable", "无法保存链调用记录，请检查磁盘空间和项目目录权限。") from exc

    @staticmethod
    def _cert_id(cert_id):
        if not isinstance(cert_id, str) or not _ID.fullmatch(cert_id):
            raise ChainError("mismatch", "证书编号格式无效。")

    @staticmethod
    def _hash(content_hash):
        if not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash):
            raise ChainError("mismatch", "证书内容摘要格式无效。")

    @staticmethod
    def _business(receipt, *, write=False, tx_id=None):
        if receipt.chain_code in (0, 4) and receipt.contract_code != 0:
            for tag, detail in _ERRORS.items():
                try:
                    require_rejection(receipt, tag)
                except ReceiptError:
                    continue
                code = "not_found" if tag == "CERTIFICATE_NOT_FOUND" and not write else "rejected"
                raise ChainError(code, detail, tx_id=tx_id)
            raise ChainError("rejected", "链上合约已拒绝该操作，请核对证书信息和操作权限。", tx_id=tx_id)
        try:
            return require_success(receipt)
        except ReceiptError as exc:
            raise ChainError("invalid_response", "链返回内容未能确认业务结果。",
                             uncertain=write, tx_id=tx_id) from exc

    def _read(self, role, method, params):
        args = ["client", "contract", "user", "get", f"--contract-name={CONTRACT}",
                f"--method={method}", "--params=" + json.dumps(params, ensure_ascii=False, separators=(",", ":")),
                "--result-to-string=true"] + self._flags(role)
        raw, code = self._command("read-" + method, args)
        try:
            receipt = parse_receipt(raw, code)
        except ReceiptError as exc:
            raise ChainError("invalid_response", "链查询未返回有效的合约回执。") from exc
        return self._business(receipt)

    def health(self):
        data = self._read("query", "health", {})
        expected = {"contract": "degreecert", "initialized": True, "schema_version": SCHEMA,
                    "issuer_org": ROLE_ORGS["uni"], "regulator_org": ROLE_ORGS["edu"]}
        if any(type(data.get(key)) is not type(value) or data.get(key) != value
               for key, value in expected.items()):
            raise ChainError("mismatch", "链上合约版本或机构配置与当前系统不一致。")
        return data

    @staticmethod
    def _validate_certificate(data, cert_id):
        def txid(value):
            return isinstance(value, str) and bool(_HASH.fullmatch(value))
        def timestamp(value):
            return (isinstance(value, str) and 0 < len(value) <= 20 and value.isascii()
                    and value.isdecimal() and int(value) > 0)
        good = (data.get("schema_version") == SCHEMA and data.get("cert_id") == cert_id
                and isinstance(data.get("content_hash"), str) and _HASH.fullmatch(data["content_hash"])
                and data.get("status") in ("valid", "revoked") and data.get("issuer_org") == ROLE_ORGS["uni"]
                and txid(data.get("issued_txid")) and timestamp(data.get("issued_at")))
        if data.get("status") == "revoked":
            good = good and (data.get("revoked_by_org") == ROLE_ORGS["edu"]
                and txid(data.get("revoked_txid")) and timestamp(data.get("revoked_at"))
                and isinstance(data.get("revoke_reason"), str) and bool(data["revoke_reason"].strip()))
        else:
            good = good and not any(data.get(key) for key in
                ("revoked_by_org", "revoked_txid", "revoked_at", "revoke_reason"))
        if not good:
            raise ChainError("mismatch", "链上证书内容与预期的编号、机构或协议不一致。")
        return data

    def get_certificate(self, cert_id, role="query"):
        self._cert_id(cert_id)
        return self._validate_certificate(self._read(role, "getCertificate", {"cert_id": cert_id}), cert_id)

    def verify_certificate(self, cert_id, content_hash):
        self._cert_id(cert_id)
        self._hash(content_hash)
        data = self._read("query", "verifyCertificate", {"cert_id": cert_id, "content_hash": content_hash})
        good = (data.get("schema_version") == SCHEMA and data.get("cert_id") == cert_id
                and isinstance(data.get("content_hash"), str) and _HASH.fullmatch(data["content_hash"])
                and data.get("status") in ("valid", "revoked")
                and type(data.get("content_match")) is bool and type(data.get("valid")) is bool)
        if not good or data["content_match"] != (data["content_hash"] == content_hash) or (
                data["valid"] != (data["content_match"] and data["status"] == "valid")):
            raise ChainError("mismatch", "链上核验结果与证书摘要或状态不一致。")
        return data

    def _write_args(self, role, method, params, tx_id):
        if {"issueCertificate": "uni", "revokeCertificate": "edu"}.get(method) != role:
            raise ChainError("rejected", "当前角色无权执行此链上操作。", tx_id=tx_id)
        if not isinstance(tx_id, str) or not _HASH.fullmatch(tx_id):
            raise ChainError("mismatch", "交易编号格式无效。")
        expected = {"cert_id", "content_hash"} if method == "issueCertificate" else {"cert_id", "reason"}
        if not isinstance(params, dict) or set(params) != expected or not all(isinstance(v, str) for v in params.values()):
            raise ChainError("mismatch", "链交易参数不完整或包含未知字段。", tx_id=tx_id)
        self._cert_id(params["cert_id"])
        if method == "issueCertificate":
            self._hash(params["content_hash"])
        else:
            reason = params["reason"]
            if not reason.strip() or len(reason) > 256 or any(unicodedata.category(c) == "Cc" for c in reason):
                raise ChainError("mismatch", "撤销原因应为 1 至 256 个字符，且不能包含控制字符。", tx_id=tx_id)

    def _reserve_submission(self, role, method, params, tx_id):
        """An atomic durable marker prevents accidental duplicate HTTP submission."""
        marker = self.logs / ("submitted-" + tx_id + ".json")
        record = {"role": role, "method": method, "params": params, "tx_id": tx_id}
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                if _load_json(marker) != record:
                    raise ValueError("submission mismatch")
            except (OSError, ValueError, TypeError) as exc:
                raise ChainError("mismatch", "该交易编号已关联其他参数，已停止再次提交。",
                                 uncertain=True, tx_id=tx_id) from exc
            return False
        except OSError as exc:
            raise ChainError("unavailable", "无法保存提交记录，尚未发送交易。", tx_id=tx_id) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                json.dump(record, output, ensure_ascii=False, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
        except OSError as exc:
            raise ChainError("unavailable", "无法保存提交记录，尚未发送交易。", tx_id=tx_id) from exc
        return True

    def submit(self, role, method, params, tx_id):
        self._write_args(role, method, params, tx_id)
        args = ["client", "contract", "user", "invoke", f"--contract-name={CONTRACT}", f"--method={method}",
                f"--tx-id={tx_id}", "--sync-result=true", "--result-to-string=true",
                "--params=" + json.dumps(params, ensure_ascii=False, separators=(",", ":"))] + self._flags(role)
        if self._reserve_submission(role, method, params, tx_id):
            try:
                self._command("invoke-" + method, args, timeout=75)
            except ChainError:
                # A timeout or CLI error may occur after a node committed the tx.
                # Only the independent ledger query below resolves that outcome.
                pass
        return self.transaction(role, method, params, tx_id)

    def transaction(self, role, method, params, tx_id):
        self._write_args(role, method, params, tx_id)
        try:
            raw, code = self._command("transaction", ["query", "tx", tx_id, "--chain-id=chain1",
                "--truncate-value=false"] + self._flags("query"))
            try:
                receipt = parse_transaction(raw, code, tx_id=tx_id, contract_name=CONTRACT,
                    method=method, params=params, expected_sender_org=ROLE_ORGS[role])
            except ReceiptError as exc:
                mismatch = "不一致" in str(exc)
                raise ChainError("mismatch" if mismatch else "invalid_response",
                    "交易记录与本次操作不一致，结果尚未确认。" if mismatch else
                    "尚未查询到可验证的链上交易，请稍后重新查询，不要重复提交。",
                    uncertain=True, tx_id=tx_id) from exc
            data = self._business(receipt, write=True, tx_id=tx_id)
            self._validate_certificate(data, params["cert_id"])
            good = (data["status"] == "valid" and data["issued_txid"] == tx_id
                    and data["content_hash"] == params["content_hash"]) if method == "issueCertificate" else (
                    data["status"] == "revoked" and data["revoked_txid"] == tx_id
                    and data["revoke_reason"] == params["reason"])
            if not good:
                raise ChainError("mismatch", "交易回执中的证书内容与本次操作不一致。",
                                 uncertain=True, tx_id=tx_id)
            return data
        except ChainError as exc:
            # A persisted, explicit contract rejection is final. Everything else
            # must remain pending because a write may already be on the ledger.
            if exc.code != "rejected":
                exc.uncertain = True
            exc.tx_id = tx_id
            raise
