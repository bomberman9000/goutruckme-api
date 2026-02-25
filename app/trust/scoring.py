from __future__ import annotations

from typing import Any


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def trust_score_to_stars(score: int) -> int:
    if score <= 20:
        return 1
    if score <= 40:
        return 2
    if score <= 60:
        return 3
    if score <= 80:
        return 4
    return 5


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return True
    return bool(str(value).strip())


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if _is_non_empty(value):
            return str(value).strip()
    return ""


def _deals_total_bucket(deals_total: int) -> str:
    if deals_total <= 2:
        return "0-2"
    if deals_total <= 9:
        return "3-9"
    if deals_total <= 29:
        return "10-29"
    return "30+"


def _history_component(deals_total: int) -> int:
    if deals_total >= 30:
        return 10
    if deals_total >= 10:
        return 6
    if deals_total >= 3:
        return 3
    return 0


def compute_profile_completeness(company: Any) -> float:
    """
    Чек-лист заполненности профиля компании (0..1).
    Адаптирован под фактические поля модели User и graceful fallback.
    """
    if not company:
        return 0.0

    name_value = _first_non_empty(
        getattr(company, "organization_name", None),
        getattr(company, "legal_name", None),
        getattr(company, "company", None),
        getattr(company, "name", None),
        getattr(company, "fullname", None),
    )
    phone_value = _first_non_empty(getattr(company, "phone", None))
    tax_value = _first_non_empty(
        getattr(company, "inn", None),
        getattr(company, "ogrn", None),
        getattr(company, "ogrnip", None),
    )
    location_value = _first_non_empty(
        getattr(company, "city", None),
        getattr(company, "region", None),
        getattr(company, "location_city", None),
        getattr(company, "location_region", None),
        getattr(company, "address", None),
    )

    explicit_contact = _first_non_empty(
        getattr(company, "contact_person", None),
        getattr(company, "contact_name", None),
        getattr(company, "responsible_person", None),
        getattr(company, "director_name", None),
    )
    fullname = _first_non_empty(getattr(company, "fullname", None))
    contact_value = explicit_contact or (fullname if fullname and fullname != name_value else "")

    role_or_type_value = _first_non_empty(
        getattr(company, "role", None),
        getattr(company, "organization_type", None),
        getattr(company, "company_type", None),
    )

    docs_or_edo = any(
        bool(getattr(company, attr, False))
        for attr in ("verified", "payment_confirmed", "edo_enabled", "docs_verified")
    )

    checklist = {
        "name": _is_non_empty(name_value),
        "tax_id": _is_non_empty(tax_value),
        "phone": _is_non_empty(phone_value),
        "location": _is_non_empty(location_value),
        "contact_person": _is_non_empty(contact_value),
        "role_or_type": _is_non_empty(role_or_type_value),
        "docs_or_edo": docs_or_edo,
    }

    total = len(checklist)
    filled = sum(1 for value in checklist.values() if value)
    if total <= 0:
        return 0.0
    return round(clamp(filled / total, 0.0, 1.0), 4)


def compute_trust(company_id: int, ctx: dict[str, Any]) -> dict[str, Any]:
    """
    Прозрачный trust score (0..100) + explainability.

    ctx ожидает сигналы:
    - company_age_days
    - deals_total, deals_success, success_rate
    - disputes_total, disputes_confirmed
    - flags_total, flags_high
    - profile_completeness
    - response_time_avg_min
    """
    company_age_days = max(_to_int(ctx.get("company_age_days"), 0), 0)
    deals_total = max(_to_int(ctx.get("deals_total"), 0), 0)
    deals_success = max(_to_int(ctx.get("deals_success"), 0), 0)
    disputes_total = max(_to_int(ctx.get("disputes_total"), 0), 0)
    disputes_confirmed = max(_to_int(ctx.get("disputes_confirmed"), 0), 0)
    flags_total = max(_to_int(ctx.get("flags_total"), 0), 0)
    flags_high = max(_to_int(ctx.get("flags_high"), 0), 0)
    profile_completeness = clamp(_to_float(ctx.get("profile_completeness"), 0.0) or 0.0, 0.0, 1.0)
    response_time_avg_min = _to_float(ctx.get("response_time_avg_min"), None)

    success_rate = _to_float(ctx.get("success_rate"), None)
    if success_rate is None and deals_total > 0:
        success_rate = clamp(deals_success / max(deals_total, 1), 0.0, 1.0)
    elif success_rate is not None:
        success_rate = clamp(success_rate, 0.0, 1.0)

    has_activity = (
        deals_total > 0
        or disputes_total > 0
        or flags_total > 0
        or response_time_avg_min is not None
    )
    deals_total_bucket = _deals_total_bucket(deals_total)
    signals = {
        "company_age_days": company_age_days,
        "deals_total": deals_total,
        "deals_total_bucket": deals_total_bucket,
        "deals_success": deals_success,
        "success_rate": success_rate,
        "disputes_total": disputes_total,
        "disputes_confirmed": disputes_confirmed,
        "flags_total": flags_total,
        "flags_high": flags_high,
        "profile_completeness": profile_completeness,
        "response_time_avg_min": response_time_avg_min,
    }

    if not has_activity:
        components = {
            "history": 0,
            "success": 0,
            "disputes": 0,
            "risk": 0,
            "profile": 0,
            "speed": 0,
        }
        return {
            "company_id": company_id,
            "trust_score": 50,
            "stars": 3,
            "components": components,
            "signals": signals,
            "flags": ["insufficient_data"],
        }

    base = 50

    history = _history_component(deals_total)

    success = int(round((success_rate or 0.0) * 15.0)) if success_rate is not None else 0
    success = int(clamp(success, 0, 15))

    disputes_penalty = int(min(25, disputes_total * 3 + disputes_confirmed * 6))
    disputes = -disputes_penalty

    risk_penalty = int(min(20, flags_total * 2 + flags_high * 6))
    risk = -risk_penalty

    profile_points = int(round(clamp(profile_completeness * 10.0, 0.0, 10.0)))
    profile = profile_points

    speed = 0
    if response_time_avg_min is not None:
        if response_time_avg_min <= 15:
            speed = 10
        elif response_time_avg_min <= 60:
            speed = 8
        elif response_time_avg_min <= 180:
            speed = 5
        elif response_time_avg_min <= 720:
            speed = 2
        else:
            speed = 0

    components = {
        "history": history,
        "success": success,
        "disputes": disputes,
        "risk": risk,
        "profile": profile,
        "speed": speed,
    }

    raw_score = base + sum(components.values())
    trust_score = int(round(clamp(raw_score, 0.0, 100.0)))

    flags: list[str] = []
    if company_age_days < 45 and deals_total < 3:
        flags.append("new_company")
    if deals_total < 3:
        flags.append("low_history")
    if disputes_confirmed > 0:
        flags.append("disputes_confirmed")
    if flags_high > 0:
        flags.append("high_risk_flags")
    if profile_completeness < 0.2:
        flags.append("profile_empty")
    if response_time_avg_min is not None and response_time_avg_min > 240:
        flags.append("slow_response")

    capped_score = trust_score
    cap_applied = False
    if deals_total < 5 and capped_score > 65:
        capped_score = 65
        cap_applied = True
    elif deals_total < 10 and capped_score > 75:
        capped_score = 75
        cap_applied = True
    elif deals_total < 20 and capped_score > 85:
        capped_score = 85
        cap_applied = True

    trust_score = capped_score
    stars = trust_score_to_stars(trust_score)
    if deals_total < 5 and stars > 3:
        stars = 3
        cap_applied = True

    if cap_applied and "cold_start_cap" not in flags:
        flags.append("cold_start_cap")

    return {
        "company_id": company_id,
        "trust_score": trust_score,
        "stars": stars,
        "components": components,
        "signals": signals,
        "flags": flags,
    }
