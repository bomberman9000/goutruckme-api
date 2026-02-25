from __future__ import annotations

import re


_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def _clean_text(value: str) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


def norm_city(value: str | None) -> str:
    text = _clean_text(value or "")
    if not text:
        return ""
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


def norm_phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return None
    if len(digits) > 11:
        digits = digits[-11:]
    if len(digits) not in (10, 11):
        return None
    return digits


def norm_inn(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) in (10, 12):
        return digits
    return None


def norm_name(value: str | None) -> str:
    return _clean_text(value or "")
