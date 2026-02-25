from __future__ import annotations

from typing import Any


DOC_CODES = {
    "contract_or_order",
    "company_details",
    "vat_docs",
    "driver_docs",
    "cargo_docs",
    "payment_confirmation",
}

ESCALATION_REQUIRED_DOCS = [
    "contract_or_order",
    "company_details",
    "driver_docs",
    "payment_confirmation",
]

_SERIOUS_CODES = {
    "high_prepay",
    "has_complaints",
    "price_too_low",
    "low_trust_score",
    "blacklist_match",
}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def build_doc_request_plan(
    risk_level: str,
    reason_codes: list[str] | None,
    payment_type: str | None,
    prepay_percent: int | float | None,
) -> dict[str, Any]:
    reasons = _dedupe([str(code or "").strip() for code in (reason_codes or [])])
    payment_kind = str(payment_type or "unknown").strip().lower()
    prepay = _to_int(prepay_percent)

    required_docs: list[str] = []

    serious = any(code in _SERIOUS_CODES for code in reasons)
    if str(risk_level or "").lower() == "high" or serious:
        required_docs.extend(["contract_or_order", "company_details", "payment_confirmation"])

    if "cash_payment" in reasons or "high_prepay" in reasons or prepay >= 50:
        required_docs.append("payment_confirmation")

    if "missing_dimensions" in reasons or "suspicious_words" in reasons:
        required_docs.append("cargo_docs")

    if "new_counterparty" in reasons:
        required_docs.append("company_details")

    if "has_complaints" in reasons or "blacklist_match" in reasons:
        required_docs.extend(["contract_or_order", "company_details", "driver_docs"])

    # Для безнала добавляем НДС/счётные документы как мягкую рекомендацию.
    if payment_kind in {"bank", "card"} and str(risk_level or "").lower() in {"medium", "high"}:
        required_docs.append("vat_docs")

    required_docs = [doc for doc in _dedupe(required_docs) if doc in DOC_CODES][:5]

    message_lines = [
        "Для безопасного подтверждения сделки пришлите документы:",
        ", ".join(required_docs) if required_docs else "доп. документы не требуются",
        "После проверки подтвердим условия оплаты и выпуск на рейс.",
    ]

    return {
        "required_docs": required_docs,
        "reason_codes": reasons,
        "message_template": "\n".join(message_lines),
    }


def apply_escalation_doc_requirements(plan: dict[str, Any]) -> dict[str, Any]:
    payload = dict(plan or {})
    base_docs = _dedupe([str(item) for item in payload.get("required_docs") or []])
    merged_docs = _dedupe([*base_docs, *ESCALATION_REQUIRED_DOCS])[:5]
    payload["required_docs"] = [doc for doc in merged_docs if doc in DOC_CODES]
    return payload
