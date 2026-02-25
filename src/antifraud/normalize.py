from __future__ import annotations

import re


_SPACE_RE = re.compile(r"\s+")
_CITY_PUNCT_RE = re.compile(r"[^\w\s-]+", re.UNICODE)


def _collapse_spaces(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip()


def norm_city(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.lower().replace("ё", "е")
    normalized = _CITY_PUNCT_RE.sub(" ", normalized)
    return _collapse_spaces(normalized)


def norm_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D+", "", value)
    if not digits:
        return None
    if len(digits) > 11:
        digits = digits[-11:]
    if len(digits) < 10:
        return None
    return digits


def norm_inn(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D+", "", value)
    if len(digits) not in (10, 12):
        return None
    return digits


def norm_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.lower().replace("ё", "е")
    normalized = _collapse_spaces(normalized)
    return normalized or None
