from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json

from sqlalchemy import select

from src.core.geo import city_coords, haversine_km
from src.core.models import Cargo, CargoPaymentStatus, CargoStatus, ParserIngestEvent, UserVehicle
from src.core.services.geo_service import get_geo_service


def _payment_verified(value: CargoPaymentStatus | str | None) -> bool:
    raw = value.value if isinstance(value, CargoPaymentStatus) else (str(value) if value else "")
    return raw in {
        CargoPaymentStatus.FUNDED.value,
        CargoPaymentStatus.DELIVERY_MARKED.value,
        CargoPaymentStatus.RELEASED.value,
    }


def _normalize_body_type(value: str | None) -> str:
    return (value or "").strip().lower()


def _body_matches(vehicle_body: str | None, cargo_body: str | None) -> bool:
    vehicle_key = _normalize_body_type(vehicle_body)
    cargo_key = _normalize_body_type(cargo_body)
    if not vehicle_key or not cargo_key:
        return True
    return vehicle_key in cargo_key or cargo_key in vehicle_key


def _distance_hint(event: ParserIngestEvent) -> int | None:
    payload_raw = getattr(event, "details_json", None)
    if not payload_raw:
        return None
    try:
        payload = json.loads(payload_raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("distance_km")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0:
        return int(round(value))
    return None


def _freshness(created_at: datetime) -> str:
    now = datetime.utcnow()
    current = created_at.replace(tzinfo=None) if getattr(created_at, "tzinfo", None) else created_at
    minutes = int((now - current).total_seconds() / 60)
    if minutes < 60:
        return f"{minutes}м"
    hours = minutes // 60
    return f"{hours}ч" if hours < 24 else f"{hours // 24}д"


@dataclass(slots=True)
class VehicleCargoMatch:
    id: int
    from_city: str | None
    to_city: str | None
    body_type: str | None
    weight_t: float | None
    rate_rub: int | None
    rate_per_km: float | None
    load_date: str | None
    is_hot_deal: bool
    freshness: str | None
    match_score: int
    distance_to_pickup_km: int | None
    match_reasons: list[str]
    verified_payment: bool = False


@dataclass(slots=True)
class CargoVehicleMatch:
    vehicle_id: int
    body_type: str
    capacity_tons: float
    location_city: str | None
    is_available: bool
    plate_number: str | None
    match_score: int
    distance_to_pickup_km: int | None
    match_reasons: list[str]


@dataclass(slots=True)
class MatchSummary:
    vehicle_match_count: int
    cargo_match_count: int
    best_vehicle_match_score: int
    best_cargo_match_score: int


async def score_vehicle_for_event(
    vehicle: UserVehicle,
    event: ParserIngestEvent,
) -> VehicleCargoMatch | None:
    if getattr(event, "status", None) != "synced" or getattr(event, "is_spam", False):
        return None

    if vehicle.capacity_tons and event.weight_t and float(event.weight_t) > float(vehicle.capacity_tons):
        return None

    score = 0
    reasons: list[str] = []

    if _body_matches(vehicle.body_type, event.body_type):
        score += 40
        reasons.append("подходит по кузову")
    else:
        return None

    if vehicle.capacity_tons and event.weight_t:
        score += 25
        reasons.append("тоннаж подходит")
    else:
        score += 10

    distance_to_pickup_km: int | None = None
    vehicle_coords = None
    if vehicle.location_city:
        city = await get_geo_service().get_city_data(vehicle.location_city)
        if city:
            vehicle_coords = (city.lat, city.lon)

    if vehicle_coords and event.from_lat is not None and event.from_lon is not None:
        distance_to_pickup_km = int(round(haversine_km(vehicle_coords[0], vehicle_coords[1], event.from_lat, event.from_lon)))
        if distance_to_pickup_km > 500:
            return None
        if distance_to_pickup_km <= 50:
            score += 20
            reasons.append("машина рядом")
        elif distance_to_pickup_km <= 150:
            score += 12
            reasons.append("нормальный подскок")
        elif distance_to_pickup_km <= 300:
            score += 5
        else:
            score -= 10
    elif vehicle.location_city and event.from_city and vehicle.location_city.strip().lower() in event.from_city.strip().lower():
        score += 12
        reasons.append("тот же город")
    elif vehicle.location_city:
        return None

    if getattr(event, "rate_rub", None):
        score += 2
    if getattr(event, "trust_score", None):
        trust = int(event.trust_score or 0)
        if trust >= 80:
            score += 5
            reasons.append("высокий trust")
        elif trust >= 40:
            score += 2
    if getattr(event, "is_hot_deal", False):
        score += 5
        reasons.append("горячий груз")

    verified_payment = False
    manual_cargo_id = None
    payload_raw = getattr(event, "details_json", None)
    if payload_raw:
        try:
            payload = json.loads(payload_raw)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            cargo_id = payload.get("cargo_id")
            if isinstance(cargo_id, int):
                manual_cargo_id = cargo_id
    if manual_cargo_id is not None:
        # handled by caller in list fetch if it preloads cargo, but allow passive scoring
        pass

    score = max(0, min(100, score))
    distance_hint = _distance_hint(event)
    rate_per_km = None
    if event.rate_rub and distance_hint and distance_hint >= 10:
        rate_per_km = round(float(event.rate_rub) / float(distance_hint), 1)

    return VehicleCargoMatch(
        id=int(event.id),
        from_city=event.from_city,
        to_city=event.to_city,
        body_type=event.body_type,
        weight_t=float(event.weight_t) if event.weight_t is not None else None,
        rate_rub=int(event.rate_rub) if event.rate_rub is not None else None,
        rate_per_km=rate_per_km,
        load_date=event.load_date,
        is_hot_deal=bool(event.is_hot_deal),
        freshness=_freshness(event.created_at),
        match_score=score,
        distance_to_pickup_km=distance_to_pickup_km,
        match_reasons=reasons[:3],
        verified_payment=verified_payment,
    )


async def find_matches_for_vehicle(session, vehicle: UserVehicle, *, limit: int = 10) -> list[VehicleCargoMatch]:
    city = (vehicle.location_city or "").strip()
    if not city:
        return []

    stmt = (
        select(ParserIngestEvent)
        .where(
            ParserIngestEvent.is_spam.is_(False),
            ParserIngestEvent.status == "synced",
        )
        .order_by(ParserIngestEvent.id.desc())
        .limit(80)
    )
    rows = (await session.execute(stmt)).scalars().all()
    manual_cargo_ids: list[int] = []
    for row in rows:
        payload_raw = getattr(row, "details_json", None)
        if not payload_raw:
            continue
        try:
            payload = json.loads(payload_raw)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        cargo_id = payload.get("cargo_id")
        if isinstance(cargo_id, int):
            manual_cargo_ids.append(cargo_id)
    manual_status: dict[int, bool] = {}
    if manual_cargo_ids:
        cargo_rows = (
            await session.execute(select(Cargo).where(Cargo.id.in_(sorted(set(manual_cargo_ids)))))
        ).scalars().all()
        manual_status = {
            int(cargo.id): _payment_verified(getattr(cargo, "payment_status", None))
            for cargo in cargo_rows
        }

    results: list[VehicleCargoMatch] = []
    for row in rows:
        match = await score_vehicle_for_event(vehicle, row)
        if not match:
            continue
        payload_raw = getattr(row, "details_json", None)
        if payload_raw:
            try:
                payload = json.loads(payload_raw)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                cargo_id = payload.get("cargo_id")
                if isinstance(cargo_id, int) and manual_status.get(cargo_id):
                    match.verified_payment = True
                    match.match_score = min(100, match.match_score + 10)
                    if "оплата гарантирована" not in match.match_reasons:
                        match.match_reasons.append("оплата гарантирована")
        if match.match_score < 35:
            continue
        results.append(match)
    results.sort(key=lambda item: (item.match_score, item.verified_payment, item.rate_rub or 0), reverse=True)
    return results[:limit]


async def score_cargo_for_vehicle(
    cargo: Cargo,
    vehicle: UserVehicle,
) -> CargoVehicleMatch | None:
    if not bool(getattr(vehicle, "is_available", False)):
        return None
    if vehicle.capacity_tons and cargo.weight and float(cargo.weight) > float(vehicle.capacity_tons):
        return None
    if not _body_matches(vehicle.body_type, cargo.cargo_type):
        return None

    score = 0
    reasons: list[str] = []
    score += 40
    reasons.append("подходит по кузову")
    score += 25
    reasons.append("тоннаж подходит")

    distance_to_pickup_km: int | None = None
    if vehicle.location_city and cargo.from_city:
        vehicle_geo = await get_geo_service().get_city_data(vehicle.location_city)
        cargo_geo = await get_geo_service().get_city_data(cargo.from_city)
        if vehicle_geo and cargo_geo:
            distance_to_pickup_km = int(round(haversine_km(vehicle_geo.lat, vehicle_geo.lon, cargo_geo.lat, cargo_geo.lon)))
            if distance_to_pickup_km > 500:
                return None
            if distance_to_pickup_km <= 50:
                score += 20
                reasons.append("машина рядом")
            elif distance_to_pickup_km <= 150:
                score += 12
                reasons.append("нормальный подскок")
            elif distance_to_pickup_km <= 300:
                score += 5
            else:
                score -= 10
        elif vehicle.location_city.strip().lower() != cargo.from_city.strip().lower():
            return None

    if _payment_verified(getattr(cargo, "payment_status", None)):
        score += 10
        reasons.append("оплата гарантирована")

    score = max(0, min(100, score))
    if score < 35:
        return None

    return CargoVehicleMatch(
        vehicle_id=int(vehicle.id),
        body_type=vehicle.body_type,
        capacity_tons=float(vehicle.capacity_tons),
        location_city=vehicle.location_city,
        is_available=bool(vehicle.is_available),
        plate_number=vehicle.plate_number,
        match_score=score,
        distance_to_pickup_km=distance_to_pickup_km,
        match_reasons=reasons[:3],
    )


async def find_matches_for_cargo(session, cargo: Cargo, *, limit: int = 10) -> list[CargoVehicleMatch]:
    rows = (
        await session.execute(
            select(UserVehicle)
            .where(UserVehicle.is_available.is_(True))
            .order_by(UserVehicle.id.desc())
            .limit(80)
        )
    ).scalars().all()
    results: list[CargoVehicleMatch] = []
    for vehicle in rows:
        match = await score_cargo_for_vehicle(cargo, vehicle)
        if not match:
            continue
        results.append(match)
    results.sort(key=lambda item: item.match_score, reverse=True)
    return results[:limit]


async def build_match_summary(session, user_id: int) -> MatchSummary:
    vehicles = (
        await session.execute(
            select(UserVehicle)
            .where(UserVehicle.user_id == user_id)
            .where(UserVehicle.is_available.is_(True))
            .order_by(UserVehicle.id.desc())
            .limit(10)
        )
    ).scalars().all()
    cargos = (
        await session.execute(
            select(Cargo)
            .where(Cargo.owner_id == user_id)
            .where(Cargo.status != CargoStatus.ARCHIVED)
            .order_by(Cargo.id.desc())
            .limit(10)
        )
    ).scalars().all()

    vehicle_match_count = 0
    cargo_match_count = 0
    best_vehicle_score = 0
    best_cargo_score = 0

    for vehicle in vehicles:
        matches = await find_matches_for_vehicle(session, vehicle, limit=3)
        if matches:
            vehicle_match_count += len(matches)
            best_vehicle_score = max(best_vehicle_score, matches[0].match_score)

    for cargo in cargos:
        matches = await find_matches_for_cargo(session, cargo, limit=3)
        if matches:
            cargo_match_count += len(matches)
            best_cargo_score = max(best_cargo_score, matches[0].match_score)

    return MatchSummary(
        vehicle_match_count=vehicle_match_count,
        cargo_match_count=cargo_match_count,
        best_vehicle_match_score=best_vehicle_score,
        best_cargo_match_score=best_cargo_score,
    )
