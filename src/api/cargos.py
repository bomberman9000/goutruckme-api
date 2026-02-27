from __future__ import annotations

import json
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.cache import clear_cached
from src.core.database import async_session
from src.core.models import Cargo, CargoStatus, ParserIngestEvent, User

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


@router.post("/api/v1/cargos/manual", response_model=ManualCargoResponse)
async def create_manual_cargo(
    body: ManualCargoCreate,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> ManualCargoResponse:
    from src.core.geo import city_coords

    origin = body.origin.strip()
    destination = body.destination.strip()
    body_type = body.body_type.strip()
    description = _normalize_text(body.description)
    payment_terms = _normalize_text(body.payment_terms)
    username, full_name = _ensure_user_full_name(tma_user.raw, tma_user.user_id)

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
        )
        session.add(cargo)
        await session.flush()

        from_coords = city_coords(origin)
        to_coords = city_coords(destination)
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
            from_lat=from_coords[0] if from_coords else None,
            from_lon=from_coords[1] if from_coords else None,
            to_lat=to_coords[0] if to_coords else None,
            to_lon=to_coords[1] if to_coords else None,
            trust_score=trust_score,
            trust_verdict=_trust_verdict(trust_score),
            trust_comment="Ручное размещение через Mini App",
            provider="manual",
            is_spam=False,
            status="synced",
            raw_text="\n".join(
                part
                for part in (
                    f"{origin} - {destination}",
                    body_type,
                    f"{body.weight}т",
                    f"{body.price}₽",
                    description or "",
                )
                if part
            ),
            details_json=json.dumps(
                {
                    "created_via": "twa_manual_form",
                    "cargo_id": cargo.id,
                    "owner_id": tma_user.user_id,
                },
                ensure_ascii=False,
            ),
        )
        session.add(feed_event)
        await session.commit()
        await session.refresh(feed_event)

    await clear_cached("feed")
    return ManualCargoResponse(cargo_id=cargo.id, feed_id=feed_event.id)
