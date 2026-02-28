from __future__ import annotations

import json
import uuid
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.audit import log_audit_event
from src.core.cache import clear_cached
from src.core.database import async_session
from src.core.models import Cargo, CargoPaymentStatus, CargoStatus, EscrowDeal, EscrowStatus, ParserIngestEvent, User
from src.core.services.geo_service import get_geo_service
from src.core.services.notification_dispatcher import notify_matching_carriers

router = APIRouter(tags=["cargos"])


class ManualCargoCreate(BaseModel):
    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    body_type: str = Field(min_length=2, max_length=100)
    weight: float = Field(gt=0, le=1000)
    price: int = Field(gt=0, le=1_000_000_000)
    load_date: date
    load_time: str | None = Field(default=None, max_length=10)
    description: str | None = Field(default=None, max_length=1000)
    payment_terms: str | None = Field(default=None, max_length=120)


class ManualCargoResponse(BaseModel):
    ok: bool = True
    cargo_id: int
    feed_id: int


class MyCargoItem(BaseModel):
    id: int
    from_city: str
    to_city: str
    body_type: str
    weight: float
    price: int
    load_date: str
    load_time: str | None
    description: str | None
    payment_terms: str | None
    status: str
    feed_id: int | None
    feed_status: str | None
    is_published: bool
    payment_status: str = CargoPaymentStatus.UNSECURED.value
    verified_payment: bool = False
    escrow_amount_rub: int | None = None
    escrow_status: str | None = None
    created_at: datetime


class MyCargoResponse(BaseModel):
    items: list[MyCargoItem] = Field(default_factory=list)
    limit: int


class ManualCargoUpdate(BaseModel):
    origin: str | None = Field(default=None, min_length=2, max_length=100)
    destination: str | None = Field(default=None, min_length=2, max_length=100)
    body_type: str | None = Field(default=None, min_length=2, max_length=100)
    weight: float | None = Field(default=None, gt=0, le=1000)
    price: int | None = Field(default=None, gt=0, le=1_000_000_000)
    load_date: date | None = None
    load_time: str | None = Field(default=None, max_length=10)
    description: str | None = Field(default=None, max_length=1000)
    payment_terms: str | None = Field(default=None, max_length=120)


class CargoMutationResponse(BaseModel):
    ok: bool = True
    cargo_id: int
    feed_id: int | None = None
    status: str


def _normalize_text(value: str | None) -> str | None:
    clean = (value or "").strip()
    return clean or None


def _ensure_user_full_name(raw_user: dict, user_id: int) -> tuple[str | None, str]:
    username = raw_user.get("username")
    first_name = (raw_user.get("first_name") or "").strip()
    last_name = (raw_user.get("last_name") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    if not full_name:
        full_name = (username or "").strip() or f"User {user_id}"
    return (username or None), full_name


def _trust_verdict(score: int | None) -> str:
    numeric = int(score or 0)
    if numeric >= 80:
        return "green"
    if numeric >= 40:
        return "yellow"
    return "red"


def _details_match_cargo_id(details_json: str | None, cargo_id: int) -> bool:
    return bool(details_json and f"\"cargo_id\": {cargo_id}" in details_json)


def _load_details_payload(details_json: str | None) -> dict:
    if not details_json:
        return {}
    try:
        payload = json.loads(details_json)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_manual_raw_text(
    origin: str,
    destination: str,
    body_type: str,
    weight: float,
    price: int,
    description: str | None,
) -> str:
    return "\n".join(
        part
        for part in (
            f"{origin} - {destination}",
            body_type,
            f"{weight}т",
            f"{price}₽",
            description or "",
        )
        if part
    )


async def _find_manual_feed_event(session, owner_id: int, cargo_id: int) -> ParserIngestEvent | None:
    rows = (
        await session.execute(
            select(ParserIngestEvent)
            .where(
                ParserIngestEvent.source == "manual_client",
                ParserIngestEvent.chat_id == f"user:{owner_id}",
            )
            .order_by(ParserIngestEvent.id.desc())
            .limit(200)
        )
    ).scalars().all()
    for row in rows:
        if _details_match_cargo_id(getattr(row, "details_json", None), cargo_id):
            return row
    return None


def _payment_status_value(value: CargoPaymentStatus | str | None) -> str:
    if isinstance(value, CargoPaymentStatus):
        return value.value
    if value:
        return str(value)
    return CargoPaymentStatus.UNSECURED.value


def _payment_verified(value: CargoPaymentStatus | str | None) -> bool:
    return _payment_status_value(value) in {
        CargoPaymentStatus.FUNDED.value,
        CargoPaymentStatus.DELIVERY_MARKED.value,
        CargoPaymentStatus.RELEASED.value,
    }


def _serialize_my_cargo(cargo: Cargo, event: ParserIngestEvent | None, escrow: EscrowDeal | None) -> MyCargoItem:
    payment_status = _payment_status_value(getattr(cargo, "payment_status", None))
    return MyCargoItem(
        id=cargo.id,
        from_city=cargo.from_city,
        to_city=cargo.to_city,
        body_type=cargo.cargo_type,
        weight=float(cargo.weight),
        price=int(cargo.price),
        load_date=cargo.load_date.date().isoformat(),
        load_time=cargo.load_time,
        description=cargo.comment,
        payment_terms=event.payment_terms if event else None,
        status=cargo.status.value if isinstance(cargo.status, CargoStatus) else str(cargo.status),
        feed_id=event.id if event else None,
        feed_status=event.status if event else None,
        is_published=bool(event and event.status == "synced" and not event.is_spam),
        payment_status=payment_status,
        verified_payment=_payment_verified(payment_status),
        escrow_amount_rub=int(escrow.amount_rub) if escrow else None,
        escrow_status=escrow.status.value if escrow and isinstance(escrow.status, EscrowStatus) else (str(escrow.status) if escrow else None),
        created_at=cargo.created_at or datetime.utcnow(),
    )


@router.post("/api/v1/cargos/manual", response_model=ManualCargoResponse)
async def create_manual_cargo(
    body: ManualCargoCreate,
    background_tasks: BackgroundTasks,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> ManualCargoResponse:
    origin = body.origin.strip()
    destination = body.destination.strip()
    body_type = body.body_type.strip()
    description = _normalize_text(body.description)
    payment_terms = _normalize_text(body.payment_terms)
    username, full_name = _ensure_user_full_name(tma_user.raw, tma_user.user_id)
    route_geo = await get_geo_service().resolve_route(origin, destination)
    if not route_geo:
        raise HTTPException(status_code=422, detail="Invalid cities detected")

    origin = route_geo.origin.name
    destination = route_geo.destination.name

    async with async_session() as session:
        user = await session.get(User, tma_user.user_id)
        if not user:
            user = User(
                id=tma_user.user_id,
                username=username,
                full_name=full_name,
            )
            session.add(user)
            await session.flush()
        elif username and not user.username:
            user.username = username
        if not user.full_name:
            user.full_name = full_name

        cargo = Cargo(
            owner_id=tma_user.user_id,
            from_city=origin,
            to_city=destination,
            cargo_type=body_type,
            weight=float(body.weight),
            volume=None,
            price=int(body.price),
            load_date=datetime.combine(body.load_date, datetime.min.time()),
            load_time=_normalize_text(body.load_time),
            comment=description,
            status=CargoStatus.NEW,
            payment_status=CargoPaymentStatus.UNSECURED,
        )
        session.add(cargo)
        await session.flush()

        trust_score = int(user.trust_score or 50)
        feed_event = ParserIngestEvent(
            stream_entry_id=f"manual-{uuid.uuid4().hex}",
            chat_id=f"user:{tma_user.user_id}",
            message_id=0,
            source="manual_client",
            from_city=origin,
            to_city=destination,
            body_type=body_type,
            phone=user.phone,
            inn=None,
            rate_rub=int(body.price),
            weight_t=float(body.weight),
            load_date=body.load_date.isoformat(),
            load_time=_normalize_text(body.load_time),
            cargo_description=description,
            payment_terms=payment_terms,
            is_direct_customer=True,
            dimensions=None,
            is_hot_deal=False,
            suggested_response=None,
            phone_blacklisted=False,
            from_lat=route_geo.origin.lat,
            from_lon=route_geo.origin.lon,
            to_lat=route_geo.destination.lat,
            to_lon=route_geo.destination.lon,
            trust_score=trust_score,
            trust_verdict=_trust_verdict(trust_score),
            trust_comment="Ручное размещение через Mini App",
            provider="manual",
            is_spam=False,
            status="synced",
            raw_text=_build_manual_raw_text(
                origin=origin,
                destination=destination,
                body_type=body_type,
                weight=float(body.weight),
                price=int(body.price),
                description=description,
            ),
            details_json=json.dumps(
                {
                    "created_via": "twa_manual_form",
                    "cargo_id": cargo.id,
                    "owner_id": tma_user.user_id,
                    "distance_km": route_geo.distance_km,
                },
                ensure_ascii=False,
            ),
        )
        session.add(feed_event)
        await session.commit()
        await session.refresh(feed_event)
        log_audit_event(
            session,
            entity_type="cargo",
            entity_id=int(cargo.id),
            action="cargo_manual_create",
            actor_user_id=tma_user.user_id,
            actor_role="user",
            meta={"feed_id": int(feed_event.id)},
        )
        await session.commit()

    await clear_cached("feed")
    background_tasks.add_task(notify_matching_carriers, cargo.id)
    return ManualCargoResponse(cargo_id=cargo.id, feed_id=feed_event.id)


@router.get("/api/v1/cargos/my", response_model=MyCargoResponse)
async def get_my_cargos(
    limit: int = Query(default=20, ge=1, le=100),
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> MyCargoResponse:
    async with async_session() as session:
        cargo_rows = (
            await session.execute(
                select(Cargo)
                .where(Cargo.owner_id == tma_user.user_id)
                .order_by(Cargo.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        cargo_rows = [row for row in cargo_rows if int(row.owner_id) == tma_user.user_id][:limit]

        event_rows = (
            await session.execute(
                select(ParserIngestEvent)
                .where(
                    ParserIngestEvent.source == "manual_client",
                    ParserIngestEvent.chat_id == f"user:{tma_user.user_id}",
                )
                .order_by(ParserIngestEvent.id.desc())
                .limit(max(limit * 3, 50))
            )
        ).scalars().all()

        escrow_rows = (
            await session.execute(
                select(EscrowDeal)
                .where(EscrowDeal.cargo_id.in_([cargo.id for cargo in cargo_rows]))
                .order_by(EscrowDeal.id.desc())
            )
        ).scalars().all() if cargo_rows else []

    event_by_cargo_id: dict[int, ParserIngestEvent] = {}
    for event in event_rows:
        for cargo in cargo_rows:
            if cargo.id not in event_by_cargo_id and _details_match_cargo_id(event.details_json, cargo.id):
                event_by_cargo_id[cargo.id] = event

    escrow_by_cargo_id: dict[int, EscrowDeal] = {}
    for escrow in escrow_rows:
        if int(escrow.cargo_id) not in escrow_by_cargo_id:
            escrow_by_cargo_id[int(escrow.cargo_id)] = escrow

    return MyCargoResponse(
        items=[_serialize_my_cargo(cargo, event_by_cargo_id.get(cargo.id), escrow_by_cargo_id.get(int(cargo.id))) for cargo in cargo_rows],
        limit=limit,
    )


@router.patch("/api/v1/cargos/{cargo_id}", response_model=CargoMutationResponse)
async def update_my_cargo(
    cargo_id: int,
    body: ManualCargoUpdate,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> CargoMutationResponse:
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo or int(cargo.owner_id) != tma_user.user_id:
            raise HTTPException(status_code=404, detail="Cargo not found")
        if cargo.status == CargoStatus.ARCHIVED:
            raise HTTPException(status_code=409, detail="Archived cargo cannot be edited")

        event = await _find_manual_feed_event(session, tma_user.user_id, cargo.id)
        route_geo = None
        route_touched = "origin" in updates or "destination" in updates
        if route_touched:
            next_origin = (updates.get("origin") or cargo.from_city or "").strip()
            next_destination = (updates.get("destination") or cargo.to_city or "").strip()
            route_geo = await get_geo_service().resolve_route(next_origin, next_destination)
            if not route_geo:
                raise HTTPException(status_code=422, detail="Invalid cities detected")

        if route_geo:
            cargo.from_city = route_geo.origin.name
            cargo.to_city = route_geo.destination.name
        elif "origin" in updates:
            cargo.from_city = updates["origin"].strip()
        elif "destination" in updates:
            cargo.to_city = updates["destination"].strip()
        if "body_type" in updates:
            cargo.cargo_type = updates["body_type"].strip()
        if "weight" in updates:
            cargo.weight = float(updates["weight"])
        if "price" in updates:
            cargo.price = int(updates["price"])
        if "load_date" in updates:
            cargo.load_date = datetime.combine(updates["load_date"], cargo.load_date.time())
        if "load_time" in updates:
            cargo.load_time = _normalize_text(updates["load_time"])
        if "description" in updates:
            cargo.comment = _normalize_text(updates["description"])

        if event:
            if route_geo:
                event.from_city = cargo.from_city
                event.to_city = cargo.to_city
                event.from_lat = route_geo.origin.lat
                event.from_lon = route_geo.origin.lon
                event.to_lat = route_geo.destination.lat
                event.to_lon = route_geo.destination.lon
                details = _load_details_payload(event.details_json)
                details["distance_km"] = route_geo.distance_km
                event.details_json = json.dumps(details, ensure_ascii=False)
            elif "origin" in updates:
                event.from_city = cargo.from_city
            elif "destination" in updates:
                event.to_city = cargo.to_city
            if "body_type" in updates:
                event.body_type = cargo.cargo_type
            if "weight" in updates:
                event.weight_t = float(cargo.weight)
            if "price" in updates:
                event.rate_rub = int(cargo.price)
            if "load_date" in updates:
                event.load_date = cargo.load_date.date().isoformat()
            if "load_time" in updates:
                event.load_time = cargo.load_time
            if "description" in updates:
                event.cargo_description = cargo.comment
            if "payment_terms" in updates:
                event.payment_terms = _normalize_text(updates["payment_terms"])

            event.raw_text = _build_manual_raw_text(
                origin=cargo.from_city,
                destination=cargo.to_city,
                body_type=cargo.cargo_type,
                weight=float(cargo.weight),
                price=int(cargo.price),
                description=cargo.comment,
            )

        await session.commit()

    await clear_cached("feed")
    return CargoMutationResponse(
        cargo_id=cargo_id,
        feed_id=event.id if event else None,
        status="updated",
    )


@router.post("/api/v1/cargos/{cargo_id}/archive", response_model=CargoMutationResponse)
async def archive_my_cargo(
    cargo_id: int,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> CargoMutationResponse:
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo or int(cargo.owner_id) != tma_user.user_id:
            raise HTTPException(status_code=404, detail="Cargo not found")

        cargo.status = CargoStatus.ARCHIVED
        event = await _find_manual_feed_event(session, tma_user.user_id, cargo.id)
        if event:
            event.status = "ignored"
            event.error = "archived_by_owner"

        await session.commit()

    await clear_cached("feed")
    return CargoMutationResponse(
        cargo_id=cargo_id,
        feed_id=event.id if event else None,
        status="archived",
    )
