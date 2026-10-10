"""Canonical degree documents shared by forms, imports and chain verification."""
from datetime import date
import hashlib
import json
import re
import unicodedata

SCHEMA = "degree-cert/v1"
FIELDS = ("cert_id", "student_id", "student_name", "school_name", "major", "degree", "graduation_date")
LABELS = {"cert_id": "证书编号", "student_id": "学号", "student_name": "学生姓名",
          "school_name": "学校名称", "major": "专业", "degree": "学历 / 学位", "graduation_date": "毕业日期"}
DEGREES = ("专科", "学士", "硕士", "博士")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("；".join(errors.values()))


def canonical_document(values):
    errors, result = {}, {"schema_version": SCHEMA}
    if values.get("schema_version", SCHEMA) != SCHEMA:
        errors["schema_version"] = "不支持的证书格式版本。"
    for field in FIELDS:
        value = values.get(field, "")
        if not isinstance(value, str):
            errors[field] = LABELS[field] + "必须为文本。"
            continue
        value = unicodedata.normalize("NFC", value).strip()
        max_len = {"cert_id": 64, "student_id": 64, "student_name": 50, "school_name": 100,
                   "major": 100, "degree": 10, "graduation_date": 10}[field]
        if not value or len(value) > max_len or any(unicodedata.category(c).startswith("C") for c in value):
            errors[field] = f"{LABELS[field]}需填写 1–{max_len} 个可见字符。"
        elif field in ("cert_id", "student_id") and not ID.fullmatch(value):
            errors[field] = LABELS[field] + "仅支持英文字母、数字、下划线和连字符，开头须为字母或数字。"
        elif field == "degree" and value not in DEGREES:
            errors[field] = "请选择专科、学士、硕士或博士。"
        elif field == "graduation_date":
            try:
                parsed = date.fromisoformat(value)
                if parsed.isoformat() != value or not 1900 <= parsed.year <= 2100:
                    raise ValueError()
            except ValueError:
                errors[field] = "毕业日期须为 1900–2100 年间有效的 YYYY-MM-DD 日期。"
        result[field] = value
    if errors:
        raise ValidationError(errors)
    return result


def canonical_json(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(document):
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def revocation_reason(value):
    if not isinstance(value, str):
        raise ValidationError({"reason": "请填写撤销原因。"})
    value = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(value) <= 256 or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValidationError({"reason": "撤销原因需填写 1–256 个可见字符。"})
    return value
