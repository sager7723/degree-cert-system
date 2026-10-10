"""CMC boundary tests: all fake ledger responses stay inside test fixtures."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from degreeapp.chain import ChainClient, ChainError, CONTRACT, ROLE_ORGS, SCHEMA

TXID = "a" * 64
HASH = "b" * 64
PARAMS = {"cert_id": "CERT_TEST_A", "content_hash": HASH}
DEPLOYMENT = {"chain_id": "chain1", "contract_name": CONTRACT, "version": "1.0",
    "runtime_type": "DOCKER_GO", "schema_version": SCHEMA, "issuer_org": ROLE_ORGS["uni"],
    "regulator_org": ROLE_ORGS["edu"], "acceptance": "stage05-complete"}


def encoded(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def certificate(**overrides):
    result = {"schema_version": SCHEMA, "cert_id": PARAMS["cert_id"], "content_hash": HASH,
        "status": "valid", "issuer_org": ROLE_ORGS["uni"], "issued_txid": TXID, "issued_at": "1790498330"}
    result.update(overrides)
    return result


def response(data=None, rejection=None):
    if rejection:
        return {"code": 4, "contract_result": {"code": 1, "message": "Fail", "result": encoded(rejection)}}
    return {"contract_result": {"result": encoded({"ok": True, "data": data or certificate()})}}


def transaction(data=None, rejection=None, *, role="uni", method="issueCertificate", params=None):
    return {"block_height": 42, "transaction": {
        "payload": {"chain_id": "chain1", "contract_name": CONTRACT, "method": method,
            "tx_id": TXID, "parameters": [{"key": k, "value": encoded(v)} for k, v in (params or PARAMS).items()]},
        "sender": {"signer": {"org_id": ROLE_ORGS[role]}},
        "result": response(data, rejection)}}


class ChainClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / "instance").mkdir()
        (self.project / "config").mkdir()
        self.manifest = self.project / "instance/chain-deployment.json"
        self.manifest.write_text(json.dumps(DEPLOYMENT), encoding="utf-8")
        for role in ROLE_ORGS:
            (self.project / f"config/sdk-{role}.yml").write_text("fixture\n", encoding="utf-8")
        self.client = ChainClient(self.project)
        self.commands = []

    def runner(self, outputs):
        remaining = iter(outputs)
        def execute(args, **kwargs):
            self.commands.append(args)
            self.assertIs(kwargs["shell"], False)
            self.assertEqual(kwargs["cwd"], self.project / "instance/cmc-work")
            self.assertEqual(args[0], str(Path("/home/wsc/chainmaker-go/tools/cmc/cmc")))
            value = next(remaining)
            if isinstance(value, Exception):
                raise value
            code = 0
            if isinstance(value, tuple):
                value, code = value
            if not isinstance(value, str):
                value = json.dumps(value)
            kwargs["stdout"].write(value.encode("utf-8"))
            return SimpleNamespace(returncode=code)
        return patch("degreeapp.chain.subprocess.run", side_effect=execute)

    def assert_chain_error(self, callback, code, *, uncertain=False):
        with self.assertRaises(ChainError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.uncertain, uncertain)
        return caught.exception

    def test_manifest_requires_completed_matching_deployment(self):
        for key, value in (("acceptance", "running"), ("runtime_type", "GO"),
                           ("issuer_org", ROLE_ORGS["query"]), ("version", 1.0)):
            with self.subTest(key=key):
                invalid = dict(DEPLOYMENT, **{key: value})
                self.manifest.write_text(json.dumps(invalid), encoding="utf-8")
                self.assert_chain_error(lambda: ChainClient(self.project), "invalid_response")

    def test_duplicate_manifest_fields_rejected(self):
        self.manifest.write_text(json.dumps(DEPLOYMENT)[:-1] + ',"acceptance":"stage05-complete"}', encoding="utf-8")
        self.assert_chain_error(lambda: ChainClient(self.project), "invalid_response")

    def test_health_verifies_actual_contract_organizations(self):
        health = {"contract": "degreecert", "initialized": True, "schema_version": SCHEMA,
                  "issuer_org": ROLE_ORGS["uni"], "regulator_org": ROLE_ORGS["edu"]}
        with self.runner([response(health), response(dict(health, issuer_org=ROLE_ORGS["query"]))]):
            self.assertEqual(self.client.health(), health)
            self.assert_chain_error(self.client.health, "mismatch")

    def test_read_certificate_is_live_and_validated(self):
        with self.runner([response(), response(certificate(cert_id="WRONG"))]):
            self.assertEqual(self.client.get_certificate(PARAMS["cert_id"]), certificate())
            self.assert_chain_error(lambda: self.client.get_certificate(PARAMS["cert_id"]), "mismatch")
        self.assertEqual(len(self.commands), 2)

    def test_missing_certificate_decodes_error_result_not_fail_message(self):
        with self.runner([response(rejection="CERTIFICATE_NOT_FOUND")]):
            error = self.assert_chain_error(lambda: self.client.get_certificate(PARAMS["cert_id"]), "not_found")
            self.assertIn("没有找到", str(error))

    def test_exit_error_and_stdout_success_do_not_prove_read_success(self):
        with self.runner([(response(), 1)]):
            self.assert_chain_error(lambda: self.client.get_certificate(PARAMS["cert_id"]), "unavailable")

    def test_bare_business_json_does_not_prove_contract_execution(self):
        with self.runner([{"ok": True, "data": certificate()}]):
            self.assert_chain_error(lambda: self.client.get_certificate(PARAMS["cert_id"]), "invalid_response")

    def test_verify_keeps_match_and_revoked_validity_distinct(self):
        data = {"schema_version": SCHEMA, "cert_id": PARAMS["cert_id"], "content_hash": HASH,
                "status": "revoked", "content_match": True, "valid": False}
        with self.runner([response(data), response(dict(data, valid=True))]):
            result = self.client.verify_certificate(PARAMS["cert_id"], HASH)
            self.assertTrue(result["content_match"])
            self.assertFalse(result["valid"])
            self.assert_chain_error(lambda: self.client.verify_certificate(PARAMS["cert_id"], HASH), "mismatch")

    def test_verify_rejects_hash_result_inconsistency(self):
        data = {"schema_version": SCHEMA, "cert_id": PARAMS["cert_id"], "content_hash": "c" * 64,
                "status": "valid", "content_match": True, "valid": True}
        with self.runner([response(data)]):
            self.assert_chain_error(lambda: self.client.verify_certificate(PARAMS["cert_id"], HASH), "mismatch")

    def test_submit_requires_confirmed_matching_persisted_transaction(self):
        with self.runner(["untrusted invoke text", transaction()]):
            result = self.client.submit("uni", "issueCertificate", PARAMS, TXID)
            self.assertEqual(result["issued_txid"], TXID)
        self.assertIn("--tx-id=" + TXID, self.commands[0])
        self.assertIn("--sync-result=true", self.commands[0])
        self.assertEqual(self.commands[1][1:4], ["query", "tx", TXID])
        self.assertNotIn("sudo", self.commands[0])

    def test_invoke_timeout_reconciles_without_resending(self):
        with self.runner([subprocess.TimeoutExpired("cmc", 75), transaction()]):
            self.assertEqual(self.client.submit("uni", "issueCertificate", PARAMS, TXID), certificate())
        self.assertEqual(sum("invoke" in cmd for cmd in self.commands), 1)

    def test_accepted_invoke_and_failed_tx_query_remain_pending(self):
        with self.runner([response(), ("transaction not found", 1)]):
            error = self.assert_chain_error(lambda: self.client.submit("uni", "issueCertificate", PARAMS, TXID),
                                            "unavailable", uncertain=True)
        self.assertEqual(error.tx_id, TXID)
        self.assertEqual(sum("invoke" in cmd for cmd in self.commands), 1)

    def test_disconnected_tx_query_never_masquerades_as_contract_rejection(self):
        with self.runner(["UNAUTHORIZED: connection failed"]):
            self.assert_chain_error(lambda: self.client.transaction("uni", "issueCertificate", PARAMS, TXID),
                                    "invalid_response", uncertain=True)

    def test_real_persisted_failure_is_final_and_decodes_rejection(self):
        with self.runner([transaction(rejection="CERTIFICATE_ALREADY_EXISTS")]):
            error = self.assert_chain_error(lambda: self.client.transaction("uni", "issueCertificate", PARAMS, TXID),
                                            "rejected")
        self.assertIn("重复签发", error.detail)

    def test_tx_query_binds_sender_method_id_and_every_parameter(self):
        for field in ("sender", "method", "tx_id", "params", "extra_param", "chain"):
            bad = transaction()
            payload = bad["transaction"]["payload"]
            if field == "sender":
                bad["transaction"]["sender"]["signer"]["org_id"] = ROLE_ORGS["query"]
            elif field == "method":
                payload["method"] = "revokeCertificate"
            elif field == "tx_id":
                payload["tx_id"] = "c" * 64
            elif field == "params":
                payload["parameters"][1]["value"] = encoded("d" * 64)
            elif field == "chain":
                payload["chain_id"] = "chain2"
            else:
                payload["parameters"].append({"key": "extra", "value": "x"})
            with self.subTest(field=field), self.runner([bad]):
                self.assert_chain_error(lambda: self.client.transaction("uni", "issueCertificate", PARAMS, TXID),
                                        "mismatch", uncertain=True)

    def test_missing_block_height_never_confirms_write(self):
        bad = transaction()
        bad.pop("block_height")
        with self.runner([bad]):
            self.assert_chain_error(lambda: self.client.transaction("uni", "issueCertificate", PARAMS, TXID),
                                    "invalid_response", uncertain=True)

    def test_receipt_business_data_must_match_requested_issue(self):
        with self.runner([transaction(certificate(content_hash="c" * 64))]):
            self.assert_chain_error(lambda: self.client.transaction("uni", "issueCertificate", PARAMS, TXID),
                                    "mismatch", uncertain=True)

    def test_repeated_submit_is_readonly_even_across_client_restart(self):
        with self.runner(["ignored", transaction(), transaction()]):
            self.client.submit("uni", "issueCertificate", PARAMS, TXID)
            restarted = ChainClient(self.project)
            restarted.submit("uni", "issueCertificate", PARAMS, TXID)
        self.assertEqual(sum("invoke" in cmd for cmd in self.commands), 1)
        self.assertEqual(sum(cmd[1:3] == ["query", "tx"] for cmd in self.commands), 2)

    def test_same_tx_id_with_different_payload_is_never_sent(self):
        with self.runner(["ignored", transaction()]):
            self.client.submit("uni", "issueCertificate", PARAMS, TXID)
            error = self.assert_chain_error(lambda: self.client.submit("uni", "issueCertificate",
                dict(PARAMS, content_hash="c" * 64), TXID), "mismatch", uncertain=True)
        self.assertEqual(error.tx_id, TXID)
        self.assertEqual(len(self.commands), 2)

    def test_adapter_refuses_wrong_write_role_before_command(self):
        with self.runner([]):
            self.assert_chain_error(lambda: self.client.submit("query", "issueCertificate", PARAMS, TXID), "rejected")
        self.assertFalse(self.commands)

    def test_revoke_validates_regulator_txid_and_reason(self):
        params = {"cert_id": PARAMS["cert_id"], "reason": "教学演示：证书已撤销"}
        data = certificate(status="revoked", revoked_by_org=ROLE_ORGS["edu"], revoked_txid=TXID,
                           revoked_at="1790498440", revoke_reason=params["reason"])
        with self.runner([transaction(data, role="edu", method="revokeCertificate", params=params)]):
            result = self.client.transaction("edu", "revokeCertificate", params, TXID)
            self.assertEqual(result["revoke_reason"], params["reason"])

    def test_missing_cmc_is_user_safe_and_recoverable(self):
        with self.runner([FileNotFoundError("SECRET PRIVATE KEY TEXT")]):
            error = self.assert_chain_error(lambda: self.client.get_certificate(PARAMS["cert_id"]), "unavailable")
        self.assertNotIn("SECRET", str(error))

    def test_output_size_limit_never_confirms_partial_json(self):
        with self.runner(["x" * (8 * 1024 * 1024 + 1)]):
            self.assert_chain_error(lambda: self.client.get_certificate(PARAMS["cert_id"]), "invalid_response")
        logs = list((self.project / "instance/chain-calls").glob("read-*.txt"))
        self.assertEqual(logs[0].stat().st_size, 8 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
