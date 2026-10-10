"""Offline parser tests: no keys, no CMC invocation, no transactions."""
import base64
import copy
import json
import unittest

from cmc_receipts import ReceiptError, parse_receipt, parse_transaction, require_rejection, require_success


def b64(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def response(data=None):
    return {"code": 0, "tx_id": "a" * 64,
            "contract_result": {"code": 0, "result": b64({"ok": True, "data": data or {"status": "valid"}})}}


def stored_transaction():
    return {"block_height": 15, "transaction": {
        "payload": {"chain_id": "chain1", "tx_id": "a" * 64, "contract_name": "degree_cert_v1",
                    "method": "issueCertificate", "parameters": [{"key": "cert_id", "value": "REVNTy1B"}]},
        "result": {"code": 0, "contract_result": response()["contract_result"]}}}


def stored_go_rejection():
    """Same receipt layout and error bytes as the user's block-10 transaction."""
    obj = stored_transaction()
    obj["block_height"] = 10
    obj["block_timestamp"] = 1790498330
    obj["transaction"]["result"] = {
        "code": 4, "contract_result": {
            "code": 1, "gas_used": 136, "message": "Fail", "result": "VU5BVVRIT1JJWkVE"}}
    obj["transaction"]["sender"] = {"signer": {"org_id": "wx-org3.chainmaker.org"}}
    return obj


class ReceiptTests(unittest.TestCase):
    def test_base64_and_plain_json_and_direct_payload(self):
        for value in (b64({"ok": True, "data": {"status": "valid"}}),
                      '{"ok":true,"data":{"status":"valid"}}', {"ok": True, "data": {"status": "valid"}}):
            with self.subTest(value=value):
                obj = response()
                obj["contract_result"]["result"] = value
                self.assertEqual(require_success(parse_receipt("CMC banner\n" + json.dumps(obj), 0)), {"status": "valid"})

    def test_protobuf_omitted_zero_fields(self):
        obj = response()
        del obj["code"]
        del obj["contract_result"]["code"]
        self.assertEqual(require_success(parse_receipt(json.dumps(obj), 0))["status"], "valid")

    def test_numeric_and_enum_codes(self):
        for code in (0, "0", "SUCCESS"):
            obj = response()
            obj["code"] = code
            require_success(parse_receipt(json.dumps(obj), 0))

    def test_failure_never_becomes_success(self):
        for field, value in (("code", 4), ("code", "TIMEOUT"), ("contract_code", 1),
                             ("exit", 124), ("code", True), ("code", None)):
            obj, exit_code = response(), 0
            if field == "exit":
                exit_code = value
            elif field == "contract_code":
                obj["contract_result"]["code"] = value
            else:
                obj[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ReceiptError):
                require_success(parse_receipt(json.dumps(obj), exit_code))

    def test_no_payload_or_ack_only(self):
        for obj in ({"code": 0}, {"code": 0, "message": "OK", "tx_id": "a"},
                    {"contract_result": {}}, {"contract_result": {"code": 0, "message": "OK"}},
                    {"ok": True, "data": {}}, {"contract_result": {"code": 0, "result": "not-json"}}):
            with self.subTest(obj=obj), self.assertRaises(ReceiptError):
                require_success(parse_receipt(json.dumps(obj), 0))

    def test_truncated_trailing_multiple_and_duplicate_json(self):
        valid = json.dumps(response())
        for raw in (valid[:-1], valid + "\nerror: failed", valid + valid,
                    '{"code":4,"code":0,"contract_result":{"result":"{}"}}',
                    '{"contract_result":{"result":"{}"},"code":NaN}',
                    "[ERROR] invoke contract failed: UNAUTHORIZED"):
            with self.subTest(raw=raw), self.assertRaises(ReceiptError):
                require_success(parse_receipt(raw, 0))

    def test_native_list_and_metadata(self):
        for data in ([], [{"name": "x", "runtime_type": 6}], {"name": "x", "version": "1.0"}):
            obj = response()
            obj["contract_result"]["result"] = b64(data)
            self.assertEqual(require_success(parse_receipt(json.dumps(obj), 0), require_data=False), data)

    def test_cmc_decoded_result(self):
        obj = response()
        data = {"ok": True, "data": {"status": "valid"}}
        obj["contract_result_result"] = data
        require_success(parse_receipt(json.dumps(obj), 0))
        obj["contract_result_result"] = {"ok": True, "data": {"status": "revoked"}}
        with self.assertRaises(ReceiptError):
            parse_receipt(json.dumps(obj), 0)

    def test_error_envelopes(self):
        for obj in ({"code": 4, "response": response()}, {"error": "bad", "response": response()},
                    dict(response(), error="bad")):
            with self.subTest(obj=obj), self.assertRaises(ReceiptError):
                require_success(parse_receipt(json.dumps(obj), 0))

    def test_actual_business_rejection(self):
        for code in (0, 4, "CONTRACT_FAIL"):
            obj = {"code": code, "contract_result": {"code": 1, "message": "UNAUTHORIZED"}}
            require_rejection(parse_receipt(json.dumps(obj), 0), "UNAUTHORIZED")

    def test_transport_and_other_errors_not_expected_rejection(self):
        original = {"code": 4, "contract_result": {"code": 1, "message": "UNAUTHORIZED"}}
        cases = [(original, 1), (dict(original, code=2), 0),
                 ({"code": 4, "contract_result": {"code": 1, "message": "NOT_UNAUTHORIZED"}}, 0),
                 ({"code": 4, "contract_result": {"code": 1, "message": "INVALID_CERT_ID"}}, 0),
                 (response(), 0)]
        for obj, code in cases:
            with self.subTest(obj=obj), self.assertRaises(ReceiptError):
                require_rejection(parse_receipt(json.dumps(obj), code), "UNAUTHORIZED")

    def test_go_error_result_plain_base64_and_matching_message(self):
        for result in ("UNAUTHORIZED", "VU5BVVRIT1JJWkVE", '"UNAUTHORIZED"'):
            for message in ("", "Fail", "UNAUTHORIZED", "contract failed: UNAUTHORIZED"):
                obj = {"code": 4, "contract_result": {"code": 1, "message": message, "result": result}}
                with self.subTest(result=result, message=message):
                    receipt = parse_receipt(json.dumps(obj), 0)
                    self.assertEqual(receipt.data, "UNAUTHORIZED")
                    require_rejection(receipt, "UNAUTHORIZED")
                    with self.assertRaisesRegex(ReceiptError, "UNAUTHORIZED"):
                        require_success(receipt)

    def test_go_error_wrong_tag_bad_encoding_and_non_error_payload(self):
        for result in ("INVALID_CERT_ID", "SU5WQUxJRF9DRVJUX0lE", "NOT_UNAUTHORIZED",
                       "VU5BVVRIT1JJWkVE!", "VU5BVVRIT1JJWkV", "/w==", "",
                       "UNAUTHORIZED INVALID_CERT_ID", {"ok": True, "data": {}}, ["UNAUTHORIZED"]):
            for message in ("Fail", "UNAUTHORIZED"):
                # An empty result is absent evidence; an explicit error message
                # remains a supported receipt layout in that one case.
                if result == "" and message == "UNAUTHORIZED":
                    continue
                obj = {"code": 4, "contract_result": {"code": 1, "message": message, "result": result}}
                with self.subTest(result=result, message=message), self.assertRaises(ReceiptError):
                    require_rejection(parse_receipt(json.dumps(obj), 0), "UNAUTHORIZED")

    def test_go_error_cannot_override_transport_or_success_status(self):
        for chain_code, contract_code, exit_code in ((0, 0, 0), (4, 0, 0), (2, 1, 0),
                                                    ("TIMEOUT", 1, 0), (4, 1, 124)):
            obj = {"code": chain_code, "contract_result": {
                "code": contract_code, "message": "Fail", "result": "VU5BVVRIT1JJWkVE"}}
            with self.subTest(status=(chain_code, contract_code, exit_code)), self.assertRaises(ReceiptError):
                require_rejection(parse_receipt(json.dumps(obj), exit_code), "UNAUTHORIZED")

    def test_go_error_result_message_conflict(self):
        for message in ("INVALID_CERT_ID", "contract failed: INVALID_CERT_ID", "Success",
                        "UNAUTHORIZED and INVALID_CERT_ID"):
            obj = {"code": 4, "contract_result": {
                "code": 1, "message": message, "result": "VU5BVVRIT1JJWkVE"}}
            with self.subTest(message=message), self.assertRaises(ReceiptError):
                require_rejection(parse_receipt(json.dumps(obj), 0), "UNAUTHORIZED")

    def test_error_result_matches_cmc_decoded_alternative(self):
        obj = {"code": 4, "contract_result": {
            "code": 1, "message": "Fail", "result": "VU5BVVRIT1JJWkVE"},
            "contract_result_result": "UNAUTHORIZED"}
        require_rejection(parse_receipt(json.dumps(obj), 0), "UNAUTHORIZED")
        obj["contract_result_result"] = "INVALID_CERT_ID"
        with self.assertRaises(ReceiptError):
            parse_receipt(json.dumps(obj), 0)


class TransactionTests(unittest.TestCase):
    def parse(self, obj, exit_code=0, expected_sender_org=None):
        return parse_transaction(json.dumps(obj), exit_code, tx_id="a" * 64,
                                 contract_name="degree_cert_v1", method="issueCertificate", params={"cert_id": "DEMO-A"},
                                 expected_sender_org=expected_sender_org)

    def test_persisted_success(self):
        self.assertEqual(require_success(self.parse(stored_transaction())), {"status": "valid"})

    def test_binds_payload(self):
        for field, value in (("tx_id", "b" * 64), ("chain_id", "chain2"), ("contract_name", "other"),
                             ("method", "revokeCertificate"), ("parameters", []), ("tx_type", 1)):
            obj = stored_transaction()
            obj["transaction"]["payload"][field] = value
            with self.subTest(field=field), self.assertRaises(ReceiptError):
                self.parse(obj)

    def test_requires_block_and_result(self):
        for field, value in (("block_height", None), ("block_height", 0), ("block_height", True), ("transaction", {})):
            obj = stored_transaction()
            obj[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ReceiptError):
                self.parse(obj)

    def test_persisted_rejection_after_text_only_invoke_error(self):
        obj = stored_transaction()
        obj["transaction"]["result"] = {"code": 4, "contract_result": {"code": 1, "message": "contract failed: UNAUTHORIZED"}}
        require_rejection(self.parse(obj), "UNAUTHORIZED")
        with self.assertRaises(ReceiptError):
            require_success(self.parse(obj))

    def test_actual_docker_go_persisted_rejection(self):
        obj = stored_go_rejection()
        tx_id = "692c53c0beaa4c38b936564bdf4626343ef49a4f794d45c8b6a6be72bbac9dba"
        params = {"cert_id": "DEMO_20260927083846_c03f4591_A",
                  "content_hash": "7ad26c073277877416570acc258756ee1740492729f36840d3015e67f8898750"}
        obj["transaction"]["payload"]["tx_id"] = tx_id
        obj["transaction"]["payload"]["parameters"] = [
            {"key": "cert_id", "value": "REVNT18yMDI2MDkyNzA4Mzg0Nl9jMDNmNDU5MV9B"},
            {"key": "content_hash", "value": "N2FkMjZjMDczMjc3ODc3NDE2NTcwYWNjMjU4NzU2ZWUxNzQwNDkyNzI5ZjM2ODQwZDMwMTVlNjdmODg5ODc1MA=="}]
        receipt = parse_transaction(json.dumps(obj), 0, tx_id=tx_id, contract_name="degree_cert_v1",
                                    method="issueCertificate", params=params,
                                    expected_sender_org="wx-org3.chainmaker.org")
        self.assertEqual(receipt.data, "UNAUTHORIZED")
        require_rejection(receipt, "UNAUTHORIZED")
        with self.assertRaisesRegex(ReceiptError, "UNAUTHORIZED"):
            require_success(receipt)

    def test_sender_org_is_bound_when_requested(self):
        for org in (None, "wx-org1.chainmaker.org", ""):
            obj = stored_go_rejection()
            obj["transaction"]["sender"]["signer"]["org_id"] = org
            with self.subTest(org=org), self.assertRaises(ReceiptError):
                self.parse(obj, expected_sender_org="wx-org3.chainmaker.org")
        for sender in (None, {}, {"signer": {}}, {"signer": "invalid"}):
            obj = stored_go_rejection()
            obj["transaction"]["sender"] = sender
            with self.subTest(sender=sender), self.assertRaises(ReceiptError):
                self.parse(obj, expected_sender_org="wx-org3.chainmaker.org")
        # Existing callers without an expected organization stay supported.
        self.parse(stored_transaction())

    def test_sender_org_camel_case_and_conflict(self):
        obj = stored_go_rejection()
        obj["transaction"]["sender"]["signer"] = {"orgId": "wx-org3.chainmaker.org"}
        self.parse(obj, expected_sender_org="wx-org3.chainmaker.org")
        obj["transaction"]["sender"]["signer"]["org_id"] = "wx-org1.chainmaker.org"
        with self.assertRaises(ReceiptError):
            self.parse(obj, expected_sender_org="wx-org3.chainmaker.org")

    def test_extra_parameter_rejected(self):
        obj = stored_transaction()
        obj["transaction"]["payload"]["parameters"].append({"key": "extra", "value": "x"})
        with self.assertRaises(ReceiptError):
            self.parse(obj)

    def test_duplicate_parameter_rejected(self):
        obj = stored_transaction()
        obj["transaction"]["payload"]["parameters"] *= 2
        with self.assertRaises(ReceiptError):
            self.parse(obj)

    def test_query_failure_not_evidence(self):
        with self.assertRaises(ReceiptError):
            self.parse(stored_transaction(), 1)
        obj = dict(stored_transaction(), error="unavailable")
        with self.assertRaises(ReceiptError):
            self.parse(obj)


if __name__ == "__main__":
    unittest.main()
