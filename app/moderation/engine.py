"""
AI Moderation engine: rules-first baseline + optional local Ollama enrichment.
review_deal(deal_sync_row) -> {risk_level, flags, comment, recommended_action, model_used}
review_document(document_row) -> same structure.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.moderation.flags import normalize_flags
from app.moderation.llm import llm_analyze_review


SUSPICIOUS_SIGNAL_MAP: list[tuple[str, str]] = [
    ("предоплата 100%", "prepay_100"),
    ("предоплата 100", "prepay_100"),
    ("только наличка", "cash_only"),
    ("наличка", "cash_only"),
    ("наличными", "cash_only"),
    ("без документов", "no_documents"),
    ("без договора", "no_contract"),
    ("срочно", "urgent_pressure"),
]
SUSPICIOUS_GENERIC_KEYWORDS = ["без ндс", "черная касса", "перевод на карту", "гарантия 100%"]

RATE_PER_KM_MIN = 5
RATE_PER_KM_MAX = 150

HIGH_RISK_FLAGS = {
    "high_price_outlier",
    "low_price_outlier",
    "prepay_100",
    "cash_only",
    "no_documents",
    "no_contract",
    "doc_empty_or_missing",
    "doc_duplicate_hash",
    "doc_type_mismatch",
}

MEDIUM_RISK_FLAGS = {
    "urgent_pressure",
    "contact_mismatch",
    "new_company",
    "low_trust_counterparty",
    "route_inconsistent",
    "weight_volume_inconsistent",
    "body_type_mismatch",
    "suspicious_words",
    "insufficient_data",
}


def _normalize_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    return (s or "").lower().strip()


def _risk_rank(level: str) -> int:
    value = _normalize_text(level)
    if value == "high":
        return 3
    if value == "medium":
        return 2
    return 1


def _rank_to_risk(rank: int) -> str:
    if rank >= 3:
        return "high"
    if rank == 2:
        return "medium"
    return "low"


def _try_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _risk_from_flags(flags: list[str]) -> str:
    if any(flag in HIGH_RISK_FLAGS for flag in flags):
        return "high"
    if flags or any(flag in MEDIUM_RISK_FLAGS for flag in flags):
        return "medium"
    return "low"


def _body_type_token(value: Any) -> str:
    text = _normalize_text(str(value or ""))
    if not text:
        return ""
    mapping = {
        "тент": "tent",
        "tent": "tent",
        "реф": "refrigerator",
        "рефрижератор": "refrigerator",
        "reef": "refrigerator",
        "refrigerator": "refrigerator",
        "площадка": "platform",
        "platform": "platform",
        "коники": "stakes",
        "stakes": "stakes",
    }
    for key, normalized in mapping.items():
        if key in text:
            return normalized
    return text.split()[0]


def _pick_first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip() == "":
                continue
            return value.strip()
        return value
    return None


def _build_llm_deal_payload(payload: dict[str, Any], rules_result: dict[str, Any]) -> dict[str, Any]:
    cargo = payload.get("cargoSnapshot") if isinstance(payload.get("cargoSnapshot"), dict) else {}
    carrier = payload.get("carrier") if isinstance(payload.get("carrier"), dict) else {}

    return {
        "from_city": _pick_first_non_empty(cargo.get("from_city"), payload.get("from_city")),
        "to_city": _pick_first_non_empty(cargo.get("to_city"), payload.get("to_city")),
        "weight_t": _try_float(_pick_first_non_empty(cargo.get("weight"), payload.get("weight"))),
        "body_type": _pick_first_non_empty(
            cargo.get("body_type"),
            cargo.get("truck_type"),
            payload.get("body_type"),
            payload.get("truck_type"),
        ),
        "pickup_date": _pick_first_non_empty(
            cargo.get("pickup_date"),
            cargo.get("loading_date"),
            payload.get("pickup_date"),
            payload.get("loading_date"),
        ),
        "price_total": _try_float(
            _pick_first_non_empty(cargo.get("price"), payload.get("price"), payload.get("price_total"))
        ),
        "distance_km": _try_float(
            _pick_first_non_empty(cargo.get("distance"), payload.get("distance"), payload.get("distance_km"))
        ),
        "payment_terms": _pick_first_non_empty(
            payload.get("payment_terms"),
            cargo.get("payment_terms"),
            payload.get("payment_type"),
        ),
        "notes": _pick_first_non_empty(
            payload.get("notes"),
            payload.get("comments"),
            payload.get("comment"),
            payload.get("carrier_message"),
            cargo.get("comments"),
            cargo.get("comment"),
        ),
        "client_trust_score": _try_float(
            _pick_first_non_empty(
                payload.get("client_trust_score"),
                payload.get("shipper_trust_score"),
                cargo.get("shipper_trust_score"),
                cargo.get("client_trust_score"),
            )
        ),
        "client_stars": _try_float(
            _pick_first_non_empty(
                payload.get("client_trust_stars"),
                payload.get("client_stars"),
                cargo.get("shipper_trust_stars"),
                cargo.get("client_stars"),
            )
        ),
        "carrier_trust_score": _try_float(
            _pick_first_non_empty(
                payload.get("carrier_trust_score"),
                carrier.get("trust_score"),
            )
        ),
        "carrier_stars": _try_float(
            _pick_first_non_empty(
                payload.get("carrier_trust_stars"),
                payload.get("carrier_stars"),
                carrier.get("trust_stars"),
            )
        ),
        "rules": {
            "risk_level": rules_result.get("risk_level"),
            "flags": rules_result.get("flags") or [],
        },
    }


def _check_suspicious_text(payload: dict) -> list[str]:
    flags: list[str] = []
    text_parts = []
    cargo = payload.get("cargoSnapshot") or {}
    for key in ("from_city", "to_city", "truck_type", "comments", "comment", "carrier_message"):
        val = payload.get(key) or cargo.get(key)
        if val:
            text_parts.append(_normalize_text(str(val)))
    full_text = " ".join(text_parts)
    for phrase, flag in SUSPICIOUS_SIGNAL_MAP:
        if phrase in full_text:
            flags.append(flag)

    if any(keyword in full_text for keyword in SUSPICIOUS_GENERIC_KEYWORDS):
        flags.append("suspicious_words")

    return normalize_flags(flags)


def _review_deal_rules(payload: dict) -> Dict[str, Any]:
    flags: List[str] = []
    cargo = payload.get("cargoSnapshot") or {}
    if not isinstance(cargo, dict):
        cargo = {}

    from_city = (cargo.get("from_city") or payload.get("from_city") or "").strip()
    to_city = (cargo.get("to_city") or payload.get("to_city") or "").strip()
    if not from_city or not to_city:
        flags.append("route_inconsistent")

    price = _try_float(cargo.get("price"))
    distance = _try_float(cargo.get("distance"))
    if price is not None and distance is not None and distance > 0:
        rate_per_km = price / distance
        if rate_per_km < RATE_PER_KM_MIN:
            flags.append("low_price_outlier")
        elif rate_per_km > RATE_PER_KM_MAX:
            flags.append("high_price_outlier")
    elif price is None or distance is None:
        flags.append("insufficient_data")

    ai_risk = payload.get("ai_risk") or (cargo.get("ai_risk") if isinstance(cargo, dict) else None)
    if _normalize_text(str(ai_risk)) == "high":
        flags.append("low_trust_counterparty")

    carrier = payload.get("carrier") or {}
    if not isinstance(carrier, dict):
        carrier = {}
    if not carrier.get("phone"):
        flags.append("contact_mismatch")

    company_inn = payload.get("company_inn") or cargo.get("inn")
    company_phone = payload.get("company_phone") or carrier.get("phone")
    if not company_inn and not company_phone:
        flags.append("insufficient_data")

    trust_score = _try_float(payload.get("carrier_trust_score") or carrier.get("trust_score"))
    if trust_score is not None and trust_score < 40:
        flags.append("low_trust_counterparty")

    carrier_age_days = _try_float(
        payload.get("carrier_age_days")
        or carrier.get("company_age_days")
        or carrier.get("age_days")
    )
    if carrier_age_days is not None and carrier_age_days < 30:
        flags.append("new_company")

    weight = _try_float(cargo.get("weight") or payload.get("weight"))
    volume = _try_float(cargo.get("volume") or payload.get("volume"))
    if weight is not None and volume is not None and weight > 0 and volume > 0:
        ratio = volume / weight
        if ratio < 0.1 or ratio > 20:
            flags.append("weight_volume_inconsistent")
    elif (weight is None) != (volume is None):
        flags.append("weight_volume_inconsistent")

    requested_body = _body_type_token(cargo.get("body_type") or cargo.get("truck_type"))
    offered_body = _body_type_token(payload.get("body_type") or payload.get("truck_type") or carrier.get("body_type"))
    if requested_body and offered_body and requested_body != offered_body:
        flags.append("body_type_mismatch")

    flags.extend(_check_suspicious_text(payload))
    flags = normalize_flags(flags)
    risk_level = _risk_from_flags(flags)

    comment = "Проверка правилами: " + (", ".join(flags) if flags else "замечаний нет")
    if risk_level == "high":
        recommended_action = "Проверить сделку вручную; при необходимости связаться с контрагентом."
    elif risk_level == "medium":
        recommended_action = "Рекомендуется проверить указанные поля."
    else:
        recommended_action = "Дополнительных действий не требуется."

    return {
        "risk_level": risk_level,
        "flags": flags,
        "comment": comment,
        "recommended_action": recommended_action,
        "model_used": "rules",
    }


def _review_document_rules(
    document_row: Any,
    deal_payload: Optional[dict] = None,
    file_exists: bool = False,
    file_size: int = 0,
    file_hash_seen_elsewhere: bool = False,
) -> Dict[str, Any]:
    flags: List[str] = []
    doc_type = getattr(document_row, "doc_type", None) or ""

    if not file_exists or file_size == 0:
        flags.append("doc_empty_or_missing")
    if file_hash_seen_elsewhere:
        flags.append("doc_duplicate_hash")

    if deal_payload and doc_type:
        status = (deal_payload.get("status") or "").upper()
        if status == "CANCELLED" and doc_type in ("CONTRACT", "TTN", "UPD"):
            flags.append("doc_type_mismatch")
    elif not doc_type:
        flags.append("insufficient_data")

    flags = normalize_flags(flags)
    risk_level = _risk_from_flags(flags)

    comment = "Проверка документа правилами: " + (", ".join(flags) if flags else "замечаний нет")
    if risk_level == "high":
        recommended_action = "Проверить наличие и корректность файла документа."
    elif risk_level == "medium":
        recommended_action = "Рекомендуется проверить контекст документа."
    else:
        recommended_action = "Дополнительных действий не требуется."

    return {
        "risk_level": risk_level,
        "flags": flags,
        "comment": comment,
        "recommended_action": recommended_action,
        "model_used": "rules",
    }


def _merge_rules_and_llm(rules_result: dict[str, Any], llm_result: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not llm_result:
        return rules_result

    merged_flags = normalize_flags(list(rules_result.get("flags") or []) + list(llm_result.get("flags") or []))
    merged_risk = _rank_to_risk(
        max(
            _risk_rank(rules_result.get("risk_level", "low")),
            _risk_rank(llm_result.get("risk_level", "low")),
            _risk_rank(_risk_from_flags(merged_flags)),
        )
    )

    llm_comment = str(llm_result.get("comment") or "").strip()
    rules_comment = str(rules_result.get("comment") or "").strip()
    if llm_comment and llm_comment != rules_comment:
        merged_comment = f"{rules_comment} | LLM: {llm_comment}" if rules_comment else llm_comment
    else:
        merged_comment = rules_comment

    llm_action = str(llm_result.get("recommended_action") or "").strip()
    rules_action = str(rules_result.get("recommended_action") or "").strip()
    merged_action = llm_action or rules_action

    model = str(llm_result.get("model_used") or "ollama")

    return {
        "risk_level": merged_risk,
        "flags": merged_flags,
        "comment": merged_comment,
        "recommended_action": merged_action,
        "model_used": f"rules+{model}",
    }


def review_deal(deal_sync_row: Any) -> Dict[str, Any]:
    payload = getattr(deal_sync_row, "payload", None) or {}
    if not isinstance(payload, dict):
        payload = {}

    rules_result = _review_deal_rules(payload)
    entity_id = int(getattr(deal_sync_row, "id", 0) or 0)
    llm_payload = _build_llm_deal_payload(payload, rules_result)
    llm_result = llm_analyze_review(entity_type="deal", entity_id=entity_id, payload=llm_payload)

    return _merge_rules_and_llm(rules_result, llm_result)


def review_document(
    document_row: Any,
    deal_payload: Optional[dict] = None,
    file_exists: bool = False,
    file_size: int = 0,
    file_hash_seen_elsewhere: bool = False,
) -> Dict[str, Any]:
    rules_result = _review_document_rules(
        document_row,
        deal_payload=deal_payload,
        file_exists=file_exists,
        file_size=file_size,
        file_hash_seen_elsewhere=file_hash_seen_elsewhere,
    )
    entity_id = int(getattr(document_row, "id", 0) or 0)
    trust_block = {}
    if isinstance(deal_payload, dict):
        carrier = deal_payload.get("carrier") if isinstance(deal_payload.get("carrier"), dict) else {}
        cargo = deal_payload.get("cargoSnapshot") if isinstance(deal_payload.get("cargoSnapshot"), dict) else {}
        trust_block = {
            "carrier_trust_score": deal_payload.get("carrier_trust_score") or carrier.get("trust_score"),
            "carrier_trust_stars": deal_payload.get("carrier_trust_stars") or carrier.get("trust_stars"),
            "client_trust_score": deal_payload.get("client_trust_score") or cargo.get("shipper_trust_score"),
            "client_trust_stars": deal_payload.get("client_trust_stars") or cargo.get("shipper_trust_stars"),
        }

    llm_payload = {
        "doc_type": getattr(document_row, "doc_type", None) or "UNKNOWN",
        "file_exists": bool(file_exists),
        "file_size": int(file_size or 0),
        "file_hash_seen_elsewhere": bool(file_hash_seen_elsewhere),
        "deal_context": deal_payload or {},
        "trust": trust_block,
        "rules_engine": {
            "risk_level": rules_result.get("risk_level"),
            "flags": rules_result.get("flags") or [],
            "comment": rules_result.get("comment"),
            "recommended_action": rules_result.get("recommended_action"),
            "model_used": rules_result.get("model_used"),
        },
    }
    llm_result = llm_analyze_review(entity_type="document", entity_id=entity_id, payload=llm_payload)

    return _merge_rules_and_llm(rules_result, llm_result)
