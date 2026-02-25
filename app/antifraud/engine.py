from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any

from app.ai.ai_service import ai_service
from app.antifraud.docs import DOC_CODES
from app.core.config import settings


logger = logging.getLogger(__name__)

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
_RISK_BY_RANK = {rank: level for level, rank in _RISK_ORDER.items()}

_SERIOUS_RULE_FLAGS = {"high_prepay", "has_complaints", "price_too_low", "low_trust_score"}
SERIOUS_REASON_CODES = {
    "high_prepay",
    "has_complaints",
    "price_too_low",
    "low_trust_score",
    "blacklist_match",
    "repeat_high_risk_pattern",
    "price_statistically_low",
}

_SUSPICIOUS_TERMS = [
    "100% предоплата",
    "срочно",
    "налич",
    "без договора",
    "без документ",
    "только на карту",
    "перевод физлицу",
    "залог",
    "штраф сразу",
    "диспетчер",
    "оплата после выгрузки на карту",
]

_FLAG_LABELS = {
    "blacklist_match": "совпадение с blacklist",
    "high_prepay": "высокая предоплата",
    "has_complaints": "есть жалобы на контрагента",
    "repeat_high_risk_pattern": "повторяющийся высокий риск",
    "chronic_risk_profile": "хронический риск-профиль",
    "price_too_low": "ставка подозрительно низкая",
    "low_trust_score": "низкий trust score",
    "price_statistically_low": "ставка статистически аномально низкая",
    "price_statistically_high": "ставка статистически аномально высокая",
    "suspicious_words": "подозрительные формулировки",
    "invalid_dates": "некорректные даты",
    "missing_dimensions": "неполные параметры груза",
    "cash_payment": "наличная форма оплаты",
    "new_counterparty": "новый контрагент",
    "price_too_high": "ставка аномально высокая",
    "whitelist_match": "совпадение с whitelist",
}

_FLAG_PRIORITY = [
    "blacklist_match",
    "repeat_high_risk_pattern",
    "high_prepay",
    "has_complaints",
    "price_statistically_low",
    "price_too_low",
    "low_trust_score",
    "suspicious_words",
    "invalid_dates",
    "missing_dimensions",
    "cash_payment",
    "new_counterparty",
    "price_too_high",
    "whitelist_match",
]

ALLOWED_REASON_CODES = {
    "price_too_low",
    "price_too_high",
    "price_statistically_low",
    "price_statistically_high",
    "high_prepay",
    "cash_payment",
    "suspicious_words",
    "new_counterparty",
    "has_complaints",
    "low_trust_score",
    "repeat_high_risk_pattern",
    "chronic_risk_profile",
    "missing_dimensions",
    "invalid_dates",
    "blacklist_match",
    "whitelist_match",
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _risk_from_score_v1(score: int) -> str:
    if score >= 6:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _risk_from_score_v2(score: int) -> str:
    if score >= 7:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _decrease_risk_one_level(risk_level: str) -> str:
    rank = _RISK_ORDER.get(str(risk_level or "low").lower(), 0)
    return _RISK_BY_RANK.get(max(rank - 1, 0), "low")


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _build_comment(flags: dict[str, Any], risk_level: str) -> str:
    if not flags:
        return "Правила не выявили явных антифрод-рисков."

    top_labels: list[str] = []
    for key in _FLAG_PRIORITY:
        if key in flags:
            top_labels.append(_FLAG_LABELS.get(key, key))
        if len(top_labels) == 2:
            break

    if not top_labels:
        return f"Обнаружены рисковые признаки. Уровень риска: {risk_level}."
    if len(top_labels) == 1:
        return f"Обнаружен риск: {top_labels[0]}. Уровень: {risk_level}."
    return f"Ключевые риски: {top_labels[0]} и {top_labels[1]}. Уровень: {risk_level}."


def _build_recommended_action(flags: dict[str, Any]) -> str:
    actions: list[str] = []

    if any(key in flags for key in ("high_prepay", "cash_payment")):
        actions.append("Не давать предоплату; согласовать оплату по безналу/эскроу")
    if any(key in flags for key in ("has_complaints", "low_trust_score", "new_counterparty", "blacklist_match")):
        actions.append("Проверить ИНН/жалобы и историю контрагента")
    if any(
        key in flags
        for key in ("suspicious_words", "price_too_low", "invalid_dates", "missing_dimensions", "price_too_high")
    ):
        actions.append("Запросить договор/заявку и реквизиты юрлица")

    if not actions:
        actions.append("Продолжить сделку по стандартному регламенту проверки")

    return "; ".join(actions[:3])


# ============================
# v1 compatibility functions
# ============================


def review_deal_rules(deal: dict) -> dict[str, Any]:
    payload = _as_dict(deal)
    route = _as_dict(payload.get("route"))
    cargo = _as_dict(payload.get("cargo"))
    price = _as_dict(payload.get("price"))
    payment = _as_dict(payload.get("payment"))
    dates = _as_dict(payload.get("dates"))
    counterparty = _as_dict(payload.get("counterparty"))
    notes = str(payload.get("notes") or "")

    score = 0
    flags: dict[str, Any] = {}

    distance_km = _to_float(route.get("distance_km"))
    total_rub = _to_float(price.get("total_rub"))
    rate_per_km = _to_float(price.get("rate_per_km"))
    if rate_per_km <= 0 and distance_km > 0 and total_rub > 0:
        rate_per_km = total_rub / distance_km

    if distance_km > 0:
        if rate_per_km < int(settings.MIN_RATE_PER_KM):
            flags["price_too_low"] = True
            score += 3
        if rate_per_km > int(settings.MAX_RATE_PER_KM):
            flags["price_too_high"] = True
            score += 1

    prepay_percent = _to_int(payment.get("prepay_percent"))
    payment_type = str(payment.get("type") or "unknown").strip().lower()
    if prepay_percent >= 50:
        flags["high_prepay"] = prepay_percent
        score += 3
    if payment_type == "cash":
        flags["cash_payment"] = True
        score += 1

    notes_lc = notes.lower()
    matched_terms = [term for term in _SUSPICIOUS_TERMS if term in notes_lc]
    if matched_terms:
        flags["suspicious_words"] = _dedupe_strings(matched_terms)
        score += 2

    if bool(counterparty.get("is_new")):
        flags["new_counterparty"] = True
        score += 1

    complaints_count = _to_int(counterparty.get("complaints_count"))
    if complaints_count > 0:
        flags["has_complaints"] = complaints_count
        score += 3

    trust_score = _to_float(counterparty.get("trust_score"))
    if trust_score and trust_score < int(settings.LOW_TRUST_SCORE_THRESHOLD):
        flags["low_trust_score"] = trust_score
        score += 2

    weight_t = _to_float(cargo.get("weight_t"))
    volume_m3 = _to_float(cargo.get("volume_m3"))
    if weight_t == 0 or volume_m3 == 0:
        flags["missing_dimensions"] = True
        score += 1

    pickup_date = _safe_date(dates.get("pickup"))
    delivery_date = _safe_date(dates.get("delivery"))
    if pickup_date is None or delivery_date is None or delivery_date < pickup_date:
        flags["invalid_dates"] = True
        score += 2

    risk_level = _risk_from_score_v1(score)
    return {
        "risk_level": risk_level,
        "flags": flags,
        "comment": _build_comment(flags, risk_level),
        "recommended_action": _build_recommended_action(flags),
    }


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    candidate = text.strip()

    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(candidate[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _normalize_llm_output(parsed: dict[str, Any], *, model: str, source: str) -> dict[str, Any]:
    risk_level = str(parsed.get("risk_level") or "").strip().lower()
    if risk_level not in _RISK_ORDER:
        return {}

    raw_flags = parsed.get("flags")
    if isinstance(raw_flags, dict):
        llm_flags = raw_flags
    elif isinstance(raw_flags, list):
        llm_flags = {str(item): True for item in raw_flags if str(item or "").strip()}
    else:
        llm_flags = {}

    if isinstance(llm_flags.get("suspicious_words"), str):
        llm_flags["suspicious_words"] = [llm_flags["suspicious_words"]]

    return {
        "risk_level": risk_level,
        "flags": llm_flags,
        "comment": str(parsed.get("comment") or "").strip(),
        "recommended_action": str(parsed.get("recommended_action") or "").strip(),
        "_llm_model": model,
        "_llm_source": source,
    }


async def review_deal_llm(deal: dict, rules_result: dict) -> dict[str, Any]:
    payload = _as_dict(deal)
    route = _as_dict(payload.get("route"))
    cargo = _as_dict(payload.get("cargo"))
    price = _as_dict(payload.get("price"))
    payment = _as_dict(payload.get("payment"))
    counterparty = _as_dict(payload.get("counterparty"))
    notes = str(payload.get("notes") or "")[:600]

    prompt = (
        "Оцени риск сделки по данным ниже и верни только JSON.\n"
        '{"risk_level":"low|medium|high","flags":{},"comment":"...","recommended_action":"..."}\n\n'
        f"Маршрут: {route.get('from_city', '')} -> {route.get('to_city', '')}\n"
        f"distance_km: {route.get('distance_km', 0)}\n"
        f"total_rub: {price.get('total_rub', 0)}\n"
        f"rate_per_km: {price.get('rate_per_km', 0)}\n"
        f"payment_type: {payment.get('type', 'unknown')}\n"
        f"prepay_percent: {payment.get('prepay_percent', 0)}\n"
        f"counterparty: name={counterparty.get('name', '')}, inn={counterparty.get('inn', '')}, "
        f"is_new={counterparty.get('is_new', False)}, complaints={counterparty.get('complaints_count', 0)}, "
        f"trust={counterparty.get('trust_score', 0)}\n"
        f"cargo: name={cargo.get('name', '')}, weight_t={cargo.get('weight_t', 0)}, volume_m3={cargo.get('volume_m3', 0)}\n"
        f"notes: {notes}\n"
        f"rules_flags: {json.dumps(rules_result.get('flags') or {}, ensure_ascii=False)}\n"
    )

    started = time.perf_counter()
    try:
        response = await asyncio.to_thread(
            ai_service.ask,
            prompt=prompt,
            model_override=(settings.AI_ANTIFRAUD_LLM_MODEL or None),
            temperature=0.1,
            max_tokens=350,
            system_prompt=(
                "Ты anti-fraud ассистент логистической платформы. "
                "Отвечай строго JSON и не добавляй текст вне JSON."
            ),
        )
    except Exception as exc:
        logger.warning("antifraud.llm.failed error=%s", exc)
        return {}

    duration_ms = int((time.perf_counter() - started) * 1000)
    llm_model = str(response.get("model") or settings.AI_ANTIFRAUD_LLM_MODEL or "llm")
    llm_source = str(response.get("source") or "unknown")
    logger.info(
        "antifraud.llm.used source=%s model=%s duration_ms=%s",
        llm_source,
        llm_model,
        duration_ms,
    )

    parsed = _extract_first_json_object(str(response.get("text") or ""))
    if not parsed:
        return {}
    return _normalize_llm_output(parsed, model=llm_model, source=llm_source)


def merge_flags(rules_flags: dict[str, Any], llm_flags: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(_as_dict(rules_flags))
    for key, value in _as_dict(llm_flags).items():
        if key == "suspicious_words":
            base_words = merged.get("suspicious_words")
            base_list = base_words if isinstance(base_words, list) else ([str(base_words)] if base_words else [])
            incoming = value if isinstance(value, list) else ([str(value)] if value else [])
            merged["suspicious_words"] = _dedupe_strings([*base_list, *[str(x) for x in incoming]])
            continue
        if key not in merged:
            merged[key] = value
    return merged


def apply_llm_risk_policy(rules_risk: str, llm_risk: str, rules_flags: dict[str, Any]) -> str:
    base_rank = _RISK_ORDER.get(str(rules_risk or "low").lower(), 0)
    llm_rank = _RISK_ORDER.get(str(llm_risk or "low").lower(), 0)

    if llm_rank <= base_rank:
        return _RISK_BY_RANK.get(base_rank, "low")

    has_serious_flag = any(flag in _as_dict(rules_flags) for flag in _SERIOUS_RULE_FLAGS)
    if not has_serious_flag:
        return _RISK_BY_RANK.get(base_rank, "low")

    return _RISK_BY_RANK.get(min(base_rank + 1, llm_rank, _RISK_ORDER["high"]), "low")


# ============================
# v2 scoring and llm helpers
# ============================


def _normalize_list_check(list_check: dict[str, Any]) -> dict[str, Any]:
    payload = _as_dict(list_check)
    entries: list[dict[str, Any]] = []
    for item in _as_list(payload.get("entries")):
        row = _as_dict(item)
        entries.append(
            {
                "list_type": str(row.get("list_type") or "").strip().lower(),
                "inn": row.get("inn"),
                "phone": row.get("phone"),
                "name": row.get("name"),
                "note": row.get("note"),
            }
        )

    return {
        "whitelist_match": bool(payload.get("whitelist_match")),
        "blacklist_match": bool(payload.get("blacklist_match")),
        "matched_fields": [
            str(value) for value in _as_list(payload.get("matched_fields")) if str(value).strip() in {"inn", "phone", "name"}
        ],
        "entries": entries,
    }


def _normalize_route_rate_profile(route_rate_profile: dict[str, Any]) -> dict[str, Any]:
    payload = _as_dict(route_rate_profile)
    stats_payload = _as_dict(payload.get("stats"))
    return {
        "from_city_norm": str(payload.get("from_city_norm") or "").strip(),
        "to_city_norm": str(payload.get("to_city_norm") or "").strip(),
        "min_rate_per_km": _to_int(payload.get("min_rate_per_km"), int(settings.ROUTE_RATE_FALLBACK_MIN)),
        "max_rate_per_km": _to_int(payload.get("max_rate_per_km"), int(settings.ROUTE_RATE_FALLBACK_MAX)),
        "source": str(payload.get("source") or "tier_fallback"),
        "cache": str(payload.get("cache") or "miss"),
        "stats": {
            "mean_rate": _to_float(stats_payload.get("mean_rate"), 0.0) if stats_payload.get("mean_rate") is not None else None,
            "median_rate": _to_float(stats_payload.get("median_rate"), 0.0) if stats_payload.get("median_rate") is not None else None,
            "std_dev": _to_float(stats_payload.get("std_dev"), 0.0) if stats_payload.get("std_dev") is not None else None,
            "p25": _to_float(stats_payload.get("p25"), 0.0) if stats_payload.get("p25") is not None else None,
            "p75": _to_float(stats_payload.get("p75"), 0.0) if stats_payload.get("p75") is not None else None,
            "sample_size": _to_int(stats_payload.get("sample_size"), 0),
            "updated_at": stats_payload.get("updated_at"),
        },
    }


def review_deal_rules_v2(
    deal: dict[str, Any],
    route_rate_profile: dict[str, Any],
    list_check: dict[str, Any],
) -> dict[str, Any]:
    payload = _as_dict(deal)
    route = _as_dict(payload.get("route"))
    cargo = _as_dict(payload.get("cargo"))
    price = _as_dict(payload.get("price"))
    payment = _as_dict(payload.get("payment"))
    dates = _as_dict(payload.get("dates"))
    counterparty = _as_dict(payload.get("counterparty"))
    notes = str(payload.get("notes") or "")

    profile = _normalize_route_rate_profile(route_rate_profile)
    list_payload = _normalize_list_check(list_check)

    score_total = 0
    score_breakdown: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    flags: dict[str, Any] = {}

    def add_score(code: str, points: int, details: dict[str, Any], flag_value: Any) -> None:
        nonlocal score_total
        score_total += int(points)
        reason_codes.append(code)
        score_breakdown.append({"code": code, "points": int(points), "details": details})
        if flag_value is not None:
            flags[code] = flag_value

    distance_km = _to_float(route.get("distance_km"))
    total_rub = _to_float(price.get("total_rub"))
    rate_per_km = _to_float(price.get("rate_per_km"))
    if rate_per_km <= 0 and distance_km > 0 and total_rub > 0:
        rate_per_km = total_rub / distance_km

    min_rate = _to_int(profile.get("min_rate_per_km"), int(settings.ROUTE_RATE_FALLBACK_MIN))
    max_rate = _to_int(profile.get("max_rate_per_km"), int(settings.ROUTE_RATE_FALLBACK_MAX))

    if distance_km > 0:
        if rate_per_km < min_rate:
            add_score(
                "price_too_low",
                3,
                {
                    "rate_per_km": round(rate_per_km, 2),
                    "min_rate_per_km": min_rate,
                },
                True,
            )
        if rate_per_km > max_rate:
            add_score(
                "price_too_high",
                1,
                {
                    "rate_per_km": round(rate_per_km, 2),
                    "max_rate_per_km": max_rate,
                },
                True,
            )

    prepay_percent = _to_int(payment.get("prepay_percent"))
    payment_type = str(payment.get("type") or "unknown").strip().lower()
    if prepay_percent >= 50:
        add_score("high_prepay", 3, {"prepay_percent": prepay_percent}, prepay_percent)
    if payment_type == "cash":
        add_score("cash_payment", 1, {"payment_type": payment_type}, True)

    notes_lc = notes.lower()
    matched_terms = [term for term in _SUSPICIOUS_TERMS if term in notes_lc]
    if matched_terms:
        matched_terms = _dedupe_strings(matched_terms)
        add_score("suspicious_words", 2, {"matched_terms": matched_terms}, matched_terms)

    if bool(counterparty.get("is_new")):
        add_score("new_counterparty", 1, {"is_new": True}, True)

    complaints_count = _to_int(counterparty.get("complaints_count"))
    if complaints_count > 0:
        add_score("has_complaints", 3, {"complaints_count": complaints_count}, complaints_count)

    trust_score = _to_float(counterparty.get("trust_score"))
    if trust_score and trust_score < int(settings.LOW_TRUST_SCORE_THRESHOLD):
        add_score("low_trust_score", 2, {"trust_score": trust_score}, trust_score)

    weight_t = _to_float(cargo.get("weight_t"))
    volume_m3 = _to_float(cargo.get("volume_m3"))
    if weight_t == 0 or volume_m3 == 0:
        add_score("missing_dimensions", 1, {"weight_t": weight_t, "volume_m3": volume_m3}, True)

    pickup_date = _safe_date(dates.get("pickup"))
    delivery_date = _safe_date(dates.get("delivery"))
    if pickup_date is None or delivery_date is None or delivery_date < pickup_date:
        add_score(
            "invalid_dates",
            2,
            {
                "pickup": str(dates.get("pickup") or ""),
                "delivery": str(dates.get("delivery") or ""),
            },
            True,
        )

    blacklist_match = bool(list_payload.get("blacklist_match"))
    whitelist_match = bool(list_payload.get("whitelist_match"))
    strict_mode = bool(settings.ANTIFRAUD_STRICT_MODE)

    if blacklist_match:
        points = 7 if strict_mode else 4
        add_score(
            "blacklist_match",
            points,
            {
                "strict_mode": strict_mode,
                "matched_fields": list_payload.get("matched_fields") or [],
            },
            True,
        )

    if whitelist_match and not blacklist_match:
        reason_codes.append("whitelist_match")
        score_breakdown.append(
            {
                "code": "whitelist_match",
                "points": 0,
                "details": {"matched_fields": list_payload.get("matched_fields") or []},
            }
        )
        flags["whitelist_match"] = True

    reason_codes = _dedupe_strings(reason_codes)
    risk_level = _risk_from_score_v2(score_total)

    if blacklist_match and strict_mode:
        risk_level = "high"
    elif whitelist_match and not blacklist_match:
        risk_level = _decrease_risk_one_level(risk_level)

    comment = _build_comment(flags, risk_level)
    recommended_action = _build_recommended_action(flags)

    return {
        "risk_level": risk_level,
        "flags": flags,
        "comment": comment,
        "recommended_action": recommended_action,
        "score_total": score_total,
        "score_breakdown": score_breakdown,
        "reason_codes": reason_codes,
        "risk_reason_codes": reason_codes,
        "route_rate_profile": profile,
        "list_check": list_payload,
        "serious": any(code in SERIOUS_REASON_CODES for code in reason_codes),
    }


def _normalize_history_summary(history_summary: dict[str, Any]) -> dict[str, Any]:
    payload = _as_dict(history_summary)
    return {
        "recent_count": _to_int(payload.get("recent_count"), 0),
        "high_risk_last5": _to_int(payload.get("high_risk_last5"), 0),
        "avg_score_total": _to_float(payload.get("avg_score_total"), 0.0),
    }


def review_deal_rules_v3(
    deal: dict[str, Any],
    route_rate_profile: dict[str, Any],
    list_check: dict[str, Any],
    history_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _as_dict(deal)
    route = _as_dict(payload.get("route"))
    cargo = _as_dict(payload.get("cargo"))
    price = _as_dict(payload.get("price"))
    payment = _as_dict(payload.get("payment"))
    dates = _as_dict(payload.get("dates"))
    counterparty = _as_dict(payload.get("counterparty"))
    notes = str(payload.get("notes") or "")

    profile = _normalize_route_rate_profile(route_rate_profile)
    list_payload = _normalize_list_check(list_check)
    history = _normalize_history_summary(history_summary or {})

    score_total = 0
    score_breakdown: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    flags: dict[str, Any] = {}

    def add_score(code: str, points: int, details: dict[str, Any], flag_value: Any) -> None:
        nonlocal score_total
        score_total += int(points)
        reason_codes.append(code)
        score_breakdown.append({"code": code, "points": int(points), "details": details})
        if flag_value is not None:
            flags[code] = flag_value

    distance_km = _to_float(route.get("distance_km"))
    total_rub = _to_float(price.get("total_rub"))
    rate_per_km = _to_float(price.get("rate_per_km"))
    if rate_per_km <= 0 and distance_km > 0 and total_rub > 0:
        rate_per_km = total_rub / distance_km

    min_rate = _to_int(profile.get("min_rate_per_km"), int(settings.ROUTE_RATE_FALLBACK_MIN))
    max_rate = _to_int(profile.get("max_rate_per_km"), int(settings.ROUTE_RATE_FALLBACK_MAX))

    stats = _as_dict(profile.get("stats"))
    stats_sample = _to_int(stats.get("sample_size"), 0)
    stats_mean = stats.get("mean_rate")
    stats_std = stats.get("std_dev")

    used_statistical_rule = False
    if (
        distance_km > 0
        and rate_per_km > 0
        and stats_sample >= 10
        and stats_mean is not None
        and stats_std is not None
        and float(stats_std) >= 5.0
    ):
        used_statistical_rule = True
        z_score = (rate_per_km - float(stats_mean)) / float(stats_std)
        if z_score < -2.5:
            add_score(
                "price_statistically_low",
                4,
                {
                    "z_score": round(z_score, 4),
                    "rate_per_km": round(rate_per_km, 2),
                    "mean_rate": float(stats_mean),
                    "std_dev": float(stats_std),
                    "sample_size": stats_sample,
                },
                True,
            )
        if z_score > 3:
            add_score(
                "price_statistically_high",
                2,
                {
                    "z_score": round(z_score, 4),
                    "rate_per_km": round(rate_per_km, 2),
                    "mean_rate": float(stats_mean),
                    "std_dev": float(stats_std),
                    "sample_size": stats_sample,
                },
                True,
            )

    # Fallback for routes with insufficient statistics or too small std_dev.
    if distance_km > 0 and not used_statistical_rule:
        if rate_per_km < min_rate:
            add_score(
                "price_too_low",
                3,
                {
                    "rate_per_km": round(rate_per_km, 2),
                    "min_rate_per_km": min_rate,
                },
                True,
            )
        if rate_per_km > max_rate:
            add_score(
                "price_too_high",
                1,
                {
                    "rate_per_km": round(rate_per_km, 2),
                    "max_rate_per_km": max_rate,
                },
                True,
            )

    prepay_percent = _to_int(payment.get("prepay_percent"))
    payment_type = str(payment.get("type") or "unknown").strip().lower()
    if prepay_percent >= 50:
        add_score("high_prepay", 3, {"prepay_percent": prepay_percent}, prepay_percent)
    if payment_type == "cash":
        add_score("cash_payment", 1, {"payment_type": payment_type}, True)

    notes_lc = notes.lower()
    matched_terms = [term for term in _SUSPICIOUS_TERMS if term in notes_lc]
    if matched_terms:
        matched_terms = _dedupe_strings(matched_terms)
        add_score("suspicious_words", 2, {"matched_terms": matched_terms}, matched_terms)

    if bool(counterparty.get("is_new")):
        add_score("new_counterparty", 1, {"is_new": True}, True)

    complaints_count = _to_int(counterparty.get("complaints_count"))
    if complaints_count > 0:
        add_score("has_complaints", 3, {"complaints_count": complaints_count}, complaints_count)

    trust_score = _to_float(counterparty.get("trust_score"))
    if trust_score and trust_score < int(settings.LOW_TRUST_SCORE_THRESHOLD):
        add_score("low_trust_score", 2, {"trust_score": trust_score}, trust_score)

    weight_t = _to_float(cargo.get("weight_t"))
    volume_m3 = _to_float(cargo.get("volume_m3"))
    if weight_t == 0 or volume_m3 == 0:
        add_score("missing_dimensions", 1, {"weight_t": weight_t, "volume_m3": volume_m3}, True)

    pickup_date = _safe_date(dates.get("pickup"))
    delivery_date = _safe_date(dates.get("delivery"))
    if pickup_date is None or delivery_date is None or delivery_date < pickup_date:
        add_score(
            "invalid_dates",
            2,
            {
                "pickup": str(dates.get("pickup") or ""),
                "delivery": str(dates.get("delivery") or ""),
            },
            True,
        )

    high_risk_last5 = _to_int(history.get("high_risk_last5"), 0)
    avg_score_total = _to_float(history.get("avg_score_total"), 0.0)
    if high_risk_last5 >= 3:
        add_score(
            "repeat_high_risk_pattern",
            4,
            {"high_risk_last5": high_risk_last5},
            True,
        )
    if avg_score_total > 6:
        add_score(
            "chronic_risk_profile",
            3,
            {"avg_score_total": round(avg_score_total, 3)},
            True,
        )

    blacklist_match = bool(list_payload.get("blacklist_match"))
    whitelist_match = bool(list_payload.get("whitelist_match"))
    strict_mode = bool(settings.ANTIFRAUD_STRICT_MODE)

    if blacklist_match:
        points = 7 if strict_mode else 4
        add_score(
            "blacklist_match",
            points,
            {
                "strict_mode": strict_mode,
                "matched_fields": list_payload.get("matched_fields") or [],
            },
            True,
        )

    if whitelist_match and not blacklist_match:
        reason_codes.append("whitelist_match")
        score_breakdown.append(
            {
                "code": "whitelist_match",
                "points": 0,
                "details": {"matched_fields": list_payload.get("matched_fields") or []},
            }
        )
        flags["whitelist_match"] = True

    reason_codes = _dedupe_strings(reason_codes)
    risk_level = _risk_from_score_v2(score_total)

    if blacklist_match and strict_mode:
        risk_level = "high"
    elif whitelist_match and not blacklist_match:
        risk_level = _decrease_risk_one_level(risk_level)

    escalation_triggered = False
    if blacklist_match:
        escalation_triggered = True
    if bool(flags.get("repeat_high_risk_pattern")):
        escalation_triggered = True
    if "price_statistically_low" in reason_codes and "high_prepay" in reason_codes:
        escalation_triggered = True

    if escalation_triggered:
        risk_level = "high"

    comment = _build_comment(flags, risk_level)
    recommended_action = _build_recommended_action(flags)

    return {
        "risk_level": risk_level,
        "flags": flags,
        "comment": comment,
        "recommended_action": recommended_action,
        "score_total": score_total,
        "score_breakdown": score_breakdown,
        "reason_codes": reason_codes,
        "risk_reason_codes": reason_codes,
        "route_rate_profile": profile,
        "list_check": list_payload,
        "history_summary": history,
        "escalation_triggered": escalation_triggered,
        "serious": any(code in SERIOUS_REASON_CODES for code in reason_codes),
    }


def _normalize_llm_output_v2(parsed: dict[str, Any], *, model: str, source: str) -> dict[str, Any]:
    risk_level = str(parsed.get("risk_level") or "").strip().lower()
    if risk_level not in _RISK_ORDER:
        return {}

    raw_flags = parsed.get("flags")
    if isinstance(raw_flags, dict):
        llm_flags = raw_flags
    elif isinstance(raw_flags, list):
        llm_flags = {str(item): True for item in raw_flags if str(item or "").strip()}
    else:
        llm_flags = {}

    if isinstance(llm_flags.get("suspicious_words"), str):
        llm_flags["suspicious_words"] = [llm_flags["suspicious_words"]]

    raw_codes = parsed.get("reason_codes")
    if not isinstance(raw_codes, list):
        raw_codes = parsed.get("risk_reason_codes")
    reason_codes = [str(code).strip() for code in _as_list(raw_codes) if str(code or "").strip()]

    doc_request_payload = _as_dict(parsed.get("doc_request"))
    doc_required = [
        doc
        for doc in _dedupe_strings([str(doc) for doc in _as_list(doc_request_payload.get("required_docs"))])
        if doc in DOC_CODES
    ]
    doc_reason_codes = _dedupe_strings([str(code) for code in _as_list(doc_request_payload.get("reason_codes"))])

    return {
        "risk_level": risk_level,
        "flags": llm_flags,
        "comment": str(parsed.get("comment") or "").strip(),
        "recommended_action": str(parsed.get("recommended_action") or "").strip(),
        "reason_codes": reason_codes,
        "doc_request": {
            "required_docs": doc_required,
            "reason_codes": doc_reason_codes,
            "message_template": str(doc_request_payload.get("message_template") or "").strip(),
        },
        "_llm_model": model,
        "_llm_source": source,
    }


async def review_deal_llm_v2(
    deal: dict[str, Any],
    rules_result: dict[str, Any],
    route_rate_profile: dict[str, Any],
) -> dict[str, Any]:
    payload = _as_dict(deal)
    route = _as_dict(payload.get("route"))
    cargo = _as_dict(payload.get("cargo"))
    price = _as_dict(payload.get("price"))
    payment = _as_dict(payload.get("payment"))
    counterparty = _as_dict(payload.get("counterparty"))
    notes = str(payload.get("notes") or "")[:600]

    profile = _normalize_route_rate_profile(route_rate_profile)
    breakdown_codes = [
        item.get("code")
        for item in _as_list(rules_result.get("score_breakdown"))
        if isinstance(item, dict) and str(item.get("code") or "").strip()
    ]

    prompt = (
        "Оцени риск сделки и верни только JSON."
        " Допускается добавлять причины и doc_request (только дополнять).\n"
        "Формат JSON: "
        '{"risk_level":"low|medium|high","flags":{},"comment":"...",'
        '"recommended_action":"...","reason_codes":[],"doc_request":{"required_docs":[],"reason_codes":[],"message_template":"..."}}\n\n'
        f"Маршрут: {route.get('from_city', '')} -> {route.get('to_city', '')}\n"
        f"distance_km: {route.get('distance_km', 0)}\n"
        f"total_rub: {price.get('total_rub', 0)}\n"
        f"rate_per_km: {price.get('rate_per_km', 0)}\n"
        f"route_rate_thresholds: min={profile.get('min_rate_per_km')} max={profile.get('max_rate_per_km')} source={profile.get('source')}\n"
        f"payment_type: {payment.get('type', 'unknown')} prepay_percent={payment.get('prepay_percent', 0)}\n"
        f"counterparty: name={counterparty.get('name', '')}, inn={counterparty.get('inn', '')}, "
        f"is_new={counterparty.get('is_new', False)}, complaints={counterparty.get('complaints_count', 0)}, "
        f"trust={counterparty.get('trust_score', 0)}\n"
        f"cargo: name={cargo.get('name', '')}, weight_t={cargo.get('weight_t', 0)}, volume_m3={cargo.get('volume_m3', 0)}\n"
        f"notes: {notes}\n"
        f"rules_reason_codes: {json.dumps(rules_result.get('reason_codes') or [], ensure_ascii=False)}\n"
        f"rules_score_breakdown_codes: {json.dumps(breakdown_codes, ensure_ascii=False)}\n"
    )

    started = time.perf_counter()
    try:
        response = await asyncio.to_thread(
            ai_service.ask,
            prompt=prompt,
            model_override=(settings.AI_ANTIFRAUD_LLM_MODEL or None),
            temperature=0.1,
            max_tokens=450,
            system_prompt=(
                "Ты anti-fraud ассистент логистической платформы. "
                "Отвечай только JSON. Не понижай риск ниже rules. "
                "Reason codes используй из доступных кодов правил."
            ),
        )
    except Exception as exc:
        logger.warning("antifraud.llm.v2.failed error=%s", exc)
        return {}

    duration_ms = int((time.perf_counter() - started) * 1000)
    llm_model = str(response.get("model") or settings.AI_ANTIFRAUD_LLM_MODEL or "llm")
    llm_source = str(response.get("source") or "unknown")
    logger.info(
        "antifraud.llm.v2.used source=%s model=%s duration_ms=%s",
        llm_source,
        llm_model,
        duration_ms,
    )

    parsed = _extract_first_json_object(str(response.get("text") or ""))
    if not parsed:
        return {}

    return _normalize_llm_output_v2(parsed, model=llm_model, source=llm_source)


def apply_llm_risk_policy_v2(rules_risk: str, llm_risk: str, serious: bool) -> str:
    base_rank = _RISK_ORDER.get(str(rules_risk or "low").lower(), 0)
    llm_rank = _RISK_ORDER.get(str(llm_risk or "low").lower(), 0)

    if llm_rank <= base_rank:
        return _RISK_BY_RANK.get(base_rank, "low")
    if not serious:
        return _RISK_BY_RANK.get(base_rank, "low")

    return _RISK_BY_RANK.get(min(base_rank + 1, llm_rank, _RISK_ORDER["high"]), "low")


def merge_reason_codes(
    rules_codes: list[str],
    llm_codes: list[str],
    *,
    max_extra: int = 2,
) -> list[str]:
    base_codes = _dedupe_strings([str(code) for code in rules_codes])
    extras = 0
    for code in _dedupe_strings([str(code) for code in llm_codes]):
        if code not in ALLOWED_REASON_CODES:
            continue
        if code in base_codes:
            continue
        if extras >= max_extra:
            break
        base_codes.append(code)
        extras += 1
    return base_codes


def merge_doc_request_additive(base_doc_request: dict[str, Any], llm_doc_request: dict[str, Any]) -> dict[str, Any]:
    base = _as_dict(base_doc_request)
    llm = _as_dict(llm_doc_request)

    base_docs = [doc for doc in _dedupe_strings([str(doc) for doc in _as_list(base.get("required_docs"))]) if doc in DOC_CODES]
    llm_docs = [doc for doc in _dedupe_strings([str(doc) for doc in _as_list(llm.get("required_docs"))]) if doc in DOC_CODES]

    merged_docs = _dedupe_strings([*base_docs, *llm_docs])[:5]
    merged_reasons = _dedupe_strings(
        [
            *[str(code) for code in _as_list(base.get("reason_codes"))],
            *[str(code) for code in _as_list(llm.get("reason_codes"))],
        ]
    )

    return {
        "required_docs": merged_docs,
        "reason_codes": merged_reasons,
        "message_template": str(base.get("message_template") or "").strip(),
    }
