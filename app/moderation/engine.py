"""
AI Moderation engine: rules-only baseline + optional LLM.
review_deal(deal_sync_row) -> {risk_level, flags, comment, recommended_action, model_used}
review_document(document_row) -> same structure.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Suspicious keywords (deal payload / comments)
SUSPICIOUS_KEYWORDS = [
    "предоплата 100%",
    "предоплата 100",
    "наличка",
    "наличными",
    "без документов",
    "без договора",
    "без ндс",
    "черная касса",
]

# Bounds for rate_per_km (RUB/km) - simple heuristic
RATE_PER_KM_MIN = 5
RATE_PER_KM_MAX = 150


def _normalize_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    return (s or "").lower().strip()


def _check_suspicious_text(payload: dict) -> List[str]:
    flags = []
    text_parts = []
    cargo = payload.get("cargoSnapshot") or {}
    for key in ("from_city", "to_city", "truck_type", "comments", "comment"):
        val = payload.get(key) or cargo.get(key)
        if val:
            text_parts.append(_normalize_text(str(val)))
    full_text = " ".join(text_parts)
    for kw in SUSPICIOUS_KEYWORDS:
        if kw in full_text:
            flags.append(f"suspicious_keyword:{kw}")
    return flags


def _review_deal_rules(payload: dict) -> Dict[str, Any]:
    flags: List[str] = []
    cargo = payload.get("cargoSnapshot") or {}
    from_city = (cargo.get("from_city") or payload.get("from_city") or "").strip()
    to_city = (cargo.get("to_city") or payload.get("to_city") or "").strip()
    if not from_city or not to_city:
        flags.append("missing_route")
    price = None
    distance = None
    if cargo:
        price = cargo.get("price")
        if price is not None:
            price = float(price)
        distance = cargo.get("distance")
        if distance is not None:
            distance = float(distance)
    if price is not None and distance is not None and distance > 0:
        rate_per_km = price / distance
        if rate_per_km < RATE_PER_KM_MIN:
            flags.append("rate_per_km_very_low")
        elif rate_per_km > RATE_PER_KM_MAX:
            flags.append("rate_per_km_very_high")
    ai_risk = payload.get("ai_risk") or (cargo.get("ai_risk") if isinstance(cargo, dict) else None)
    if _normalize_text(str(ai_risk)) == "high":
        flags.append("ai_risk_high")
    carrier = payload.get("carrier") or {}
    if isinstance(carrier, dict):
        if not (carrier.get("name") or carrier.get("phone")):
            flags.append("carrier_contact_missing")
    company_inn = payload.get("company_inn") or cargo.get("inn")
    company_phone = payload.get("company_phone") or carrier.get("phone")
    if not company_inn and not company_phone:
        flags.append("company_fields_missing")
    flags.extend(_check_suspicious_text(payload))
    # Determine risk_level
    if "ai_risk_high" in flags or "suspicious_keyword:" in " ".join(flags):
        risk_level = "high"
    elif "rate_per_km_very_low" in flags or "rate_per_km_very_high" in flags:
        risk_level = "high"
    elif flags:
        risk_level = "medium"
    else:
        risk_level = "low"
    comment = "Проверка правилами: " + (", ".join(flags) if flags else "замечаний нет.")
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


def review_deal(deal_sync_row: Any) -> Dict[str, Any]:
    """Review a deal_sync row. deal_sync_row must have .payload (dict)."""
    payload = getattr(deal_sync_row, "payload", None) or {}
    if not isinstance(payload, dict):
        payload = {}
    result = _review_deal_rules(payload)
    # Optional LLM
    try:
        llm_result = _llm_review_deal(payload, result)
        if llm_result:
            result = llm_result
    except Exception as e:
        logger.warning("LLM review_deal failed, using rules: %s", e)
        result["model_used"] = result.get("model_used") or "rules_fallback"
    return result


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
        flags.append("missing_or_empty_file")
    if file_hash_seen_elsewhere:
        flags.append("file_hash_reused")
    if deal_payload and doc_type:
        # Simple check: deal has cargoSnapshot with route; doc_type should match document purpose
        # Mismatch: e.g. CONTRACT but deal status is CANCELLED
        status = (deal_payload.get("status") or "").upper()
        if status == "CANCELLED" and doc_type in ("CONTRACT", "TTN", "UPD"):
            flags.append("document_for_cancelled_deal")
    if "missing_or_empty_file" in flags:
        risk_level = "high"
    elif flags:
        risk_level = "medium"
    else:
        risk_level = "low"
    comment = "Проверка документа правилами: " + (", ".join(flags) if flags else "замечаний нет.")
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


def review_document(
    document_row: Any,
    deal_payload: Optional[dict] = None,
    file_exists: bool = False,
    file_size: int = 0,
    file_hash_seen_elsewhere: bool = False,
) -> Dict[str, Any]:
    """Review a document_sync row."""
    result = _review_document_rules(
        document_row,
        deal_payload=deal_payload,
        file_exists=file_exists,
        file_size=file_size,
        file_hash_seen_elsewhere=file_hash_seen_elsewhere,
    )
    try:
        llm_result = _llm_review_document(document_row, result, deal_payload)
        if llm_result:
            result = llm_result
    except Exception as e:
        logger.warning("LLM review_document failed, using rules: %s", e)
        result["model_used"] = result.get("model_used") or "rules_fallback"
    return result


def _get_llm_config() -> Optional[Dict[str, str]]:
    try:
        from app.core.config import get_settings
        s = get_settings()
        key = getattr(s, "LLM_API_KEY", None) or ""
        if not key or not key.strip():
            return None
        return {
            "api_key": key.strip(),
            "base_url": (getattr(s, "LLM_BASE_URL", None) or "https://api.openai.com/v1").strip(),
            "model": (getattr(s, "LLM_MODEL", None) or "gpt-4o-mini").strip(),
        }
    except Exception:
        return None


def _llm_review_deal(payload: dict, rules_result: dict) -> Optional[dict]:
    cfg = _get_llm_config()
    if not cfg:
        return None
    prompt = f"""Ты модератор сделок. По данным сделки определи риск (risk_level: low/medium/high), список флагов (flags), краткий comment и recommended_action.
Данные сделки (JSON): {json.dumps(payload, ensure_ascii=False)[:2000]}
Результат правил: {json.dumps(rules_result, ensure_ascii=False)}
Ответь ТОЛЬКО валидным JSON в одну строку: {{"risk_level":"low|medium|high","flags":["..."],"comment":"...","recommended_action":"..."}}
"""
    try:
        out = _llm_chat(cfg, prompt)
        if not out:
            return None
        parsed = json.loads(out)
        risk = (parsed.get("risk_level") or "low").lower()
        if risk not in ("low", "medium", "high"):
            risk = "low"
        return {
            "risk_level": risk,
            "flags": parsed.get("flags") if isinstance(parsed.get("flags"), list) else rules_result.get("flags", []),
            "comment": str(parsed.get("comment") or rules_result.get("comment", "")),
            "recommended_action": str(parsed.get("recommended_action") or rules_result.get("recommended_action", "")),
            "model_used": cfg.get("model", "llm"),
        }
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("LLM JSON invalid: %s", e)
        rules_result["model_used"] = "rules_fallback"
        return rules_result


def _llm_review_document(
    document_row: Any,
    rules_result: dict,
    deal_payload: Optional[dict],
) -> Optional[dict]:
    cfg = _get_llm_config()
    if not cfg:
        return None
    doc_type = getattr(document_row, "doc_type", "")
    deal_snippet = json.dumps(deal_payload or {}, ensure_ascii=False)[:1000] if deal_payload else "{}"
    prompt = f"""Ты модератор документов. Тип документа: {doc_type}. Контекст сделки: {deal_snippet}
Результат правил: {json.dumps(rules_result, ensure_ascii=False)}
Ответь ТОЛЬКО валидным JSON: {{"risk_level":"low|medium|high","flags":["..."],"comment":"...","recommended_action":"..."}}
"""
    try:
        out = _llm_chat(cfg, prompt)
        if not out:
            return None
        parsed = json.loads(out)
        risk = (parsed.get("risk_level") or "low").lower()
        if risk not in ("low", "medium", "high"):
            risk = "low"
        return {
            "risk_level": risk,
            "flags": parsed.get("flags") if isinstance(parsed.get("flags"), list) else rules_result.get("flags", []),
            "comment": str(parsed.get("comment") or rules_result.get("comment", "")),
            "recommended_action": str(parsed.get("recommended_action") or rules_result.get("recommended_action", "")),
            "model_used": cfg.get("model", "llm"),
        }
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("LLM document JSON invalid: %s", e)
        rules_result["model_used"] = "rules_fallback"
        return rules_result


def _llm_chat(cfg: dict, prompt: str) -> Optional[str]:
    import httpx
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    body = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                return (msg.get("content") or "").strip()
    except Exception as e:
        logger.warning("LLM request failed: %s", e)
    return None
