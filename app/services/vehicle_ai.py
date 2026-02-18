from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Load, User, Vehicle


_GLOBAL_REGION_MARKERS = {"рф", "россия", "российская федерация", "all", "any"}


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def estimate_market_rate(
    db: Session,
    body_type: str,
    capacity_tons: float,
) -> Optional[float]:
    min_capacity = max(capacity_tons - 3.0, 0.0)
    max_capacity = capacity_tons + 3.0
    avg_rate = (
        db.query(func.avg(Vehicle.rate_per_km))
        .filter(Vehicle.status == "active")
        .filter(Vehicle.rate_per_km.isnot(None))
        .filter(Vehicle.body_type == body_type)
        .filter(Vehicle.capacity_tons >= min_capacity)
        .filter(Vehicle.capacity_tons <= max_capacity)
        .scalar()
    )
    return float(avg_rate) if avg_rate is not None else None


def calculate_idle_ratio(
    db: Session,
    carrier_id: int,
    *,
    as_of: Optional[date] = None,
) -> float:
    today = as_of or date.today()
    stale_date = today - timedelta(days=7)
    total = db.query(Vehicle).filter(Vehicle.carrier_id == carrier_id).count()
    if total == 0:
        return 0.0
    stale_active = (
        db.query(Vehicle)
        .filter(Vehicle.carrier_id == carrier_id)
        .filter(Vehicle.status == "active")
        .filter(Vehicle.available_from <= stale_date)
        .count()
    )
    return stale_active / total


def count_matching_loads(
    db: Session,
    *,
    capacity_tons: float,
    location_city: str,
    location_region: Optional[str],
    available_from: date,
) -> int:
    # В текущей модели груза нет отдельной даты погрузки, поэтому
    # для MVP используем простой критерий: машина доступна не в будущем.
    if available_from > date.today():
        return 0

    city = _norm(location_city)
    region = _norm(location_region)
    global_region = city in _GLOBAL_REGION_MARKERS or region in _GLOBAL_REGION_MARKERS
    geo_tokens = {token for token in (city, region) if token}

    loads = db.query(Load).filter(Load.status == "open").all()
    matched = 0
    for load in loads:
        weight = float(load.weight or 0.0)
        if capacity_tons < weight:
            continue

        if not global_region:
            from_city = _norm(load.from_city)
            if not any(token in from_city for token in geo_tokens):
                continue

        matched += 1
    return matched


def analyze_vehicle_submission(
    db: Session,
    *,
    carrier: User,
    body_type: str,
    capacity_tons: float,
    location_city: str,
    location_region: Optional[str],
    available_from: date,
    rate_per_km: Optional[float],
) -> dict:
    warnings: list[str] = []
    score = 0

    market_rate = estimate_market_rate(db, body_type=body_type, capacity_tons=capacity_tons)
    if rate_per_km is not None and market_rate and market_rate > 0:
        deviation = abs(rate_per_km - market_rate) / market_rate
        if deviation >= 0.5:
            score += 30
            warnings.append(
                f"Ставка заметно отклоняется от рынка: {rate_per_km:.1f} ₽/км против ~{market_rate:.1f} ₽/км."
            )
        elif deviation >= 0.3:
            score += 15
            warnings.append(
                f"Ставка отличается от среднего рынка на {int(deviation * 100)}%."
            )
    elif rate_per_km is None:
        warnings.append("Ставка за км не указана: AI-оценка цены менее точная.")

    idle_ratio = calculate_idle_ratio(db, carrier.id)
    if idle_ratio >= 0.7:
        score += 25
        warnings.append("Высокая доля простаивающих машин у перевозчика.")
    elif idle_ratio >= 0.4:
        score += 10
        warnings.append("Есть признаки повышенного простоя парка.")

    if not carrier.verified:
        score += 20
        warnings.append("Перевозчик не верифицирован.")

    if (carrier.trust_level or "new") == "new":
        score += 20
        warnings.append("Низкий trust level перевозчика (new).")

    if (carrier.complaints or 0) >= 3:
        score += 25
        warnings.append("У перевозчика много жалоб.")
    elif (carrier.complaints or 0) > 0:
        score += 10

    if (carrier.disputes or 0) >= 2:
        score += 15
        warnings.append("У перевозчика есть повторяющиеся споры.")

    rating = float(carrier.rating or 0.0)
    if rating and rating < 4.0:
        score += 20
        warnings.append(f"Рейтинг перевозчика низкий ({rating:.1f}).")
    elif rating and rating < 4.5:
        score += 10

    if score >= 65:
        risk_level = "high"
    elif score >= 35:
        risk_level = "medium"
    else:
        risk_level = "low"

    matching_loads = count_matching_loads(
        db,
        capacity_tons=capacity_tons,
        location_city=location_city,
        location_region=location_region,
        available_from=available_from,
    )

    return {
        "risk_level": risk_level,
        "score": score,
        "warnings": warnings,
        "market_rate_per_km": market_rate,
        "idle_ratio": round(idle_ratio, 3),
        "matching_loads": matching_loads,
    }
