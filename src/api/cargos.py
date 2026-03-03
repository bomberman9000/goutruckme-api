from __future__ import annotations

import json
import uuid
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.audit import log_audit_event
from src.core.ai import parse_cargo_nlp
from src.core.cache import clear_cached
from src.core.database import async_session
from src.core.models import Cargo, CargoPaymentStatus, CargoStatus, EscrowDeal, EscrowStatus, ParserIngestEvent, User
from src.core.services.geo_service import get_geo_service
from src.core.services.notification_dispatcher import notify_matching_carriers

router = APIRouter(tags=["cargos"])


class ManualCargoCreate(BaseModel):
    raw_text: str | None = Field(default=None, min_length=5, max_length=4000)
    origin: str | None = Field(default=None, min_length=2, max_length=100)
    destination: str | None = Field(default=None, min_length=2, max_length=100)
    body_type: str | None = Field(default=None, min_length=2, max_length=100)
    weight: float | None = Field(default=None, gt=0, le=1000)
    volume: float | None = Field(default=None, gt=0, le=10_000)
    price: int | None = Field(default=None, gt=0, le=1_000_000_000)
    load_date: date | None = None
    load_time: str | None = Field(default=None, max_length=10)
    description: str | None = Field(default=None, max_length=1000)
    payment_terms: str | None = Field(default=None, max_length=120)


class ManualCargoPreviewRequest(BaseModel):
    raw_text: str = Field(min_length=5, max_length=4000)


class ManualCargoParsedPreview(BaseModel):
    from_city: str
    to_city: str
    body_type: str
    cargo_type: str
    weight: float
    volume_m3: float | None = None
    price: int | None = None
    load_date: str | None = None
    load_time: str | None = None
    price_source: str
    ai_score: int
    ai_verdict: str
    ai_comment: str


class ManualCargoPreviewResponse(BaseModel):
    ok: bool = True
    parsed: ManualCargoParsedPreview


class ManualCargoResponse(BaseModel):
    ok: bool = True
    cargo_id: int
    feed_id: int


class RecommendedRateResponse(BaseModel):
    ok: bool = True
    origin: str
    destination: str
    distance_km: int
    recommended_rate_rub: int
    min_rate_rub: int
    max_rate_rub: int
    rate_per_km: int
    source: str
    details: str


class MyCargoItem(BaseModel):
    id: int
    from_city: str
    to_city: str
    body_type: str
    weight: float
    volume: float | None = None
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
    volume: float | None = Field(default=None, gt=0, le=10_000)
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


MANUAL_FEED_SOURCES = ("manual_client", "manual_web")


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
    volume: float | None,
    price: int,
    description: str | None,
) -> str:
    return "\n".join(
        part
        for part in (
            f"{origin} - {destination}",
            body_type,
            f"{weight}т",
            (f"{volume:g}м3" if volume else ""),
            f"{price}₽",
            description or "",
        )
        if part
    )


def _format_volume_dimensions(volume: float | None) -> str | None:
    if volume is None:
        return None
    return f"{volume:g} м³"


def _build_ai_score_preview(
    *,
    stated_price: int | None,
    estimated_price: int | None,
    has_volume: bool,
    cargo_type: str,
    body_type: str,
) -> tuple[int, str, str, str]:
    score = 58
    notes: list[str] = ["маршрут подтверждён"]
    price_source = "missing"

    if stated_price and estimated_price:
        price_source = "provided"
        ratio = stated_price / max(1, estimated_price)
        if 0.75 <= ratio <= 1.35:
            score += 18
            notes.append("цена близка к рынку")
        elif 0.55 <= ratio <= 1.7:
            score += 8
            notes.append("цена в рабочем диапазоне")
        else:
            score -= 10
            notes.append("цена выбивается из рынка")
    elif stated_price:
        price_source = "provided"
        score += 8
        notes.append("цена указана вручную")
    elif estimated_price:
        price_source = "estimated"
        score += 12
        notes.append("цена рассчитана автоматически")

    if has_volume:
        score += 5
        notes.append("есть кубатура")
    if cargo_type and cargo_type != "Груз":
        score += 6
        notes.append(f"характер груза: {cargo_type.lower()}")
    if body_type and body_type != "тент":
        score += 3
        notes.append(f"тип кузова: {body_type}")

    score = max(0, min(100, score))
    if score >= 75:
        verdict = "green"
        comment = "Маршрут реалистичный, цена близка к рынку, риск низкий."
    elif score >= 45:
        verdict = "yellow"
        comment = "Маршрут выглядит рабочим, но проверь цену и условия перед публикацией."
    else:
        verdict = "red"
        comment = "Данных мало или цена выглядит рискованно. Проверь детали вручную."

    return score, verdict, comment, price_source


async def _build_smart_preview(raw_text: str) -> tuple[ManualCargoParsedPreview, object, int | None]:
    parsed = await parse_cargo_nlp(raw_text)
    if not parsed:
        raise HTTPException(
            status_code=422,
            detail="Не удалось распознать данные груза. Добавьте маршрут, вес или кубатуру в текст.",
        )

    origin = str(parsed.get("from_city") or "").strip()
    destination = str(parsed.get("to_city") or "").strip()
    if not origin or not destination:
        raise HTTPException(status_code=422, detail="Не удалось распознать маршрут. Укажи откуда и куда в тексте.")

    if parsed.get("weight") is None:
        raise HTTPException(status_code=422, detail="Не удалось распознать вес. Укажи тоннаж или килограммы.")

    route_geo = await get_geo_service().resolve_route(origin, destination)
    if not route_geo:
        raise HTTPException(status_code=422, detail="Invalid cities detected")

    parsed_body_type = str(parsed.get("body_type") or "").strip()
    parsed_cargo_type = str(parsed.get("cargo_type") or "").strip()
    if not parsed_body_type and parsed_cargo_type in {"тент", "рефрижератор", "трал", "борт", "контейнер", "изотерм"}:
        parsed_body_type = parsed_cargo_type
        parsed_cargo_type = "Груз"

    body_type = parsed_body_type or "тент"
    cargo_type = parsed_cargo_type or "Груз"
    weight = float(parsed["weight"])
    volume = float(parsed["volume_m3"]) if parsed.get("volume_m3") is not None else None
    stated_price = int(parsed["price"]) if parsed.get("price") else None

    estimated_price: int | None = None
    try:
        estimate = await _estimate_recommended_rate(
            route_geo.origin.name,
            route_geo.destination.name,
            weight,
            body_type,
        )
        raw_price = estimate.get("price")
        if isinstance(raw_price, int) and raw_price > 0:
            estimated_price = raw_price
    except Exception:
        estimated_price = None

    ai_score, ai_verdict, ai_comment, price_source = _build_ai_score_preview(
        stated_price=stated_price,
        estimated_price=estimated_price,
        has_volume=volume is not None,
        cargo_type=cargo_type,
        body_type=body_type,
    )

    preview = ManualCargoParsedPreview(
        from_city=route_geo.origin.name,
        to_city=route_geo.destination.name,
        body_type=body_type,
        cargo_type=cargo_type,
        weight=weight,
        volume_m3=volume,
        price=stated_price or estimated_price,
        load_date=parsed.get("load_date"),
        load_time=parsed.get("load_time"),
        price_source=price_source,
        ai_score=ai_score,
        ai_verdict=ai_verdict,
        ai_comment=ai_comment,
    )
    return preview, route_geo, estimated_price


async def _estimate_recommended_rate(
    origin: str,
    destination: str,
    weight: float,
    body_type: str,
) -> dict:
    from src.core.ai import estimate_price_smart

    return await estimate_price_smart(origin, destination, weight, body_type)


async def _find_manual_feed_event(session, owner_id: int, cargo_id: int) -> ParserIngestEvent | None:
    rows = (
        await session.execute(
            select(ParserIngestEvent)
            .where(
                ParserIngestEvent.source.in_(MANUAL_FEED_SOURCES),
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
        volume=float(cargo.volume) if cargo.volume is not None else None,
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


async def _resolve_manual_create_payload(body: ManualCargoCreate) -> dict:
    raw_text = _normalize_text(body.raw_text)
    description = _normalize_text(body.description)
    payment_terms = _normalize_text(body.payment_terms)

    if raw_text:
        parsed = await parse_cargo_nlp(raw_text)
        if not parsed:
            raise HTTPException(
                status_code=422,
                detail="Не удалось распознать маршрут и тоннаж. Добавьте города и вес в текст.",
            )

        origin = str(parsed["from_city"]).strip()
        destination = str(parsed["to_city"]).strip()
        body_type = str(parsed.get("cargo_type") or "тент").strip()
        weight = float(parsed["weight"])
        volume = body.volume if body.volume is not None else parsed.get("volume_m3")
        price = body.price
        parsed_load_date = parsed.get("load_date")
        load_date = body.load_date
        if load_date is None and parsed_load_date:
            load_date = date.fromisoformat(str(parsed_load_date))
        load_date = load_date or date.today()
        load_time = _normalize_text(body.load_time) or _normalize_text(parsed.get("load_time"))
        return {
            "origin": origin,
            "destination": destination,
            "body_type": body_type,
            "weight": weight,
            "volume": float(volume) if volume is not None else None,
            "price": int(price) if price is not None else None,
            "load_date": load_date,
            "load_time": load_time,
            "description": description or raw_text,
            "payment_terms": payment_terms,
            "raw_text": raw_text,
        }

    required_fields = {
        "origin": body.origin,
        "destination": body.destination,
        "body_type": body.body_type,
        "weight": body.weight,
        "price": body.price,
        "load_date": body.load_date,
    }
    missing = [name for name, value in required_fields.items() if value is None]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required fields: {', '.join(missing)}",
        )

    return {
        "origin": body.origin.strip(),
        "destination": body.destination.strip(),
        "body_type": body.body_type.strip(),
        "weight": float(body.weight),
        "volume": float(body.volume) if body.volume is not None else None,
        "price": int(body.price),
        "load_date": body.load_date,
        "load_time": _normalize_text(body.load_time),
        "description": description,
        "payment_terms": payment_terms,
        "raw_text": None,
    }


@router.post("/api/v1/cargos/manual/preview", response_model=ManualCargoPreviewResponse)
async def preview_manual_cargo(
    body: ManualCargoPreviewRequest,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> ManualCargoPreviewResponse:
    del tma_user
    preview, _route_geo, _estimated = await _build_smart_preview(body.raw_text)
    return ManualCargoPreviewResponse(parsed=preview)


@router.post("/api/v1/cargos/manual", response_model=ManualCargoResponse)
async def create_manual_cargo(
    body: ManualCargoCreate,
    background_tasks: BackgroundTasks,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> ManualCargoResponse:
    payload = await _resolve_manual_create_payload(body)
    origin = payload["origin"]
    destination = payload["destination"]
    body_type = payload["body_type"]
    weight = float(payload["weight"])
    volume = payload["volume"]
    price = payload["price"]
    description = payload["description"]
    payment_terms = payload["payment_terms"]
    username, full_name = _ensure_user_full_name(tma_user.raw, tma_user.user_id)
    route_geo = await get_geo_service().resolve_route(origin, destination)
    if not route_geo:
        raise HTTPException(status_code=422, detail="Invalid cities detected")

    origin = route_geo.origin.name
    destination = route_geo.destination.name
    if price is None:
        estimate = await _estimate_recommended_rate(origin, destination, weight, body_type)
        estimated_price = estimate.get("price")
        if not isinstance(estimated_price, int) or estimated_price <= 0:
            raise HTTPException(status_code=422, detail="Не удалось определить ставку. Укажите цену вручную.")
        price = int(estimated_price)

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
            weight=weight,
            volume=volume,
            price=price,
            load_date=datetime.combine(payload["load_date"], datetime.min.time()),
            load_time=payload["load_time"],
            comment=description,
            source_platform="manual_web",
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
            source="manual_web",
            from_city=origin,
            to_city=destination,
            body_type=body_type,
            phone=user.phone,
            inn=None,
            rate_rub=price,
            weight_t=weight,
            load_date=payload["load_date"].isoformat(),
            load_time=payload["load_time"],
            cargo_description=description,
            payment_terms=payment_terms,
            is_direct_customer=True,
            dimensions=_format_volume_dimensions(volume),
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
            raw_text=payload["raw_text"] or _build_manual_raw_text(
                origin=origin,
                destination=destination,
                body_type=body_type,
                weight=weight,
                volume=volume,
                price=price,
                description=description,
            ),
            details_json=json.dumps(
                {
                    "created_via": "twa_manual_form" if not payload["raw_text"] else "twa_smart_paste",
                    "cargo_id": cargo.id,
                    "owner_id": tma_user.user_id,
                    "distance_km": route_geo.distance_km,
                    "volume_m3": volume,
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


@router.get("/api/v1/cargos/recommended-rate", response_model=RecommendedRateResponse)
async def get_recommended_rate(
    origin: str = Query(min_length=2, max_length=100),
    destination: str = Query(min_length=2, max_length=100),
    weight: float = Query(gt=0, le=1000),
    body_type: str = Query(default="тент", min_length=2, max_length=100),
) -> RecommendedRateResponse:
    route_geo = await get_geo_service().resolve_route(origin, destination)
    if not route_geo:
        raise HTTPException(status_code=422, detail="Invalid cities detected")

    normalized_origin = route_geo.origin.name
    normalized_destination = route_geo.destination.name
    normalized_body_type = body_type.strip()
    distance_km = max(1, int(route_geo.distance_km))

    estimate = await _estimate_recommended_rate(
        normalized_origin,
        normalized_destination,
        float(weight),
        normalized_body_type,
    )

    recommended_rate = estimate.get("price")
    source = str(estimate.get("source") or "unknown")
    details = str(estimate.get("details") or "").strip()

    if not isinstance(recommended_rate, int) or recommended_rate <= 0:
        fallback_rate_per_km = int(max(30, min(50, 35 + min(float(weight), 20.0) * 0.5)))
        recommended_rate = distance_km * fallback_rate_per_km
        source = "geo_calculated"
        details = (
            "📐 Расчёт по расстоянию\n"
            f"• Дистанция: ~{distance_km} км\n"
            f"• Ставка: ~{fallback_rate_per_km} ₽/км"
        )

    rate_per_km = max(1, round(recommended_rate / distance_km))
    if source == "calculated" or source == "geo_calculated":
        min_rate = distance_km * 30
        max_rate = distance_km * 50
    else:
        spread = max(5_000, int(recommended_rate * 0.12))
        min_rate = max(1, recommended_rate - spread)
        max_rate = recommended_rate + spread

    return RecommendedRateResponse(
        origin=normalized_origin,
        destination=normalized_destination,
        distance_km=distance_km,
        recommended_rate_rub=int(recommended_rate),
        min_rate_rub=int(min_rate),
        max_rate_rub=int(max_rate),
        rate_per_km=int(rate_per_km),
        source=source,
        details=details or "📊 Рекомендованная ставка рассчитана автоматически",
    )


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
                    ParserIngestEvent.source.in_(MANUAL_FEED_SOURCES),
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
        if "volume" in updates:
            cargo.volume = float(updates["volume"]) if updates["volume"] is not None else None
        if "price" in updates:
            cargo.price = int(updates["price"])
        if "load_date" in updates:
            cargo.load_date = datetime.combine(updates["load_date"], cargo.load_date.time())
        if "load_time" in updates:
            cargo.load_time = _normalize_text(updates["load_time"])
        if "description" in updates:
            cargo.comment = _normalize_text(updates["description"])

        if event:
            details = _load_details_payload(event.details_json)
            if route_geo:
                event.from_city = cargo.from_city
                event.to_city = cargo.to_city
                event.from_lat = route_geo.origin.lat
                event.from_lon = route_geo.origin.lon
                event.to_lat = route_geo.destination.lat
                event.to_lon = route_geo.destination.lon
                details["distance_km"] = route_geo.distance_km
            elif "origin" in updates:
                event.from_city = cargo.from_city
            elif "destination" in updates:
                event.to_city = cargo.to_city
            if "body_type" in updates:
                event.body_type = cargo.cargo_type
            if "weight" in updates:
                event.weight_t = float(cargo.weight)
            if "volume" in updates:
                event.dimensions = _format_volume_dimensions(cargo.volume)
                details["volume_m3"] = cargo.volume
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
                volume=float(cargo.volume) if cargo.volume is not None else None,
                price=int(cargo.price),
                description=cargo.comment,
            )
            event.details_json = json.dumps(details, ensure_ascii=False)

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
