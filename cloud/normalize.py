from __future__ import annotations

import unicodedata


def normalize_class_code(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()


def normalize_student_no(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFKC", value).strip().upper()


def normalize_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(character for character in normalized if not character.isspace())


def mask_name(value: str) -> str:
    if not value:
        return ""
    if len(value) == 1:
        return value + "*"
    if len(value) == 2:
        return value[0] + "*"
    return value[0] + ("*" * (len(value) - 2)) + value[-1]
