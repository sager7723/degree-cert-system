import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from degreeapp.domain import (FIELDS, SCHEMA, ValidationError, canonical_document,
                              canonical_json, content_hash, revocation_reason)


def document(cert_id="WEB_TEST_A", **changes):
    value = {"schema_version": SCHEMA, "cert_id": cert_id, "student_id": "DEMO-STUDENT-001",
             "student_name": "演示同学", "school_name": "演示大学", "major": "计算机科学与技术",
             "degree": "学士", "graduation_date": "2026-06-30"}
    value.update(changes)
    return value


class DomainTests(unittest.TestCase):
    def test_hash_matches_user_verified_stage05_document(self):
        original = document("DEMO_20260927083846_c03f4591_A")
        self.assertEqual(content_hash(canonical_document(original)),
                         "7ad26c073277877416570acc258756ee1740492729f36840d3015e67f8898750")

    def test_field_order_and_nfc_whitespace_normalization_are_stable(self):
        source = document(student_name="Jos\u00e9")
        modified = dict(reversed(list(source.items())))
        modified["student_name"] = " Jose\u0301 "
        self.assertEqual(content_hash(canonical_document(source)), content_hash(canonical_document(modified)))

    def test_every_business_field_is_bound_to_hash(self):
        original = document()
        changes = {"cert_id": "WEB_OTHER", "student_id": "OTHER-STUDENT", "student_name": "另一位同学",
                   "school_name": "另一所学校", "major": "软件工程", "degree": "硕士", "graduation_date": "2028-06-30"}
        for field in FIELDS:
            with self.subTest(field=field):
                altered = dict(original, **{field: changes[field]})
                self.assertNotEqual(content_hash(canonical_document(original)), content_hash(canonical_document(altered)))

    def test_invalid_schema_date_identifiers_and_control_characters_rejected(self):
        for field, value in (("schema_version", "degree-cert/v2"), ("graduation_date", "2026-02-29"),
                ("graduation_date", "1899-01-01"), ("graduation_date", "20260630"), ("cert_id", "../SECRET"),
                ("student_id", "有空格 学号"), ("student_name", "学生\x00姓名"), ("student_name", 42),
                ("school_name", "学校\u200b名称"), ("degree", "未知学位")):
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError) as caught:
                canonical_document(document(**{field: value}))
            self.assertIn(field, caught.exception.errors)

    def test_valid_leap_date_and_missing_fields(self):
        self.assertEqual(canonical_document(document(graduation_date="2024-02-29"))["graduation_date"], "2024-02-29")
        with self.assertRaises(ValidationError) as caught:
            canonical_document({})
        self.assertEqual(set(caught.exception.errors), set(FIELDS))

    def test_canonical_export_roundtrip_has_same_digest(self):
        source = canonical_document(document())
        exported = canonical_json(source)
        self.assertIn("演示同学", exported)
        self.assertEqual(hashlib.sha256(exported.encode("utf-8")).hexdigest(), content_hash(source))
        self.assertEqual(content_hash(canonical_document(json.loads(exported))), content_hash(source))

    def test_revocation_reason_has_unicode_normalization_and_strict_bounds(self):
        self.assertEqual(revocation_reason("  教学演示  "), "教学演示")
        self.assertEqual(len(revocation_reason("字" * 256)), 256)
        for value in ("", "   ", "字" * 257, "原因\n换行", "隐藏\u200b字符", None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                revocation_reason(value)


if __name__ == "__main__":
    unittest.main()
