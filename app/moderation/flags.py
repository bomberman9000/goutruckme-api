"""
Canonical moderation flags and normalization helpers.
"""

from __future__ import annotations

from typing import Iterable


CANONICAL_FLAGS = {
    "high_price_outlier",
    "low_price_outlier",
    "prepay_100",
    "cash_only",
    "no_documents",
    "no_contract",
    "urgent_pressure",
    "contact_mismatch",
    "new_company",
    "low_trust_counterparty",
    "route_inconsistent",
    "weight_volume_inconsistent",
    "body_type_mismatch",
    "doc_empty_or_missing",
    "doc_duplicate_hash",
    "doc_type_mismatch",
    "suspicious_words",
    "insufficient_data",
}


_LEGACY_FLAG_MAP = {
    "rate_per_km_very_low": "low_price_outlier",
    "rate_per_km_very_high": "high_price_outlier",
    "missing_route": "route_inconsistent",
    "carrier_contact_missing": "contact_mismatch",
    "company_fields_missing": "insufficient_data",
    "missing_or_empty_file": "doc_empty_or_missing",
    "file_hash_reused": "doc_duplicate_hash",
    "document_for_cancelled_deal": "doc_type_mismatch",
    "ai_risk_high": "low_trust_counterparty",
}

_IGNORED_FLAGS = {
    "date_in_past",
    "past_date",
    "loading_date_in_past",
}


def _normalize_text(value: str) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_flag(flag: str) -> str:
    raw = str(flag or "").strip()
    if not raw:
        return "insufficient_data"

    lower_raw = raw.lower()
    value = _normalize_text(raw)

    if value in _IGNORED_FLAGS:
        return ""

    if value in CANONICAL_FLAGS:
        return value

    if value in _LEGACY_FLAG_MAP:
        return _LEGACY_FLAG_MAP[value]

    if value.startswith("suspicious_keyword:"):
        token = value.split(":", 1)[1]
        if "предоплата" in token and "100" in token:
            return "prepay_100"
        if "налич" in token:
            return "cash_only"
        if "без_документ" in token or "бездокумент" in token:
            return "no_documents"
        if "без_договор" in token or "бездоговор" in token:
            return "no_contract"
        if "сроч" in token:
            return "urgent_pressure"
        return "suspicious_words"

    if "предоплата" in lower_raw and "100" in lower_raw:
        return "prepay_100"
    if "налич" in lower_raw:
        return "cash_only"
    if "без документ" in lower_raw:
        return "no_documents"
    if "без договор" in lower_raw:
        return "no_contract"
    if "urgent" in value or "сроч" in lower_raw:
        return "urgent_pressure"
    if "route" in value or "маршрут" in lower_raw:
        return "route_inconsistent"
    if "weight" in value or "volume" in value:
        return "weight_volume_inconsistent"
    if "body" in value or "кузов" in lower_raw:
        return "body_type_mismatch"
    if "contact" in value:
        return "contact_mismatch"
    if "trust" in value:
        return "low_trust_counterparty"
    if "new" in value and "company" in value:
        return "new_company"
    if "doc" in value and ("empty" in value or "missing" in value):
        return "doc_empty_or_missing"
    if "doc" in value and ("duplicate" in value or "hash" in value):
        return "doc_duplicate_hash"
    if "doc" in value and ("type" in value or "mismatch" in value):
        return "doc_type_mismatch"
    if "price" in value and ("high" in value or "over" in value or "outlier" in value):
        return "high_price_outlier"
    if "price" in value and ("low" in value or "under" in value):
        return "low_price_outlier"
    if "insufficient" in value or "missing_data" in value:
        return "insufficient_data"
    if ("date" in value and "past" in value) or ("дата" in lower_raw and "прош" in lower_raw):
        return ""

    return "suspicious_words"


def normalize_flags(flags: Iterable[str] | None) -> list[str]:
    if not flags:
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in flags:
        normalized = normalize_flag(str(item or ""))
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
