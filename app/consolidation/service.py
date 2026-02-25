from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, radians, sin, sqrt
from typing import Any

from sqlalchemy.orm import Session

from app.ai.scoring import MarketStats, compute_ai_score
from app.consolidation.profiles import VehicleProfile, apply_profile_overrides, get_profile
from app.matching.compat import check_compat
from app.models.models import (
    ConsolidationPlan,
    ConsolidationPlanItem,
    Load,
    Vehicle,
)
from app.services.cargo_status import apply_cargo_status_filter
from app.trust.service import get_company_trust_snapshot


_BODY_TYPE_MAP = {
    "тент": "тент",
    "tent": "тент",
    "реф": "реф",
    "рефрижератор": "реф",
    "ref": "реф",
    "площадка": "площадка",
    "platform": "площадка",
    "коники": "коники",
}

_CITY_COORDS: dict[str, tuple[float, float]] = {
    "москва": (55.7558, 37.6176),
    "санкт-петербург": (59.9311, 30.3609),
    "казань": (55.7961, 49.1064),
    "нижний новгород": (56.2965, 43.9361),
    "екатеринбург": (56.8389, 60.6057),
    "новосибирск": (55.0084, 82.9357),
    "краснодар": (45.0355, 38.9753),
    "ростов-на-дону": (47.2357, 39.7015),
    "самара": (53.2415, 50.2212),
    "уфа": (54.7388, 55.9721),
    "воронеж": (51.6615, 39.2003),
    "пермь": (58.0105, 56.2502),
    "челябинск": (55.1644, 61.4368),
    "омск": (54.9885, 73.3242),
    "тюмень": (57.1530, 65.5343),
    "тольятти": (53.5086, 49.4128),
}


@dataclass
class CandidateCargo:
    load: Load
    load_id: int
    weight_t: float
    volume_m3: float
    pickup_distance_km: float
    pickup_point: tuple[float, float] | None
    delivery_point: tuple[float, float] | None
    base_score: float
    rate_per_km: float
    proximity_score: float
    fill_score: float
    profit_score: float
    trust_bonus: float
    risk_penalty: float
    ai_risk: str
    ai_score: int
    trust_score: int
    reasons: list[str]


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _canonical_body_type(value: Any) -> str:
    normalized = _norm(value)
    return _BODY_TYPE_MAP.get(normalized, normalized)


def _city_point(city: str | None) -> tuple[float, float] | None:
    normalized = _norm(city)
    if not normalized:
        return None
    return _CITY_COORDS.get(normalized)


def _haversine_km(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    lat1, lon1 = point_a
    lat2, lon2 = point_b
    radius = 6371.0

    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)

    h = sin(d_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(d_lon / 2) ** 2
    return 2 * radius * atan2(sqrt(h), sqrt(1 - h))


def _point_distance_km(
    point_a: tuple[float, float] | None,
    point_b: tuple[float, float] | None,
    city_a: str | None = None,
    city_b: str | None = None,
) -> float:
    if point_a and point_b:
        return _haversine_km(point_a, point_b)
    if _norm(city_a) and _norm(city_a) == _norm(city_b):
        return 0.0
    # Fallback для грузов без координат: нейтральная дистанция, чтобы не было краша/NaN.
    return 180.0


def _vehicle_limits(vehicle: Vehicle) -> tuple[float, float]:
    max_weight_t = float(vehicle.max_weight_t or vehicle.capacity_tons or 0.0)
    max_volume_m3 = float(vehicle.max_volume_m3 or vehicle.volume_m3 or 0.0)
    return max(max_weight_t, 0.0), max(max_volume_m3, 0.0)


def _load_weight_volume(load: Load) -> tuple[float, float]:
    weight_t = float(load.weight_t if load.weight_t is not None else (load.weight or 0.0))
    volume_m3 = float(load.volume_m3 if load.volume_m3 is not None else (load.volume or 0.0))
    return max(weight_t, 0.0), max(volume_m3, 0.0)


def _vehicle_start_point(vehicle: Vehicle) -> tuple[float, float] | None:
    if vehicle.start_lat is not None and vehicle.start_lon is not None:
        return float(vehicle.start_lat), float(vehicle.start_lon)
    return _city_point(vehicle.location_city)


def _pickup_point(load: Load) -> tuple[float, float] | None:
    if load.pickup_lat is not None and load.pickup_lon is not None:
        return float(load.pickup_lat), float(load.pickup_lon)
    return _city_point(load.from_city)


def _delivery_point(load: Load) -> tuple[float, float] | None:
    if load.delivery_lat is not None and load.delivery_lon is not None:
        return float(load.delivery_lat), float(load.delivery_lon)
    return _city_point(load.to_city)


def cargo_compatible(vehicle: Vehicle, cargo: Load) -> tuple[bool, list[str], list[str]]:
    compat = check_compat(vehicle, cargo)
    if bool(compat.get("ok")):
        reasons = list(compat.get("reasons") or [])
        return True, reasons, []
    blockers = list(compat.get("blockers") or ["Несовместимо по параметрам"])
    return False, [], blockers


def _risk_penalty_points(level: str) -> float:
    normalized = _norm(level)
    if normalized == "high":
        return 25.0
    if normalized == "medium":
        return 10.0
    return 0.0


def _calc_rate_per_km(load: Load) -> float:
    if load.rate_per_km and float(load.rate_per_km) > 0:
        return float(load.rate_per_km)
    price = float(load.total_price if load.total_price is not None else (load.price or 0.0))
    distance = float(load.distance_km or 0.0)
    if price > 0 and distance > 0:
        return round(price / distance, 1)
    return 0.0


def _route_cities(vehicle: Vehicle, selected: list[CandidateCargo]) -> list[str]:
    route: list[str] = []

    def _push(city: str | None) -> None:
        name = str(city or "").strip()
        if not name:
            return
        if route and route[-1] == name:
            return
        route.append(name)

    _push(vehicle.location_city)
    for item in selected:
        _push(item.load.from_city)
        _push(item.load.to_city)
    return route


def _prepare_candidates(
    db: Session,
    vehicle: Vehicle,
    profile: VehicleProfile,
) -> list[CandidateCargo]:
    start_point = _vehicle_start_point(vehicle)
    max_weight_t, max_volume_m3 = _vehicle_limits(vehicle)
    stats = MarketStats.from_db(db, lookback_days=60)
    trust_cache: dict[int, int] = {}

    query = apply_cargo_status_filter(db.query(Load), "active").order_by(Load.created_at.desc()).limit(500)
    candidates: list[CandidateCargo] = []

    for load in query.all():
        weight_t, volume_m3 = _load_weight_volume(load)
        if max_weight_t > 0 and weight_t > max_weight_t:
            continue
        if max_volume_m3 > 0 and volume_m3 > max_volume_m3:
            continue

        compatible, compat_reasons, _ = cargo_compatible(vehicle, load)
        if not compatible:
            continue

        pickup = _pickup_point(load)
        delivery = _delivery_point(load)
        pickup_distance = _point_distance_km(start_point, pickup, vehicle.location_city, load.from_city)
        if pickup_distance > profile.radius_km:
            continue

        ai_payload = compute_ai_score(load, stats)
        ai_risk = _norm(ai_payload.get("ai_risk")) or "low"
        ai_score = int(ai_payload.get("ai_score") or 0)

        owner_id = int(load.user_id or 0)
        if owner_id and owner_id not in trust_cache:
            snapshot = get_company_trust_snapshot(db, owner_id)
            trust_cache[owner_id] = int(snapshot.get("trust_score") or 50)
        trust_score = trust_cache.get(owner_id, 50)

        fill_ratio = max(
            (weight_t / max_weight_t) if max_weight_t > 0 else 0.0,
            (volume_m3 / max_volume_m3) if max_volume_m3 > 0 else 0.0,
        )
        rate_per_km = _calc_rate_per_km(load)
        proximity_score = max(0.0, 100.0 - pickup_distance)
        fill_score = max(0.0, min(30.0, fill_ratio * 30.0))
        profit_score = max(0.0, min(30.0, rate_per_km * 0.2))
        trust_bonus = max(0.0, min(20.0, trust_score / 5.0))
        risk_penalty = _risk_penalty_points(ai_risk)
        base_score = max(0.0, min(100.0, proximity_score + fill_score + profit_score + trust_bonus - risk_penalty))

        reasons = list(compat_reasons[:4])
        reasons.extend(
            [
                f"Близко к базе: {pickup_distance:.1f} км",
                f"Заполнение: {fill_ratio * 100:.0f}%",
                f"Ставка: {rate_per_km:.1f} ₽/км",
                f"Trust {trust_score}/100",
            ]
        )
        if ai_risk == "low":
            reasons.append("Низкий AI-риск")
        elif ai_risk == "medium":
            reasons.append("Умеренный AI-риск")
        else:
            reasons.append("Высокий AI-риск")

        candidates.append(
            CandidateCargo(
                load=load,
                load_id=int(load.id),
                weight_t=weight_t,
                volume_m3=volume_m3,
                pickup_distance_km=pickup_distance,
                pickup_point=pickup,
                delivery_point=delivery,
                base_score=base_score,
                rate_per_km=rate_per_km,
                proximity_score=proximity_score,
                fill_score=fill_score,
                profit_score=profit_score,
                trust_bonus=trust_bonus,
                risk_penalty=risk_penalty,
                ai_risk=ai_risk,
                ai_score=ai_score,
                trust_score=trust_score,
                reasons=reasons,
            )
        )

    # MVP: сначала близость к базе, затем общий score.
    candidates.sort(key=lambda item: (item.pickup_distance_km, -item.base_score, -item.rate_per_km, item.load_id))
    return candidates[: max(profile.top_k, 1)]


def _build_plan_from_seed(
    seed: CandidateCargo,
    candidates: list[CandidateCargo],
    vehicle: Vehicle,
    profile: VehicleProfile,
) -> dict[str, Any] | None:
    max_weight_t, max_volume_m3 = _vehicle_limits(vehicle)
    if max_weight_t <= 0 and max_volume_m3 <= 0:
        return None

    selected: list[CandidateCargo] = [seed]
    used_ids = {seed.load_id}
    total_weight = seed.weight_t
    total_volume = seed.volume_m3
    detour_km = seed.pickup_distance_km
    current_point = seed.delivery_point or seed.pickup_point
    current_city = seed.load.to_city or seed.load.from_city

    if detour_km > profile.max_detour_km:
        return None

    while len(selected) < profile.max_stops:
        best_next: CandidateCargo | None = None
        best_extra_km = float("inf")

        for candidate in candidates:
            if candidate.load_id in used_ids:
                continue

            next_weight = total_weight + candidate.weight_t
            next_volume = total_volume + candidate.volume_m3
            if max_weight_t > 0 and next_weight > max_weight_t:
                continue
            if max_volume_m3 > 0 and next_volume > max_volume_m3:
                continue

            extra_km = _point_distance_km(current_point, candidate.pickup_point, current_city, candidate.load.from_city)
            if detour_km + extra_km > profile.max_detour_km:
                continue

            if (
                best_next is None
                or extra_km < best_extra_km
                or (abs(extra_km - best_extra_km) < 1e-9 and candidate.base_score > best_next.base_score)
                or (
                    abs(extra_km - best_extra_km) < 1e-9
                    and abs(candidate.base_score - best_next.base_score) < 1e-9
                    and candidate.pickup_distance_km < best_next.pickup_distance_km
                )
            ):
                best_next = candidate
                best_extra_km = extra_km

        if best_next is None:
            break

        selected.append(best_next)
        used_ids.add(best_next.load_id)
        total_weight += best_next.weight_t
        total_volume += best_next.volume_m3
        detour_km += best_extra_km
        current_point = best_next.delivery_point or best_next.pickup_point or current_point
        current_city = best_next.load.to_city or best_next.load.from_city

    if len(selected) == 0:
        return None

    fill_ratio = max(
        (total_weight / max_weight_t) if max_weight_t > 0 else 0.0,
        (total_volume / max_volume_m3) if max_volume_m3 > 0 else 0.0,
    )
    fill_score = max(0.0, min(30.0, fill_ratio * 30.0))
    avg_rate_per_km = sum(item.rate_per_km for item in selected) / len(selected)
    profit_score = max(0.0, min(30.0, avg_rate_per_km * 0.2))
    avg_trust = sum(item.trust_score for item in selected) / len(selected)
    trust_bonus = max(0.0, min(20.0, avg_trust / 5.0))
    avg_risk_penalty = sum(item.risk_penalty for item in selected) / len(selected)
    proximity_score = max(0.0, 100.0 - (detour_km / max(len(selected), 1)))
    score = round(
        max(
            0.0,
            min(100.0, proximity_score + fill_score + profit_score + trust_bonus - avg_risk_penalty),
        ),
        1,
    )
    avg_pickup_km = sum(item.pickup_distance_km for item in selected) / len(selected)
    profit_estimate = round(
        sum(float(item.load.total_price if item.load.total_price is not None else (item.load.price or 0.0)) for item in selected),
        2,
    )

    explain = [
        (
            f"Помещается: {total_weight:.2f} т / {max_weight_t:.2f} т, "
            f"{total_volume:.2f} м³ / {max_volume_m3:.2f} м³"
        ),
        f"{len(selected)} точки в сборке, средняя подача {avg_pickup_km:.1f} км",
        f"Минимальный крюк: +{detour_km:.1f} км",
        (
            "Скоринг: "
            f"близость {proximity_score:.1f} + заполнение {fill_score:.1f} + "
            f"выгода {profit_score:.1f} + trust {trust_bonus:.1f} - риск {avg_risk_penalty:.1f}"
        ),
    ]
    if avg_trust >= 70:
        explain.append(f"Надёжные контрагенты: средний trust {avg_trust:.0f}/100")
    elif avg_trust >= 50:
        explain.append(f"Сбалансированный trust: {avg_trust:.0f}/100")
    else:
        explain.append(f"Требуется проверка контрагентов: trust {avg_trust:.0f}/100")

    route_points: list[dict[str, Any]] = [
        {
            "type": "start",
            "city": vehicle.location_city,
            "lat": vehicle.start_lat,
            "lon": vehicle.start_lon,
        }
    ]
    for idx, candidate in enumerate(selected, start=1):
        route_points.append(
            {
                "type": "pickup",
                "seq": idx,
                "load_id": candidate.load_id,
                "city": candidate.load.from_city,
                "lat": candidate.load.pickup_lat,
                "lon": candidate.load.pickup_lon,
            }
        )
        route_points.append(
            {
                "type": "delivery",
                "seq": idx,
                "load_id": candidate.load_id,
                "city": candidate.load.to_city,
                "lat": candidate.load.delivery_lat,
                "lon": candidate.load.delivery_lon,
            }
        )

    route_cities = _route_cities(vehicle, selected)
    why_selected: list[str] = []
    if proximity_score >= 60:
        why_selected.append("Ближайший к базе")
    if fill_score >= 20:
        why_selected.append("Хорошо заполняет объём и вес")
    if detour_km <= (profile.max_detour_km * 0.4):
        why_selected.append("Минимальный крюк")
    if avg_trust >= 60:
        why_selected.append("Надёжный пул контрагентов")
    why_selected.append("Совместим по типу и ограничениям")

    return {
        "items": selected,
        "load_ids": [item.load_id for item in selected],
        "total_weight": round(total_weight, 3),
        "total_volume": round(total_volume, 3),
        "detour_km": round(detour_km, 2),
        "extra_km": round(detour_km, 2),
        "profit_estimate": profit_estimate,
        "score": score,
        "stops_count": len(selected),
        "route": route_points,
        "route_cities": route_cities,
        "why_selected": why_selected,
        "score_components": {
            "proximity_score": round(proximity_score, 1),
            "fill_score": round(fill_score, 1),
            "profit_score": round(profit_score, 1),
            "trust_bonus": round(trust_bonus, 1),
            "risk_penalty": round(avg_risk_penalty, 1),
        },
        "explain": explain,
    }


def _serialize_plan_payload(
    plan_id: int,
    status: str,
    vehicle: Vehicle,
    profile: VehicleProfile,
    plan_payload: dict[str, Any],
) -> dict[str, Any]:
    max_weight_t, max_volume_m3 = _vehicle_limits(vehicle)
    loads_payload = []

    for idx, item in enumerate(plan_payload["items"], start=1):
        loads_payload.append(
            {
                "seq": idx,
                "id": item.load_id,
                "from_city": item.load.from_city,
                "to_city": item.load.to_city,
                "weight_t": round(item.weight_t, 3),
                "volume_m3": round(item.volume_m3, 3),
                "price": float(item.load.price or 0.0),
                "rate_per_km": float(item.rate_per_km or 0.0),
                "pickup_distance_km": round(item.pickup_distance_km, 2),
                "ai_risk": item.ai_risk,
                "ai_score": item.ai_score,
                "trust_score": item.trust_score,
                "required_body_type": item.load.required_body_type,
                "reasons": item.reasons,
            }
        )

    return {
        "id": plan_id,
        "plan_id": plan_id,
        "status": status,
        "vehicle_id": vehicle.id,
        "vehicle_profile": profile.to_dict(),
        "capacity": {
            "max_weight_t": round(max_weight_t, 3),
            "max_volume_m3": round(max_volume_m3, 3),
        },
        "load_ids": plan_payload["load_ids"],
        "loads": loads_payload,
        "total_weight": plan_payload["total_weight"],
        "total_volume": plan_payload["total_volume"],
        "detour_km": plan_payload["detour_km"],
        "extra_km": plan_payload["extra_km"],
        "profit_estimate": plan_payload["profit_estimate"],
        "stops_count": plan_payload["stops_count"],
        "score": plan_payload["score"],
        "score_components": plan_payload["score_components"],
        "route": plan_payload["route_cities"],
        "route_cities": plan_payload["route_cities"],
        "route_points": plan_payload["route"],
        "why_selected": plan_payload["why_selected"],
        "explain": plan_payload["explain"],
    }


def build_plans(
    db: Session,
    vehicle_id: int,
    *,
    max_stops: int | None = None,
    radius_km: float | None = None,
    top_k: int | None = None,
    variants: int | None = None,
    max_detour_km: float | None = None,
    profile_overrides: dict[str, Any] | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if not vehicle:
        raise ValueError("Машина не найдена")

    profile = get_profile(vehicle)
    profile = apply_profile_overrides(profile, profile_overrides)
    runtime_overrides: dict[str, Any] = {}
    if max_stops is not None:
        runtime_overrides["max_stops"] = max_stops
    if radius_km is not None:
        runtime_overrides["radius_km"] = radius_km
    if top_k is not None:
        runtime_overrides["top_k"] = top_k
    if variants is not None:
        runtime_overrides["variants"] = variants
    if max_detour_km is not None:
        runtime_overrides["max_detour_km"] = max_detour_km
    profile = apply_profile_overrides(profile, runtime_overrides)

    if created_by:
        old_plans = (
            db.query(ConsolidationPlan)
            .filter(
                ConsolidationPlan.vehicle_id == vehicle.id,
                ConsolidationPlan.created_by == int(created_by),
                ConsolidationPlan.status == "draft",
            )
            .all()
        )
        for old in old_plans:
            db.delete(old)
        db.flush()

    candidates = _prepare_candidates(db, vehicle, profile)
    if not candidates:
        if created_by:
            db.commit()
        return {
            "vehicle_id": vehicle.id,
            "vehicle_profile": profile.to_dict(),
            "plans": [],
            "meta": {"candidates_total": 0},
        }

    seeds_count = min(len(candidates), max(profile.variants * 2, 5))
    signatures: set[tuple[int, ...]] = set()
    generated: list[dict[str, Any]] = []

    for seed in candidates[:seeds_count]:
        plan_payload = _build_plan_from_seed(seed, candidates, vehicle, profile)
        if not plan_payload:
            continue
        signature = tuple(plan_payload["load_ids"])
        if signature in signatures:
            continue
        signatures.add(signature)
        generated.append(plan_payload)

    generated.sort(key=lambda item: (item["score"], -item["detour_km"]), reverse=True)
    generated = generated[: max(profile.variants, 1)]

    serialized_plans: list[dict[str, Any]] = []
    for payload in generated:
        plan_row = ConsolidationPlan(
            vehicle_id=vehicle.id,
            status="draft",
            created_by=int(created_by or vehicle.carrier_id),
            total_weight=payload["total_weight"],
            total_volume=payload["total_volume"],
            score=payload["score"],
            detour_km=payload["detour_km"],
            explain_json={
                "explain": payload["explain"],
                "why_selected": payload["why_selected"],
                "route_cities": payload["route_cities"],
                "route_points": payload["route"],
                "profit_estimate": payload["profit_estimate"],
                "score_components": payload["score_components"],
                "profile": profile.to_dict(),
            },
        )
        db.add(plan_row)
        db.flush()

        for seq, item in enumerate(payload["items"], start=1):
            db.add(
                ConsolidationPlanItem(
                    plan_id=plan_row.id,
                    cargo_id=item.load_id,
                    seq=seq,
                )
            )

        serialized_plans.append(_serialize_plan_payload(plan_row.id, plan_row.status, vehicle, profile, payload))

    db.commit()

    return {
        "vehicle_id": vehicle.id,
        "vehicle_profile": profile.to_dict(),
        "plans": serialized_plans,
        "meta": {
            "candidates_total": len(candidates),
            "variants_returned": len(serialized_plans),
        },
    }


def serialize_plan(db: Session, plan: ConsolidationPlan) -> dict[str, Any]:
    vehicle = db.query(Vehicle).filter(Vehicle.id == plan.vehicle_id).first()
    if not vehicle:
        raise ValueError("Машина плана не найдена")

    profile_data = {}
    explain = []
    why_selected: list[str] = []
    route_cities: list[str] = []
    route_points: list[dict[str, Any]] = []
    score_components: dict[str, float] = {}
    profit_estimate = 0.0
    if isinstance(plan.explain_json, dict):
        profile_data = plan.explain_json.get("profile") or {}
        explain = plan.explain_json.get("explain") or []
        why_selected = plan.explain_json.get("why_selected") or []
        route_cities = plan.explain_json.get("route_cities") or []
        route_points = plan.explain_json.get("route_points") or []
        score_components = plan.explain_json.get("score_components") or {}
        try:
            profit_estimate = float(plan.explain_json.get("profit_estimate") or 0.0)
        except (TypeError, ValueError):
            profit_estimate = 0.0

    profile = apply_profile_overrides(get_profile(vehicle), profile_data if isinstance(profile_data, dict) else None)
    items = (
        db.query(ConsolidationPlanItem, Load)
        .join(Load, Load.id == ConsolidationPlanItem.cargo_id)
        .filter(ConsolidationPlanItem.plan_id == plan.id)
        .order_by(ConsolidationPlanItem.seq.asc())
        .all()
    )
    load_payload = []
    load_ids = []
    for row, load in items:
        weight_t, volume_m3 = _load_weight_volume(load)
        load_payload.append(
            {
                "seq": int(row.seq),
                "id": int(load.id),
                "from_city": load.from_city,
                "to_city": load.to_city,
                "weight_t": round(weight_t, 3),
                "volume_m3": round(volume_m3, 3),
                "price": float(load.price or 0.0),
                "rate_per_km": float(_calc_rate_per_km(load)),
                "required_body_type": load.required_body_type,
            }
        )
        load_ids.append(int(load.id))

    max_weight_t, max_volume_m3 = _vehicle_limits(vehicle)
    if profit_estimate <= 0:
        profit_estimate = round(sum(float(item.get("price") or 0.0) for item in load_payload), 2)
    if not route_cities:
        route_cities = [str(vehicle.location_city or "").strip()] + [str(item.get("to_city") or "").strip() for item in load_payload]
        route_cities = [item for item in route_cities if item]

    return {
        "id": plan.id,
        "plan_id": plan.id,
        "status": plan.status,
        "vehicle_id": plan.vehicle_id,
        "vehicle_profile": profile.to_dict(),
        "capacity": {
            "max_weight_t": round(max_weight_t, 3),
            "max_volume_m3": round(max_volume_m3, 3),
        },
        "load_ids": load_ids,
        "loads": load_payload,
        "total_weight": float(plan.total_weight or 0.0),
        "total_volume": float(plan.total_volume or 0.0),
        "detour_km": float(plan.detour_km or 0.0),
        "extra_km": float(plan.detour_km or 0.0),
        "profit_estimate": profit_estimate,
        "score": float(plan.score or 0.0),
        "score_components": score_components if isinstance(score_components, dict) else {},
        "stops_count": len(load_payload),
        "route": route_cities,
        "route_cities": route_cities,
        "route_points": route_points if isinstance(route_points, list) else [],
        "why_selected": why_selected if isinstance(why_selected, list) else [],
        "explain": explain if isinstance(explain, list) else [],
    }
