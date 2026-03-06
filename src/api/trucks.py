from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.config import settings
from src.core.database import async_session
from src.core.matching import match_trucks
from src.core.models import AvailableTruck, TruckContactUnlock, User
from src.core.truck_search import extract_truck_search_params

router = APIRouter(tags=["trucks"])


class TruckSearchRequest(BaseModel):
    raw_text: str | None = None
    from_city: str | None = None
    to_city: str | None = None
    weight: float | None = None
    truck_type: str | None = None
    top_n: int = Field(default=3, ge=1, le=10)


class TruckSearchItem(BaseModel):
    id: int
    truck_type: str | None
    capacity_tons: float | None
    base_city: str | None
    base_region: str | None
    routes: str | None
    can_view_contact: bool
    is_unlocked: bool = False
    phone: str | None = None
    source_url: str | None = None
    unlock_bot_link: str | None = None


class TruckSearchResponse(BaseModel):
    ok: bool = True
    query: dict[str, str | float | None]
    total: int
    unlock_price_stars: int
    premium_stars_30d: int
    items: list[TruckSearchItem] = Field(default_factory=list)


class TruckRecentResponse(BaseModel):
    ok: bool = True
    total: int
    unlock_price_stars: int
    premium_stars_30d: int
    items: list[TruckSearchItem] = Field(default_factory=list)


def _is_premium_user(user: User | None) -> bool:
    if not user or not user.is_premium:
        return False
    if user.premium_until is None:
        return True
    return user.premium_until >= datetime.now()


def _unlock_bot_link(truck_id: int) -> str | None:
    username = (settings.bot_username or "").strip().lstrip("@")
    if not username:
        return None
    return f"https://t.me/{username}?start=unlock_truck_{truck_id}"


async def _load_unlocked_ids(user_id: int, truck_ids: list[int]) -> set[int]:
    if not truck_ids:
        return set()
    async with async_session() as session:
        rows = (
            await session.execute(
                select(TruckContactUnlock.truck_id).where(
                    TruckContactUnlock.user_id == user_id,
                    TruckContactUnlock.truck_id.in_(truck_ids),
                    TruckContactUnlock.status == "success",
                )
            )
        ).scalars().all()
    return {int(row) for row in rows}


def _serialize_trucks(
    trucks: list,
    *,
    is_premium: bool,
    unlocked_ids: set[int],
) -> list[TruckSearchItem]:
    items: list[TruckSearchItem] = []
    for truck in trucks:
        can_view = is_premium or truck.id in unlocked_ids
        items.append(
            TruckSearchItem(
                id=truck.id,
                truck_type=truck.truck_type,
                capacity_tons=truck.capacity_tons,
                base_city=truck.base_city,
                base_region=truck.base_region,
                routes=truck.routes,
                can_view_contact=can_view,
                is_unlocked=truck.id in unlocked_ids,
                phone=truck.phone if can_view else None,
                source_url=truck.avito_url if can_view else None,
                unlock_bot_link=None if can_view else _unlock_bot_link(truck.id),
            )
        )
    return items


@router.post("/api/v1/trucks/search", response_model=TruckSearchResponse)
async def search_trucks(
    body: TruckSearchRequest,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    from_city = body.from_city
    to_city = body.to_city
    weight = body.weight
    truck_type = body.truck_type

    if body.raw_text and not any([from_city, to_city, weight, truck_type]):
        parsed = await extract_truck_search_params(body.raw_text)
        if parsed:
            from_city = parsed.get("from_city")
            to_city = parsed.get("to_city")
            weight = parsed.get("weight")
            truck_type = parsed.get("truck_type")

    if not any([from_city, to_city, weight, truck_type]):
        raise HTTPException(status_code=400, detail="Не удалось распознать запрос поиска машины")

    async with async_session() as session:
        user = await session.get(User, tma_user.user_id)
        is_premium = _is_premium_user(user)
        trucks = await match_trucks(
            session,
            from_city=from_city,
            to_city=to_city,
            truck_type=truck_type,
            capacity_tons=weight,
            top_n=body.top_n,
        )
    unlocked_ids = set()
    if not is_premium:
        unlocked_ids = await _load_unlocked_ids(tma_user.user_id, [truck.id for truck in trucks])

    return TruckSearchResponse(
        query={
            "from_city": from_city,
            "to_city": to_city,
            "weight": weight,
            "truck_type": truck_type,
        },
        total=len(trucks),
        unlock_price_stars=settings.truck_contact_unlock_stars,
        premium_stars_30d=settings.premium_stars_30d,
        items=_serialize_trucks(trucks, is_premium=is_premium, unlocked_ids=unlocked_ids),
    )


@router.get("/api/v1/trucks/recent", response_model=TruckRecentResponse)
async def recent_trucks(
    limit: int = Query(default=12, ge=1, le=50),
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    async with async_session() as session:
        user = await session.get(User, tma_user.user_id)
        is_premium = _is_premium_user(user)
        trucks = (
            await session.execute(
                select(AvailableTruck)
                .where(AvailableTruck.is_active.is_(True))
                .order_by(AvailableTruck.last_seen_at.desc(), AvailableTruck.id.desc())
                .limit(limit)
            )
        ).scalars().all()

    unlocked_ids = set()
    if not is_premium:
        unlocked_ids = await _load_unlocked_ids(tma_user.user_id, [truck.id for truck in trucks])

    return TruckRecentResponse(
        total=len(trucks),
        unlock_price_stars=settings.truck_contact_unlock_stars,
        premium_stars_30d=settings.premium_stars_30d,
        items=_serialize_trucks(trucks, is_premium=is_premium, unlocked_ids=unlocked_ids),
    )
