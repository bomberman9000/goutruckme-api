from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from app.models.models import Vehicle


@dataclass(frozen=True)
class VehicleProfile:
    profile_name: str
    max_stops: int
    radius_km: float
    max_detour_km: float
    top_k: int
    variants: int
    w_fill: float
    w_detour: float
    w_stops: float
    w_profit: float
    w_trust: float
    w_pickup: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _detect_profile_name(vehicle: Vehicle) -> str:
    capacity = float(vehicle.max_weight_t or vehicle.capacity_tons or 0.0)
    if capacity <= 2.5:
        return "gazelle"
    if capacity <= 7.5:
        return "medium_5t"
    if capacity >= 16:
        return "fura_20t"
    return "medium_10t"


def get_profile(vehicle: Vehicle) -> VehicleProfile:
    profile_name = _detect_profile_name(vehicle)

    if profile_name == "gazelle":
        return VehicleProfile(
            profile_name=profile_name,
            max_stops=5,
            radius_km=120.0,
            max_detour_km=40.0,
            top_k=40,
            variants=10,
            w_fill=1.15,
            w_detour=0.90,
            w_stops=0.35,
            w_profit=0.55,
            w_trust=0.45,
            w_pickup=0.95,
        )

    if profile_name == "medium_5t":
        return VehicleProfile(
            profile_name=profile_name,
            max_stops=4,
            radius_km=200.0,
            max_detour_km=65.0,
            top_k=35,
            variants=10,
            w_fill=1.05,
            w_detour=1.00,
            w_stops=0.45,
            w_profit=0.65,
            w_trust=0.45,
            w_pickup=0.85,
        )

    if profile_name == "fura_20t":
        return VehicleProfile(
            profile_name=profile_name,
            max_stops=3,
            radius_km=300.0,
            max_detour_km=90.0,
            top_k=30,
            variants=8,
            w_fill=1.25,
            w_detour=1.20,
            w_stops=0.85,
            w_profit=0.75,
            w_trust=0.55,
            w_pickup=0.65,
        )

    return VehicleProfile(
        profile_name=profile_name,
        max_stops=4,
        radius_km=220.0,
        max_detour_km=70.0,
        top_k=32,
        variants=9,
        w_fill=1.10,
        w_detour=1.05,
        w_stops=0.55,
        w_profit=0.70,
        w_trust=0.50,
        w_pickup=0.78,
    )


def apply_profile_overrides(profile: VehicleProfile, overrides: dict[str, Any] | None) -> VehicleProfile:
    if not overrides:
        return profile

    data: dict[str, Any] = {}
    int_fields = {"max_stops", "top_k", "variants"}
    float_fields = {
        "radius_km",
        "max_detour_km",
        "w_fill",
        "w_detour",
        "w_stops",
        "w_profit",
        "w_trust",
        "w_pickup",
    }

    for key in int_fields:
        if key not in overrides:
            continue
        value = overrides.get(key)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            data[key] = parsed

    for key in float_fields:
        if key not in overrides:
            continue
        value = overrides.get(key)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            data[key] = parsed

    if not data:
        return profile
    return replace(profile, **data)
