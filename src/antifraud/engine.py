from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from src.core.config import settings


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

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value
        return datetime.strptime(str(value).strip(), "%Y-%m-%d")
    except Exception:
        return None


def _risk_from_score(score: int) -> str:
    if score >= 7:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _decrease_risk_one_level(level: str) -> str:
    rank = max(0, _RISK_ORDER.get(level, 0) - 1)
    for key, value in _RISK_ORDER.items():
        if value == rank:
            return key
    return "low"


def _add_flag(
    *,
    flags: dict[str, Any],
    breakdown: list[dict[str, Any]],
    reason_codes: list[str],
    code: str,
    points: int,
    details: Any = True,
) -> int:
    if code not in reason_codes:
        reason_codes.append(code)
    flags[code] = details
    breakdown.append({"code": code, "points": points, "details": details})
    return points


def _comment_from_flags(flags: dict[str, Any], risk_level: str) -> str:
    if not flags:
        return "Правила не выявили явных антифрод-рисков."
    labels = []
    for key in ("blacklist_match", "high_prepay", "has_complaints", "price_too_low", "low_trust_score"):
        if key in flags:
            labels.append(key)
        if len(labels) == 2:
            break
    if not labels:
        labels = list(flags.keys())[:2]
    if len(labels) == 1:
        return f"Обнаружен риск: {labels[0]}. Уровень: {risk_level}."
    return f"Ключевые риски: {labels[0]} и {labels[1]}. Уровень: {risk_level}."


def _recommended_action(flags: dict[str, Any]) -> str:
    actions: list[str] = []
    if any(key in flags for key in ("high_prepay", "cash_payment")):
        actions.append("Не давать предоплату; согласовать оплату по безналу/эскроу")
    if any(key in flags for key in ("has_complaints", "low_trust_score", "new_counterparty", "blacklist_match")):
        actions.append("Проверить ИНН/жалобы и историю контрагента")
    if any(key in flags for key in ("suspicious_words", "price_too_low", "invalid_dates", "missing_dimensions")):
        actions.append("Запросить договор/заявку и реквизиты юрлица")
    if not actions:
        actions.append("Продолжить сделку по стандартному регламенту проверки")
    return "; ".join(actions[:3])


def review_deal_rules_v2(
    deal: dict[str, Any],
    route_rate_profile: dict[str, Any],
    list_check: dict[str, Any],
) -> dict[str, Any]:
    payload = deal if isinstance(deal, dict) else {}
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    cargo = payload.get("cargo") if isinstance(payload.get("cargo"), dict) else {}
    price = payload.get("price") if isinstance(payload.get("price"), dict) else {}
    payment = payload.get("payment") if isinstance(payload.get("payment"), dict) else {}
    dates = payload.get("dates") if isinstance(payload.get("dates"), dict) else {}
    counterparty = payload.get("counterparty") if isinstance(payload.get("counterparty"), dict) else {}
    notes = str(payload.get("notes") or "")

    flags: dict[str, Any] = {}
    score_breakdown: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    score = 0

    # A) Price anomalies
    distance_km = _to_float(route.get("distance_km"), 0.0)
    rate_per_km = _to_float(price.get("rate_per_km"), 0.0)
    min_rate = int(route_rate_profile.get("min_rate_per_km", settings.min_rate_per_km))
    max_rate = int(route_rate_profile.get("max_rate_per_km", settings.max_rate_per_km))
    if distance_km > 0:
        if rate_per_km < min_rate:
            score += _add_flag(
                flags=flags,
                breakdown=score_breakdown,
                reason_codes=reason_codes,
                code="price_too_low",
                points=3,
                details={"rate_per_km": rate_per_km, "min": min_rate},
            )
        if rate_per_km > max_rate:
            score += _add_flag(
                flags=flags,
                breakdown=score_breakdown,
                reason_codes=reason_codes,
                code="price_too_high",
                points=1,
                details={"rate_per_km": rate_per_km, "max": max_rate},
            )

    # B) Prepay/cash
    prepay_percent = _to_float(payment.get("prepay_percent"), 0.0)
    payment_type = str(payment.get("type") or "unknown").strip().lower()
    if prepay_percent >= 50:
        score += _add_flag(
            flags=flags,
            breakdown=score_breakdown,
            reason_codes=reason_codes,
            code="high_prepay",
            points=3,
            details=prepay_percent,
        )
    if payment_type == "cash":
        score += _add_flag(
            flags=flags,
            breakdown=score_breakdown,
            reason_codes=reason_codes,
            code="cash_payment",
            points=1,
        )

    # C) Suspicious words
    notes_lower = notes.lower()
    matches = [term for term in _SUSPICIOUS_TERMS if re.search(re.escape(term), notes_lower, re.IGNORECASE)]
    if matches:
        score += _add_flag(
            flags=flags,
            breakdown=score_breakdown,
            reason_codes=reason_codes,
            code="suspicious_words",
            points=2,
            details=matches,
        )

    # D) Counterparty
    if bool(counterparty.get("is_new")):
        score += _add_flag(
            flags=flags,
            breakdown=score_breakdown,
            reason_codes=reason_codes,
            code="new_counterparty",
            points=1,
        )
    complaints_count = int(_to_float(counterparty.get("complaints_count"), 0.0))
    if complaints_count > 0:
        score += _add_flag(
            flags=flags,
            breakdown=score_breakdown,
            reason_codes=reason_codes,
            code="has_complaints",
            points=3,
            details=complaints_count,
        )
    trust_score = _to_float(counterparty.get("trust_score"), 0.0)
    if trust_score and trust_score < float(settings.low_trust_score_threshold):
        score += _add_flag(
            flags=flags,
            breakdown=score_breakdown,
            reason_codes=reason_codes,
            code="low_trust_score",
            points=2,
            details=trust_score,
        )

    # E) Inconsistencies
    weight_t = _to_float(cargo.get("weight_t"), 0.0)
    volume_m3 = _to_float(cargo.get("volume_m3"), 0.0)
    if weight_t == 0 or volume_m3 == 0:
        score += _add_flag(
            flags=flags,
            breakdown=score_breakdown,
            reason_codes=reason_codes,
            code="missing_dimensions",
            points=1,
            details={"weight_t": weight_t, "volume_m3": volume_m3},
        )

    pickup = _safe_date(dates.get("pickup"))
    delivery = _safe_date(dates.get("delivery"))
    if pickup is None or delivery is None or delivery < pickup:
        score += _add_flag(
            flags=flags,
            breakdown=score_breakdown,
            reason_codes=reason_codes,
            code="invalid_dates",
            points=2,
            details={"pickup": dates.get("pickup"), "delivery": dates.get("delivery")},
        )

    # List policy
    whitelist_match = bool(list_check.get("whitelist_match"))
    blacklist_match = bool(list_check.get("blacklist_match"))

    if blacklist_match:
        if settings.antifraud_strict_mode:
            score += _add_flag(
                flags=flags,
                breakdown=score_breakdown,
                reason_codes=reason_codes,
                code="blacklist_match",
                points=7,
                details=list_check.get("matched_fields", []),
            )
        else:
            score += _add_flag(
                flags=flags,
                breakdown=score_breakdown,
                reason_codes=reason_codes,
                code="blacklist_match",
                points=4,
                details=list_check.get("matched_fields", []),
            )

    risk_level = _risk_from_score(score)
    if whitelist_match and not blacklist_match:
        flags["whitelist_match"] = list_check.get("matched_fields", [])
        if "whitelist_match" not in reason_codes:
            reason_codes.append("whitelist_match")
        risk_level = _decrease_risk_one_level(risk_level)

    return {
        "risk_level": risk_level,
        "flags": flags,
        "comment": _comment_from_flags(flags, risk_level),
        "recommended_action": _recommended_action(flags),
        "score_total": score,
        "score_breakdown": score_breakdown,
        "reason_codes": reason_codes,
        "route_rate_profile": route_rate_profile,
        "list_check": list_check,
    }


def _normalize_history_summary(history_summary: dict[str, Any]) -> dict[str, Any]:
    payload = history_summary if isinstance(history_summary, dict) else {}
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
    payload = deal if isinstance(deal, dict) else {}
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    cargo = payload.get("cargo") if isinstance(payload.get("cargo"), dict) else {}
    price = payload.get("price") if isinstance(payload.get("price"), dict) else {}
    payment = payload.get("payment") if isinstance(payload.get("payment"), dict) else {}
    dates = payload.get("dates") if isinstance(payload.get("dates"), dict) else {}
    counterparty = payload.get("counterparty") if isinstance(payload.get("counterparty"), dict) else {}
    notes = str(payload.get("notes") or "")

    profile = route_rate_profile if isinstance(route_rate_profile, dict) else {}
    list_payload = list_check if isinstance(list_check, dict) else {}
    history = _normalize_history_summary(history_summary or {})

    flags: dict[str, Any] = {}
    score_breakdown: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    score = 0

    def add_score(code: str, points: int, details: Any = True, flag_value: Any = True) -> None:
        nonlocal score
        score += int(points)
        if code not in reason_codes:
            reason_codes.append(code)
        score_breakdown.append({"code": code, "points": int(points), "details": details})
        flags[code] = flag_value

    distance_km = _to_float(route.get("distance_km"), 0.0)
    total_rub = _to_float(price.get("total_rub"), 0.0)
    rate_per_km = _to_float(price.get("rate_per_km"), 0.0)
    if rate_per_km <= 0 and distance_km > 0 and total_rub > 0:
        rate_per_km = total_rub / distance_km

    min_rate = _to_int(profile.get("min_rate_per_km"), int(settings.route_rate_fallback_min))
    max_rate = _to_int(profile.get("max_rate_per_km"), int(settings.route_rate_fallback_max))
    stats = profile.get("stats") if isinstance(profile.get("stats"), dict) else {}
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

    if distance_km > 0 and not used_statistical_rule:
        if rate_per_km < min_rate:
            add_score(
                "price_too_low",
                3,
                {"rate_per_km": round(rate_per_km, 2), "min_rate_per_km": min_rate},
                True,
            )
        if rate_per_km > max_rate:
            add_score(
                "price_too_high",
                1,
                {"rate_per_km": round(rate_per_km, 2), "max_rate_per_km": max_rate},
                True,
            )

    prepay_percent = _to_int(payment.get("prepay_percent"), 0)
    payment_type = str(payment.get("type") or "unknown").strip().lower()
    if prepay_percent >= 50:
        add_score("high_prepay", 3, {"prepay_percent": prepay_percent}, prepay_percent)
    if payment_type == "cash":
        add_score("cash_payment", 1, {"payment_type": payment_type}, True)

    notes_lc = notes.lower()
    matched_terms = [term for term in _SUSPICIOUS_TERMS if term in notes_lc]
    if matched_terms:
        add_score("suspicious_words", 2, {"matched_terms": matched_terms}, matched_terms)

    if bool(counterparty.get("is_new")):
        add_score("new_counterparty", 1, {"is_new": True}, True)

    complaints_count = _to_int(counterparty.get("complaints_count"), 0)
    if complaints_count > 0:
        add_score("has_complaints", 3, {"complaints_count": complaints_count}, complaints_count)

    trust_score = _to_float(counterparty.get("trust_score"), 0.0)
    if trust_score and trust_score < float(settings.low_trust_score_threshold):
        add_score("low_trust_score", 2, {"trust_score": trust_score}, trust_score)

    weight_t = _to_float(cargo.get("weight_t"), 0.0)
    volume_m3 = _to_float(cargo.get("volume_m3"), 0.0)
    if weight_t == 0 or volume_m3 == 0:
        add_score("missing_dimensions", 1, {"weight_t": weight_t, "volume_m3": volume_m3}, True)

    pickup = _safe_date(dates.get("pickup"))
    delivery = _safe_date(dates.get("delivery"))
    if pickup is None or delivery is None or delivery < pickup:
        add_score(
            "invalid_dates",
            2,
            {"pickup": str(dates.get("pickup") or ""), "delivery": str(dates.get("delivery") or "")},
            True,
        )

    high_risk_last5 = _to_int(history.get("high_risk_last5"), 0)
    avg_score_total = _to_float(history.get("avg_score_total"), 0.0)
    if high_risk_last5 >= 3:
        add_score("repeat_high_risk_pattern", 4, {"high_risk_last5": high_risk_last5}, True)
    if avg_score_total > 6:
        add_score("chronic_risk_profile", 3, {"avg_score_total": round(avg_score_total, 3)}, True)

    whitelist_match = bool(list_payload.get("whitelist_match"))
    blacklist_match = bool(list_payload.get("blacklist_match"))

    if blacklist_match:
        points = 7 if settings.antifraud_strict_mode else 4
        add_score(
            "blacklist_match",
            points,
            {
                "strict_mode": bool(settings.antifraud_strict_mode),
                "matched_fields": list_payload.get("matched_fields") or [],
            },
            True,
        )

    if whitelist_match and not blacklist_match:
        if "whitelist_match" not in reason_codes:
            reason_codes.append("whitelist_match")
        score_breakdown.append(
            {
                "code": "whitelist_match",
                "points": 0,
                "details": {"matched_fields": list_payload.get("matched_fields") or []},
            }
        )
        flags["whitelist_match"] = True

    risk_level = _risk_from_score(score)
    if blacklist_match and settings.antifraud_strict_mode:
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

    return {
        "risk_level": risk_level,
        "flags": flags,
        "comment": _comment_from_flags(flags, risk_level),
        "recommended_action": _recommended_action(flags),
        "score_total": score,
        "score_breakdown": score_breakdown,
        "reason_codes": reason_codes,
        "route_rate_profile": profile,
        "list_check": list_payload,
        "history_summary": history,
        "escalation_triggered": escalation_triggered,
    }
