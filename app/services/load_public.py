from __future__ import annotations

from typing import Any, Optional

from app.models.models import Load
from app.services.cargo_status import cargo_loading_date, normalize_cargo_status
from app.services.geo import canonicalize_city_name, is_city_like_name


def is_public_load(load: Load | None) -> bool:
    if load is None:
        return False
    return is_city_like_name(load.from_city) and is_city_like_name(load.to_city)


def build_public_load_base(load: Load, ai_payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    ai_payload = ai_payload or {}
    total_price = load.total_price if load.total_price is not None else load.price
    distance_km = load.distance_km if load.distance_km is not None else ai_payload.get("distance_km")
    rate_per_km = load.rate_per_km
    if (
        rate_per_km is None
        and isinstance(distance_km, (int, float))
        and distance_km > 0
        and isinstance(total_price, (int, float))
    ):
        rate_per_km = round(float(total_price) / float(distance_km), 1)
    if rate_per_km is None:
        rate_per_km = ai_payload.get("rate_per_km")

    loading_date = cargo_loading_date(load)
    canonical_from = canonicalize_city_name(load.from_city)
    canonical_to = canonicalize_city_name(load.to_city)

    return {
        "id": load.id,
        "from_city_id": load.from_city_id,
        "to_city_id": load.to_city_id,
        "from_city": canonical_from,
        "to_city": canonical_to,
        "price": float(total_price) if total_price is not None else 0.0,
        "total_price": float(total_price) if total_price is not None else 0.0,
        "distance": distance_km,
        "distance_km": distance_km,
        "price_per_km": round(float(rate_per_km), 1) if isinstance(rate_per_km, (int, float)) else None,
        "rate_per_km": round(float(rate_per_km), 1) if isinstance(rate_per_km, (int, float)) else None,
        "weight": load.weight,
        "volume": load.volume,
        "truck_type": None,
        "status": normalize_cargo_status(load.status),
        "loading_date": loading_date.isoformat() if loading_date else None,
        "loading_time": load.loading_time,
    }
