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


def _add_doc(target: list[str], code: str) -> None:
    if code in DOC_CODES and code not in target:
        target.append(code)


def build_doc_request_plan(
    *,
    risk_level: str,
    reason_codes: list[str],
    payment_type: str | None,
    prepay_percent: float | int | None,
) -> dict[str, Any]:
    reasons = set(reason_codes or [])
    docs: list[str] = []
    payment = (payment_type or "").strip().lower()
    prepay = float(prepay_percent or 0)

    serious = {
        "high_prepay",
        "has_complaints",
        "price_too_low",
        "low_trust_score",
        "blacklist_match",
    }

    if risk_level in {"high"} or reasons.intersection(serious):
        _add_doc(docs, "contract_or_order")
        _add_doc(docs, "company_details")
        _add_doc(docs, "payment_confirmation")

    if payment == "cash" or prepay >= 50:
        _add_doc(docs, "payment_confirmation")

    if {"missing_dimensions", "suspicious_words"}.intersection(reasons):
        _add_doc(docs, "cargo_docs")

    if "new_counterparty" in reasons:
        _add_doc(docs, "company_details")

    if {"has_complaints", "blacklist_match"}.intersection(reasons):
        _add_doc(docs, "contract_or_order")
        _add_doc(docs, "company_details")
        _add_doc(docs, "driver_docs")

    docs = docs[:5]

    if docs:
        lines = [
            "Для продолжения сделки пришлите документы:",
            ", ".join(docs),
            "После проверки подтвердим дальнейшие шаги.",
        ]
        message = "\n".join(lines)
    else:
        message = "Дополнительные документы пока не требуются."

    return {
        "required_docs": docs,
        "reason_codes": sorted(reasons),
        "message_template": message,
    }


def apply_escalation_doc_requirements(plan: dict[str, Any]) -> dict[str, Any]:
    payload = dict(plan or {})
    required_docs = list(payload.get("required_docs") or [])
    for code in ("contract_or_order", "company_details", "driver_docs", "payment_confirmation"):
        _add_doc(required_docs, code)
    payload["required_docs"] = required_docs[:5]
    return payload
