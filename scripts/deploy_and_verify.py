#!/usr/bin/env python3
"""Stage 05: conservative, journaled CMC deployment and real-chain acceptance.

Only create uses sudo to read the existing admin signing keys. Business calls
run as the ordinary user. A pending/failed run is never blindly resubmitted.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from cmc_receipts import ReceiptError, parse_receipt, parse_transaction, require_rejection, require_success
from summarize_chain_probe import assess_output

CONTRACT = "degree_cert_v1"
VERSION = "1.0"
# v2.4.0 contract management accepts DOCKER_GO and detects the SDK marker to
# route internally to GO. Passing GO directly is rejected by that API.
RUNTIME = "DOCKER_GO"
SCHEMA = "degree-cert/v1"
ISSUER = "wx-org1.chainmaker.org"
REGULATOR = "wx-org2.chainmaker.org"
ROLE_ORGS = {"uni": ISSUER, "edu": REGULATOR, "query": "wx-org3.chainmaker.org"}
CHAIN_DIR = Path("/home/wsc/chainmaker-go")
# The user supplied these digests after the successful Ubuntu stage-04 build.
EXPECTED_BINARY_SHA256 = "1cf9103691e0e9f68c0e3ca5424743b313f3c524e953316e930393198136f618"
EXPECTED_ARCHIVE_SHA256 = "fe9fad7b1ab991ff4e0f260672204c0839c590efdee7150f30138597d798ee30"
# Recovery is intentionally scoped to the exact deployment and rejection the
# user reported, not a general permission to retry arbitrary interrupted writes.
KNOWN_DEPLOYMENT_TXID = "18d920bdcadda8d3cad0992125931f886f4eb1ee0578400d9ff83e728a4987ca"
KNOWN_REJECTION_TXID = "692c53c0beaa4c38b936564bdf4626343ef49a4f794d45c8b6a6be72bbac9dba"
KNOWN_CERT_A = "DEMO_20260927083846_c03f4591_A"
KNOWN_HASH_A = "7ad26c073277877416570acc258756ee1740492729f36840d3015e67f8898750"


class StageError(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise StageError(message)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path, value):
    """Flush mutation intent to disk before sending anything to the chain."""
    check(not path.is_symlink(), f"拒绝写入符号链接：{path}")
    fd, name = tempfile.mkstemp(prefix=".stage05-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        if sys.platform == "linux":
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def demo_document(cert_id, suffix):
    # Fictitious content, retained off-chain; only its hash is submitted.
    return {
        "schema_version": SCHEMA,
        "cert_id": cert_id,
        "student_id": "DEMO-STUDENT-001",
        "student_name": "演示同学",
        "school_name": "演示大学",
        "major": "计算机科学与技术" if suffix == "A" else "软件工程",
        "degree": "学士" if suffix == "A" else "硕士",
        "graduation_date": "2026-06-30" if suffix == "A" else "2028-06-30",
    }


def content_hash(document):
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class Stage:
    def __init__(self, project, report, *, resume=False):
        self.project = project
        self.report = report
        self.cmc = CHAIN_DIR / "tools/cmc/cmc"
        self.work = project / "instance/cmc-work"
        self.state_path = project / "instance/stage05-state.json"
        self.state = None
        self.counter = 0
        self.last_raw = None
        self.last_label = ""
        self.string_flags = {}
        self.resume_requested = resume
        self.reconciled_first = None

    def command(self, label, args, *, root=False, timeout=55):
        self.counter += 1
        self.last_label = label
        safe_label = re.sub(r"[^A-Za-z0-9_-]", "_", label)
        raw_path = self.report / f"{self.counter:02d}-{safe_label}.txt"
        self.last_raw = raw_path
        cwd = self.work
        if root:
            cwd = self.report / "deployment-work"
            cwd.mkdir(exist_ok=True, mode=0o700)
        command = (["sudo", "-n"] if root else []) + [str(self.cmc)] + args
        # No shell interpolation and no output of certificate/key contents.
        with raw_path.open("wb") as output:
            try:
                result = subprocess.run(command, cwd=cwd, stdout=output,
                                        stderr=subprocess.STDOUT, timeout=timeout,
                                        stdin=subprocess.DEVNULL, check=False)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
        raw = raw_path.read_text(encoding="utf-8", errors="replace")
        atomic_json(raw_path.with_suffix(".meta.json"),
                    {"label": label, "exit_code": code, "time": utc_now(), "sudo": root})
        return raw, code

    def base_flags(self, role):
        return [f"--sdk-conf-path={self.project / ('config/sdk-' + role + '.yml')}",
                "--enable-cert-hash=false"]

    def preflight(self):
        check(self.cmc.is_file() and os.access(self.cmc, os.X_OK), "没有找到可执行的 CMC。")
        check(self.work.is_dir(), "缺少普通用户 CMC 工作目录，请核对第四步。")
        for role in ("uni", "edu", "query"):
            check((self.project / f"config/sdk-{role}.yml").is_file(), f"缺少 {role} 身份配置。")
        for action, flags in {
            "get": ["contract-name", "method", "params", "sdk-conf-path", "enable-cert-hash"],
            "invoke": ["contract-name", "method", "params", "sync-result", "tx-id", "sdk-conf-path", "enable-cert-hash"],
            "create": ["contract-name", "runtime-type", "byte-code-path", "version", "params",
                       "admin-key-file-paths", "admin-crt-file-paths", "sync-result", "sdk-conf-path", "enable-cert-hash"],
        }.items():
            raw, code = self.command("help-" + action, ["client", "contract", "user", action, "--help"])
            check(code == 0, f"无法读取 CMC {action} 帮助。")
            missing = [flag for flag in flags if not re.search(r"--" + re.escape(flag) + r"\b", raw)]
            check(not missing, f"本机 CMC {action} 缺少预期参数：{missing}，已保存帮助供核对。")
            self.string_flags[action] = ["--result-to-string=true"] if "--result-to-string" in raw else []
        raw, code = self.command("help-query-tx", ["query", "tx", "--help"])
        check(code == 0 and "--sdk-conf-path" in raw and "--chain-id" in raw,
              "CMC query tx 帮助与预期不一致，尚未发送交易。")
        self.string_flags["tx"] = ["--truncate-value=false"] if "--truncate-value" in raw else []
        for role in ("uni", "edu", "query"):
            raw, code = self.command("block-" + role, ["query", "block-by-height", "--chain-id=chain1",
                "--with-rw-set=false", "--truncate-value=true"] + self.base_flags(role))
            passed, message = assess_output(raw, code, "chain1")
            check(passed, role + "：" + message)
            print(role + "：" + message, flush=True)

    def read(self, label, method, params=None, *, role="query", contract=CONTRACT, native=False):
        args = ["client", "contract", "user", "get", f"--contract-name={contract}", f"--method={method}",
                "--params=" + json.dumps(params or {}, ensure_ascii=False, separators=(",", ":"))]
        raw, code = self.command(label, args + self.base_flags(role) + self.string_flags["get"])
        receipt = parse_receipt(raw, code)
        data = require_success(receipt, require_data=not native)
        return receipt.data if native else data

    def contract_list(self):
        value = self.read("contract-list", "GET_CONTRACT_LIST", contract="CONTRACT_MANAGE", native=True)
        check(isinstance(value, list) and all(isinstance(item, dict) and isinstance(item.get("name"), str)
              for item in value), "合约列表不是预期的 JSON 数组，不能判断是否已部署。")
        return value

    def health(self):
        health = self.read("health", "health")
        expected = {"contract": "degreecert", "initialized": True, "schema_version": SCHEMA,
                    "issuer_org": ISSUER, "regulator_org": REGULATOR}
        check(all(type(health.get(key)) is type(value) and health.get(key) == value
                  for key, value in expected.items()), "合约 health 与本项目的机构或协议版本不一致。")
        print("通过：合约已初始化，签发机构为 org1，监管机构为 org2。", flush=True)

    def metadata(self):
        data = self.read("contract-info", "GET_CONTRACT_INFO", {"CONTRACT_NAME": CONTRACT},
                         contract="CONTRACT_MANAGE", native=True)
        check(isinstance(data, dict), "合约元信息未返回对象。")
        check(data.get("name") == CONTRACT and data.get("version") == VERSION, "合约名称或版本不匹配。")
        runtime = data.get("runtime_type", data.get("runtimeType"))
        check(type(runtime) in (int, str) and runtime in (6, "6", "DOCKER_GO", 8, "8", "GO"),
              "链上合约运行时不是预期的 Go 合约类型。")
        # Common.Contract.status is an enum whose omitted protobuf default is NORMAL=0.
        status = data.get("status", 0)
        check(type(status) in (int, str) and status in (0, "0", "NORMAL"), "链上合约当前不是正常可调用状态。")
        atomic_json(self.report / "contract-info-summary.json",
                    {key: data.get(key) for key in ("name", "version", "runtime_type", "runtimeType", "status")})
        print(f"通过：链上合约 {CONTRACT}，版本 {VERSION}，公开运行时字段 {runtime}。", flush=True)

    def save(self):
        self.state["updated_at"] = utc_now()
        atomic_json(self.state_path, self.state)

    def begin_write(self, label, detail):
        check(self.state.get("pending") is None, "尚有未确认操作，禁止发送新交易。")
        self.state["pending"] = {"label": label, "detail": detail, "report_dir": str(self.report), "time": utc_now()}
        self.save()

    def finish_write(self, label, receipt, expected=None):
        self.state["steps"].append({"label": label, "tx_id": receipt.tx_id,
                                    "expected_rejection": expected, "verified_at": utc_now()})
        self.state["pending"] = None
        self.save()

    def archive(self):
        pointer = self.project / "contracts/degreecert/latest-build.txt"
        check(pointer.is_file(), "缺少第四步 latest-build.txt，请反馈报告。")
        path = Path(pointer.read_text(encoding="utf-8").strip()).resolve()
        source_dir = (self.project / "contracts/degreecert").resolve()
        check(path.parent.parent == source_dir and path.parent.name.startswith("build-")
              and path.name == CONTRACT + ".7z", "构建包路径不在本项目的预期构建目录。")
        check(path.is_file() and path.with_suffix("").is_file(), "合约构建产物缺失。")
        check(digest(path) == EXPECTED_ARCHIVE_SHA256, "7z 校验值与第四步已反馈的成功构建不一致，停止部署。")
        check(digest(path.with_suffix("")) == EXPECTED_BINARY_SHA256, "合约二进制校验值与第四步报告不一致。")
        sdk_marker = b"Contract_SDK_Version:fc5101a7f55d71e234242163fd1bdfaa4fdea7437bf161f7e1cf7c49e57580a2"
        check(sdk_marker in path.with_suffix("").read_bytes(), "二进制缺少新版 Go 合约 SDK 标记。")
        print("通过：部署包及二进制 SHA-256 与第四步报告一致。", flush=True)
        return path

    def admin_paths(self):
        result = subprocess.run(["sudo", "-n", "true"], stdin=subprocess.DEVNULL, check=False)
        check(result.returncode == 0, "需要读取原链管理员签名密钥，请在当前终端先执行 sudo -v。")
        keys, certs = [], []
        for number in range(1, 5):
            directory = CHAIN_DIR / f"tools/cmc/testdata/crypto-config/wx-org{number}.chainmaker.org/user/admin1"
            key, cert = directory / "admin1.sign.key", directory / "admin1.sign.crt"
            for path in (key, cert):
                result = subprocess.run(["sudo", "-n", "test", "-r", str(path)], check=False)
                check(result.returncode == 0, f"原链管理员签名文件不可读：{path}")
            keys.append(str(key))
            certs.append(str(cert))
        return ",".join(keys), ",".join(certs)

    def deploy(self, archive, keys, certs):
        print("\n正在创建合约：使用四机构管理员签名，等待真实执行回执……", flush=True)
        args = ["client", "contract", "user", "create", f"--contract-name={CONTRACT}",
                f"--runtime-type={RUNTIME}", f"--byte-code-path={archive}", f"--version={VERSION}",
                f"--admin-key-file-paths={keys}", f"--admin-crt-file-paths={certs}", "--sync-result=true",
                "--params=" + json.dumps({"issuer_org": ISSUER, "regulator_org": REGULATOR}, separators=(",", ":"))]
        self.begin_write("create-contract", {"archive_sha256": EXPECTED_ARCHIVE_SHA256, "runtime": RUNTIME})
        raw, code = self.command("create-contract", args + self.base_flags("uni"), root=True, timeout=150)
        receipt = parse_receipt(raw, code)
        require_success(receipt, require_data=False)
        check(bool(receipt.tx_id), "创建回执缺少交易 ID，尚不能确认部署。")
        self.state["deployment_txid"] = receipt.tx_id
        self.save()
        # An SDK acknowledgement alone is insufficient: also query ledger metadata and application state.
        self.metadata()
        self.health()
        self.finish_write("create-contract", receipt)
        print("通过：合约创建与链上读取，部署交易 " + receipt.tx_id, flush=True)

    def transaction(self, label, tx_id, method, params, contract=CONTRACT, *, role="query"):
        raw, code = self.command(label + "-transaction", ["query", "tx", tx_id, "--chain-id=chain1"]
                                 + self.base_flags("query") + self.string_flags["tx"])
        return parse_transaction(raw, code, tx_id=tx_id, contract_name=contract, method=method, params=params,
                                 expected_sender_org=ROLE_ORGS[role])

    def resume_known_rejection(self):
        """Reconcile this one known rejected tx using fresh, read-only evidence."""
        state = self.state
        check(state.get("phase") in ("running", "needs_review"), "当前阶段不属于本补丁可恢复的状态。")
        check(state.get("version") == VERSION and state.get("archive_sha256") == EXPECTED_ARCHIVE_SHA256,
              "运行记录的合约版本或构建哈希不匹配。")
        check(state.get("deployment_txid") == KNOWN_DEPLOYMENT_TXID, "部署交易与已反馈的报告不一致。")
        ids = state.get("cert_ids")
        check(ids == [KNOWN_CERT_A, KNOWN_CERT_A[:-1] + "B"], "演示证书 ID 与这次已确认的运行不一致。")
        documents, hashes = state.get("documents"), state.get("hashes")
        check(isinstance(documents, dict) and isinstance(hashes, dict) and
              set(documents) == set(ids) and set(hashes) == set(ids), "演示全文或哈希记录不完整。")
        for cert_id, letter in zip(ids, ("A", "B")):
            check(documents[cert_id] == demo_document(cert_id, letter) and
                  content_hash(documents[cert_id]) == hashes[cert_id], "演示全文或内容哈希发生变化，停止恢复。")
        check(hashes[KNOWN_CERT_A] == KNOWN_HASH_A, "证书 A 的内容哈希与已反馈的交易不一致。")
        steps = state.get("steps")
        check(isinstance(steps, list) and len(steps) in (1, 2) and all(isinstance(item, dict) for item in steps),
              "已完成步骤不属于本次可恢复的检查点。")
        check(steps[0].get("label") == "create-contract" and steps[0].get("tx_id") == KNOWN_DEPLOYMENT_TXID
              and steps[0].get("expected_rejection") is None, "已完成部署步骤与报告不一致。")
        expected_detail = {"role": "query", "method": "issueCertificate",
                           "params": {"cert_id": KNOWN_CERT_A, "content_hash": KNOWN_HASH_A},
                           "tx_id": KNOWN_REJECTION_TXID, "expected_rejection": "UNAUTHORIZED"}
        if len(steps) == 1:
            pending = state.get("pending")
            check(isinstance(pending, dict) and pending.get("label") == "query-cannot-issue" and
                  pending.get("detail") == expected_detail, "待确认交易与本次已观察到的越权调用不一致。")
        else:
            # Also tolerate interruption immediately after the checkpoint was
            # committed. This still only re-reads the same transaction.
            check(state.get("pending") is None and steps[1].get("label") == "query-cannot-issue" and
                  steps[1].get("tx_id") == KNOWN_REJECTION_TXID and
                  steps[1].get("expected_rejection") == "UNAUTHORIZED", "恢复检查点不一致，停止发送交易。")
        backup = self.report / "state-before-resume.json"
        check(not backup.exists() and not backup.is_symlink(), "恢复前备份已经存在，停止覆盖。")
        # Every invocation has a new report directory; preserve the exact bytes
        # before modifying even the phase or pending fields.
        with backup.open("xb") as handle:
            handle.write(self.state_path.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        print("\n恢复前状态已备份：" + str(backup), flush=True)
        self.preflight()
        self.metadata()
        self.health()
        receipt = self.transaction("reconcile-query-cannot-issue", KNOWN_REJECTION_TXID,
                                   "issueCertificate", expected_detail["params"], role="query")
        require_rejection(receipt, "UNAUTHORIZED")
        if len(steps) == 1:
            self.finish_write("query-cannot-issue", receipt, "UNAUTHORIZED")
        self.state["phase"] = "running"
        self.state.pop("last_error", None)
        self.state.setdefault("recoveries", []).append({"tx_id": receipt.tx_id, "time": utc_now(),
                                                         "backup": str(backup), "report_dir": str(self.report)})
        self.save()
        self.reconciled_first = receipt
        print("恢复确认：已按交易 ID 核对 org3 的原调用，链上明确拒绝 UNAUTHORIZED；继续后续验收。", flush=True)

    def invoke(self, label, role, method, params, *, rejection=None):
        if label == "query-cannot-issue" and self.reconciled_first is not None:
            check(role == "query" and method == "issueCertificate" and rejection == "UNAUTHORIZED" and
                  params == {"cert_id": KNOWN_CERT_A, "content_hash": KNOWN_HASH_A}, "恢复步骤参数不匹配。")
            require_rejection(self.reconciled_first, rejection)
            print("通过：query-cannot-issue（已重新查询原交易，未重发）", flush=True)
            return None
        tx_id = uuid.uuid4().hex + uuid.uuid4().hex
        args = ["client", "contract", "user", "invoke", f"--contract-name={CONTRACT}", f"--method={method}",
                "--params=" + json.dumps(params, ensure_ascii=False, separators=(",", ":")), "--sync-result=true",
                "--tx-id=" + tx_id]
        self.begin_write(label, {"role": role, "method": method, "params": params,
                                 "tx_id": tx_id, "expected_rejection": rejection})
        raw, code = self.command(label, args + self.base_flags(role) + self.string_flags["invoke"], timeout=100)
        # CMC may print a text error on contract failure. Do not interpret it as
        # proof: query the saved transaction ID and validate the persisted call.
        try:
            immediate = parse_receipt(raw, code)
            check(not immediate.tx_id or immediate.tx_id == tx_id, "CMC 回执交易 ID 与发送 ID 不一致。")
        except ReceiptError:
            print("CMC 即时输出未给出完整回执，正在按已保存的交易 ID 查询链上结果。", flush=True)
        receipt = self.transaction(label, tx_id, method, params, role=role)
        if rejection:
            require_rejection(receipt, rejection)
            data = None
        else:
            data = require_success(receipt)
            field = "issued_txid" if method == "issueCertificate" else "revoked_txid"
            check(data.get(field) == receipt.tx_id, "合约返回的业务交易 ID 与交易回执不一致。")
            expected_status = "valid" if method == "issueCertificate" else "revoked"
            self.assert_certificate(data, params["cert_id"], expected_status)
            stored = self.read(label + "-readback", "getCertificate", {"cert_id": params["cert_id"]})
            check(stored == data, "提交回执与重新查询的链上记录不一致，停止后续写入。")
        self.finish_write(label, receipt, rejection)
        print(f"通过：{label}" + (f"（合约明确拒绝：{rejection}）" if rejection else "（链上回读一致）"), flush=True)
        return data

    def assert_certificate(self, data, cert_id, status):
        expected_hash = self.state["hashes"][cert_id]
        expected = {"schema_version": SCHEMA, "cert_id": cert_id, "content_hash": expected_hash,
                    "status": status, "issuer_org": ISSUER}
        check(all(data.get(key) == value for key, value in expected.items()), "证书 ID、内容哈希、状态或机构不符合预期。")
        check(isinstance(data.get("issued_txid"), str) and bool(data["issued_txid"]), "缺少签发交易 ID。")
        check(isinstance(data.get("issued_at"), str) and data["issued_at"].isdigit()
              and int(data["issued_at"]) > 0, "签发时间不是有效的链交易时间。")
        if status == "revoked":
            check(data.get("revoked_by_org") == REGULATOR and bool(data.get("revoked_txid"))
                  and data.get("revoke_reason") == "教学演示：撤销证书 A", "撤销信息不符合预期。")

    def verify(self, label, cert_id, supplied_hash, match, status):
        result = self.read(label, "verifyCertificate", {"cert_id": cert_id, "content_hash": supplied_hash})
        check(result.get("schema_version") == SCHEMA and result.get("cert_id") == cert_id
              and result.get("content_hash") == self.state["hashes"][cert_id], "核验返回的证书或原始哈希不匹配。")
        check(result.get("content_match") is match and result.get("status") == status
              and result.get("valid") is (match and status == "valid"), "核验结果与预期不一致。")
        print("通过：" + label, flush=True)

    def smoke(self):
        a, b = self.state["cert_ids"]
        ha, hb = self.state["hashes"][a], self.state["hashes"][b]
        print("\n开始真实合约业务验收（虚构证书 A、B，同一演示学生）：", flush=True)
        self.invoke("query-cannot-issue", "query", "issueCertificate", {"cert_id": a, "content_hash": ha}, rejection="UNAUTHORIZED")
        self.invoke("regulator-cannot-issue", "edu", "issueCertificate", {"cert_id": a, "content_hash": ha}, rejection="UNAUTHORIZED")
        issued_a = self.invoke("issuer-issues-A", "uni", "issueCertificate", {"cert_id": a, "content_hash": ha})
        issued_b = self.invoke("issuer-issues-B", "uni", "issueCertificate", {"cert_id": b, "content_hash": hb})
        self.state["issued_records"] = {a: issued_a, b: issued_b}
        self.save()
        self.invoke("duplicate-issue-rejected", "uni", "issueCertificate", {"cert_id": a, "content_hash": ha}, rejection="CERTIFICATE_ALREADY_EXISTS")
        self.verify("original-content-valid", a, ha, True, "valid")
        altered = dict(self.state["documents"][a], major="已改动的专业")
        self.verify("altered-content-detected", a, content_hash(altered), False, "valid")
        revoke = {"cert_id": a, "reason": "教学演示：撤销证书 A"}
        self.invoke("issuer-cannot-revoke", "uni", "revokeCertificate", revoke, rejection="UNAUTHORIZED")
        self.invoke("query-cannot-revoke", "query", "revokeCertificate", revoke, rejection="UNAUTHORIZED")
        unchanged = self.read("after-denials-A", "getCertificate", {"cert_id": a})
        check(unchanged == issued_a, "被拒绝的操作改变了证书 A。")
        revoked = self.invoke("regulator-revokes-A", "edu", "revokeCertificate", revoke)
        check(all(revoked.get(key) == issued_a.get(key) for key in ("content_hash", "issued_txid", "issued_at")),
              "撤销改变了原始存证哈希或签发信息。")
        self.invoke("repeat-revoke-rejected", "edu", "revokeCertificate", revoke, rejection="CERTIFICATE_ALREADY_REVOKED")
        self.state["revoked_record"] = revoked
        self.save()
        self.final_reads()

    def final_reads(self):
        a, b = self.state["cert_ids"]
        self.verify("A-original-matches-but-revoked", a, self.state["hashes"][a], True, "revoked")
        self.verify("B-still-valid", b, self.state["hashes"][b], True, "valid")
        final_a = self.read("final-certificate-A", "getCertificate", {"cert_id": a})
        final_b = self.read("final-certificate-B", "getCertificate", {"cert_id": b})
        check(final_a == self.state["revoked_record"], "证书 A 的最终记录与已验证撤销记录不一致。")
        check(final_b == self.state["issued_records"][b], "证书 B 被其他操作影响。")
        atomic_json(self.report / "verified-certificates.json", {"A": final_a, "B": final_b})

    def run(self):
        print("学历证书存证系统 / 第 5 步部署与真实业务验收\n时间：" + utc_now(), flush=True)
        if self.resume_requested:
            print("本次恢复已部署合约的验收：先查证原交易，再继续后续演示；恢复流程使用普通用户身份。", flush=True)
        else:
            print("首次运行创建 degree_cert_v1 并发送演示交易；已完成时只读复核。仅部署使用 sudo。", flush=True)
        subprocess.run(["free", "-h"], check=False)
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            check(isinstance(self.state, dict), "第五步状态文件格式不正确。")
            check(self.state.get("contract") == CONTRACT and self.state.get("runtime") == RUNTIME,
                  "已有第五步状态属于不同合约或运行时，停止。")
            if self.state.get("phase") != "complete":
                check(self.resume_requested, "发现上次未完成的第五步。使用本次修复包并按说明加 --resume 恢复已确认的首次拒绝交易；其他未完成状态仍需核对。")
                self.resume_known_rejection()
                self.smoke()
                self.complete()
                return
            check(self.state.get("pending") is None, "已完成状态仍有待确认操作，请反馈报告。")
            self.preflight()
            self.metadata()
            self.health()
            self.final_reads()
            print("\n第五步曾经完成，本次只读复核通过，未重复签发或撤销。", flush=True)
            return
        check(not self.resume_requested, "恢复需要原有 instance/stage05-state.json；未找到时停止，不重新部署。")
        self.preflight()
        archive = self.archive()
        contracts = self.contract_list()
        check(not any(item["name"] == CONTRACT for item in contracts),
              "链上已有同名合约，但本项目没有部署记录。未覆盖或升级，请反馈报告核对来源。")
        keys, certs = self.admin_paths()
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
        ids = ["DEMO_" + suffix + "_" + letter for letter in ("A", "B")]
        documents = {cert_id: demo_document(cert_id, letter) for cert_id, letter in zip(ids, ("A", "B"))}
        self.state = {"contract": CONTRACT, "runtime": RUNTIME, "version": VERSION, "phase": "running",
                      "created_at": utc_now(), "report_dir": str(self.report), "pending": None, "steps": [],
                      "archive_sha256": EXPECTED_ARCHIVE_SHA256, "cert_ids": ids, "documents": documents,
                      "hashes": {key: content_hash(value) for key, value in documents.items()}}
        self.save()
        atomic_json(self.report / "demo-documents.json", documents)
        self.deploy(archive, keys, certs)
        self.smoke()
        self.complete()

    def complete(self):
        atomic_json(self.project / "instance/chain-deployment.json", {
            "chain_id": "chain1", "contract_name": CONTRACT, "version": VERSION, "runtime_type": RUNTIME,
            "schema_version": SCHEMA, "issuer_org": ISSUER, "regulator_org": REGULATOR,
            "archive_sha256": EXPECTED_ARCHIVE_SHA256, "deployment_txid": self.state["deployment_txid"],
            "verified_at": utc_now(), "report_dir": str(self.report), "acceptance": "stage05-complete"})
        self.state["phase"] = "complete"
        self.save()
        print("\n第五步全部通过：真实合约部署、签发、查询、内容核验、越权拒绝和撤销已完成。", flush=True)
        print("演示证书 A（已撤销）：" + self.state["cert_ids"][0] + "\n演示证书 B（有效）：" + self.state["cert_ids"][1], flush=True)
        print("链上验收记录已保存；下一步接入 Flask 页面与 SQLite，网页尚未完成。", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true", help="核对并恢复已反馈的首次拒绝交易；不重复部署或重发原交易")
    args = parser.parse_args()
    project, report = args.project.resolve(), args.report_dir.resolve()
    if sys.platform != "linux" or os.geteuid() == 0:
        print("请在 Ubuntu 中通过 bash scripts/05-deploy-and-verify.sh 以普通用户执行。", file=sys.stderr)
        return 1
    if project != Path("/home/wsc/degree-cert-system") or report.parent != project / "reports":
        print("项目或报告路径不符合预期。", file=sys.stderr)
        return 1
    os.umask(0o077)
    stage = Stage(project, report, resume=args.resume)
    try:
        stage.run()
        return 0
    except (StageError, ReceiptError, OSError, ValueError, KeyboardInterrupt) as exc:
        print("\n本步未完成：" + str(exc), flush=True)
        print("停止位置：" + stage.last_label, flush=True)
        if isinstance(stage.state, dict) and stage.state.get("phase") != "complete":
            print("首次运行的报告目录：" + str(stage.state.get("report_dir", "未知")), flush=True)
            # During recovery leave the journal at its last durable checkpoint.
            # In particular, failed read-only validation must not alter it.
            if not stage.resume_requested:
                stage.state["phase"] = "needs_review"
                stage.state["last_error"] = str(exc)
                try:
                    stage.save()
                except OSError:
                    pass
        if stage.last_raw and stage.last_raw.is_file():
            print("原始输出文件：" + str(stage.last_raw), flush=True)
            print("最近命令输出（最多 6000 字符）：\n" + stage.last_raw.read_text(encoding="utf-8", errors="replace")[-6000:], flush=True)
        print("请反馈 report.txt。保留 instance/stage05-state.json 和原始回执；未确认前不要手动删除状态文件或重发交易。", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
