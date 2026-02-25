from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import Load
from app.services.ai_logist import AILogist


RISK_PENALTIES = {"low": 0, "medium": 15, "high": 35}
SCORE_BASE = 60.0
_DEFAULT_VEHICLE_TYPE = "generic"
_ai_logist = AILogist()


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _get_value(load: Any, key: str, default: Any = None) -> Any:
    if isinstance(load, dict):
        return load.get(key, default)
    return getattr(load, key, default)


def _normalize_city(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_vehicle_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized or _DEFAULT_VEHICLE_TYPE


def _normalize_risk(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"low", "medium", "high"}:
        return "low"
    return normalized


def infer_distance_km(load: Any) -> float | None:
    direct = _safe_float(
        _get_value(load, "distance")
        or _get_value(load, "distance_km")
        or _get_value(load, "distanceKm")
    )
    if direct is not None and direct > 0:
        return direct

    from_city = str(_get_value(load, "from_city") or "").strip()
    to_city = str(_get_value(load, "to_city") or "").strip()
    if not from_city or not to_city:
        return None
    try:
        inferred = float(_ai_logist.get_distance(from_city, to_city))
        return inferred if inferred > 0 else None
    except Exception:
        return None


def _extract_load_features(load: Any) -> dict[str, Any]:
    price = _safe_float(_get_value(load, "price") or _get_value(load, "price_total")) or 0.0
    distance_km = infer_distance_km(load)
    rate_per_km = (price / distance_km) if distance_km and distance_km > 0 else None

    return {
        "id": _get_value(load, "id"),
        "from_city": str(_get_value(load, "from_city") or "").strip(),
        "to_city": str(_get_value(load, "to_city") or "").strip(),
        "from_city_key": _normalize_city(_get_value(load, "from_city")),
        "to_city_key": _normalize_city(_get_value(load, "to_city")),
        "vehicle_type_key": _normalize_vehicle_type(
            _get_value(load, "truck_type")
            or _get_value(load, "vehicle_type")
            or _get_value(load, "body_type")
        ),
        "price": price,
        "distance_km": distance_km,
        "rate_per_km": rate_per_km,
        "risk_level": _normalize_risk(_get_value(load, "ai_risk") or _get_value(load, "risk_level")),
        "created_at": _safe_datetime(_get_value(load, "created_at")),
    }


@dataclass
class RatePoint:
    from_city_key: str
    to_city_key: str
    vehicle_type_key: str
    rate_per_km: float


class MarketStats:
    def __init__(self, points: list[RatePoint]):
        self.points = points
        self._cache: dict[tuple[str, ...], tuple[float, int]] = {}

    @classmethod
    def from_rate_points(cls, points: list[RatePoint]) -> "MarketStats":
        return cls(points=points)

    @classmethod
    def from_db(cls, db: Session, lookback_days: int = 60) -> "MarketStats":
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        rows = db.query(Load).all()
        points: list[RatePoint] = []

        for row in rows:
            created_at = _safe_datetime(getattr(row, "created_at", None))
            if created_at and created_at < cutoff:
                continue

            features = _extract_load_features(row)
            rate = features["rate_per_km"]
            if rate is None or rate <= 0:
                continue

            points.append(
                RatePoint(
                    from_city_key=features["from_city_key"],
                    to_city_key=features["to_city_key"],
                    vehicle_type_key=features["vehicle_type_key"],
                    rate_per_km=float(rate),
                )
            )

        return cls(points=points)

    def _median_with_count(self, key: tuple[str, ...], predicate) -> tuple[float, int]:
        if key in self._cache:
            return self._cache[key]

        values = [point.rate_per_km for point in self.points if predicate(point)]
        if not values:
            result = (0.0, 0)
        else:
            result = (float(median(values)), len(values))
        self._cache[key] = result
        return result

    def median_for_route_vehicle(self, from_city_key: str, to_city_key: str, vehicle_type_key: str) -> tuple[float, int]:
        key = ("route_vehicle", from_city_key, to_city_key, vehicle_type_key)
        return self._median_with_count(
            key,
            lambda p: (
                p.from_city_key == from_city_key
                and p.to_city_key == to_city_key
                and p.vehicle_type_key == vehicle_type_key
            ),
        )

    def median_for_destination_vehicle(self, to_city_key: str, vehicle_type_key: str) -> tuple[float, int]:
        key = ("to_vehicle", to_city_key, vehicle_type_key)
        return self._median_with_count(
            key,
            lambda p: p.to_city_key == to_city_key and p.vehicle_type_key == vehicle_type_key,
        )

    def median_for_vehicle(self, vehicle_type_key: str) -> tuple[float, int]:
        key = ("vehicle", vehicle_type_key)
        return self._median_with_count(
            key,
            lambda p: p.vehicle_type_key == vehicle_type_key,
        )


def compute_market_median(load: Any, stats: MarketStats) -> tuple[float, int, str]:
    features = _extract_load_features(load)
    from_city_key = features["from_city_key"]
    to_city_key = features["to_city_key"]
    vehicle_type_key = features["vehicle_type_key"]

    route_median, route_count = stats.median_for_route_vehicle(from_city_key, to_city_key, vehicle_type_key)
    if route_count >= 10:
        return route_median, route_count, "route_bucket"

    dest_median, dest_count = stats.median_for_destination_vehicle(to_city_key, vehicle_type_key)
    if dest_count >= 10:
        return dest_median, dest_count, "destination_bucket"

    vehicle_median, vehicle_count = stats.median_for_vehicle(vehicle_type_key)
    if vehicle_count > 0:
        return vehicle_median, vehicle_count, "vehicle_bucket"

    return 0.0, 0, "none"


def build_explain(result: dict[str, Any]) -> str:
    rate_per_km = result.get("rate_per_km")
    market_rate = result.get("market_rate_per_km")
    market_source = result.get("market_source")
    risk_level = result.get("ai_risk") or "low"
    risk_penalty = int(result.get("risk_penalty") or 0)
    flags = result.get("ai_flags") or []

    if isinstance(rate_per_km, (int, float)):
        rate_text = f"{rate_per_km:.1f}"
    else:
        rate_text = "—"

    if isinstance(market_rate, (int, float)) and market_rate > 0:
        if market_source == "route_bucket":
            source_label = "маршрут+тип"
        elif market_source == "destination_bucket":
            source_label = "направление+тип"
        elif market_source == "vehicle_bucket":
            source_label = "тип ТС"
        else:
            source_label = "рынок"
        base = f"Ставка {rate_text} ₽/км, рынок {market_rate:.1f} ₽/км ({source_label})."
    else:
        base = f"Ставка {rate_text} ₽/км; рыночной статистики недостаточно, использован fallback."

    if risk_penalty > 0:
        extra = f" Риск {risk_level} снизил оценку на {risk_penalty}."
    else:
        extra = " Риск не уменьшал итоговый балл."

    if flags:
        extra += " " + "; ".join(flags[:2]) + "."

    return (base + extra).strip()


def compute_ai_score(load: Any, stats: MarketStats) -> dict[str, Any]:
    features = _extract_load_features(load)
    market_rate, market_count, market_source = compute_market_median(load, stats)

    rate_per_km = features["rate_per_km"]
    risk_level = features["risk_level"]
    risk_penalty = int(RISK_PENALTIES.get(risk_level, 0))

    if market_rate > 0 and isinstance(rate_per_km, (int, float)):
        rate_delta = (rate_per_km - market_rate) / market_rate
        score_rate = clamp(rate_delta * 40.0, -20.0, 25.0)
    else:
        score_rate = 0.0

    distance_km = features["distance_km"]
    score_distance = 0.0
    if isinstance(distance_km, (int, float)):
        if distance_km <= 300:
            score_distance = 5.0
        elif distance_km > 1200:
            score_distance = -5.0

    score_raw = SCORE_BASE + score_rate + score_distance - risk_penalty
    score = int(round(clamp(score_raw, 0.0, 100.0)))

    ai_flags: list[str] = []
    if market_rate > 0 and isinstance(rate_per_km, (int, float)):
        if rate_per_km < market_rate * 0.65:
            ai_flags.append("Ставка подозрительно низкая")
        if rate_per_km > market_rate * 1.35:
            ai_flags.append("Ставка выше рынка")

    result = {
        "ai_score": score,
        "ai_risk": risk_level,
        "risk_penalty": risk_penalty,
        "rate_per_km": round(rate_per_km, 2) if isinstance(rate_per_km, (int, float)) else None,
        "distance_km": round(distance_km, 2) if isinstance(distance_km, (int, float)) else None,
        "market_rate_per_km": round(market_rate, 2) if market_rate > 0 else 0,
        "market_sample_size": market_count,
        "market_source": market_source,
        "score_rate": round(score_rate, 2),
        "score_distance": round(score_distance, 2),
        "ai_flags": ai_flags,
    }
    result["ai_explain"] = build_explain(result)
    return result


def score_loads(loads: list[Any], stats: MarketStats | None = None) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    stats_obj = stats or MarketStats.from_rate_points([])
    for load in loads:
        scored.append(compute_ai_score(load, stats_obj))
    return scored
