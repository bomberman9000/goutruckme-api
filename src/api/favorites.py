"""CRM-lite: favorites / «Мои рейсы» endpoints.

Users can save cargos, add notes, and track their pipeline.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.database import async_session
from src.core.models import Favorite, ParserIngestEvent

router = APIRouter(tags=["favorites"])


class FavoriteItem(BaseModel):
    id: int
    feed_id: int
    note: str | None
    status: str
    created_at: datetime
    from_city: str | None = None
    to_city: str | None = None
    body_type: str | None = None
    rate_rub: int | None = None
    phone: str | None = None
    load_date: str | None = None
    is_hot_deal: bool = False


class FavoriteListResponse(BaseModel):
    items: list[FavoriteItem] = Field(default_factory=list)
    total: int


class AddFavoriteRequest(BaseModel):
    note: str | None = None


class AddFavoriteResponse(BaseModel):
    ok: bool
    favorite_id: int


class UpdateFavoriteRequest(BaseModel):
    note: str | None = None
    status: str | None = None


@router.post("/api/v1/favorites/{feed_id}", response_model=AddFavoriteResponse)
async def add_favorite(
    feed_id: int,
    body: AddFavoriteRequest | None = None,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> AddFavoriteResponse:
    """Save a cargo to user's personal list."""
    user_id = tma_user.user_id
    note = body.note if body else None

    async with async_session() as session:
        event = await session.get(ParserIngestEvent, feed_id)
        if not event:
            raise HTTPException(status_code=404, detail="feed item not found")

        existing = await session.scalar(
            select(Favorite).where(
                Favorite.user_id == user_id,
                Favorite.feed_id == feed_id,
            )
        )
        if existing:
            if note is not None:
                existing.note = note
                await session.commit()
            return AddFavoriteResponse(ok=True, favorite_id=existing.id)

        fav = Favorite(user_id=user_id, feed_id=feed_id, note=note)
        session.add(fav)
        await session.commit()
        await session.refresh(fav)

    return AddFavoriteResponse(ok=True, favorite_id=fav.id)


@router.get("/api/v1/favorites", response_model=FavoriteListResponse)
async def list_favorites(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> FavoriteListResponse:
    """List user's saved cargos with cargo details."""
    user_id = tma_user.user_id

    async with async_session() as session:
        stmt = select(Favorite).where(Favorite.user_id == user_id)
        if status:
            stmt = stmt.where(Favorite.status == status.strip())
        stmt = stmt.order_by(Favorite.id.desc()).limit(limit)
        favs = (await session.execute(stmt)).scalars().all()

        total = await session.scalar(
            select(func.count()).select_from(Favorite).where(Favorite.user_id == user_id)
        )

        feed_ids = [f.feed_id for f in favs]
        events_map: dict[int, ParserIngestEvent] = {}
        if feed_ids:
            events = (
                await session.execute(
                    select(ParserIngestEvent).where(ParserIngestEvent.id.in_(feed_ids))
                )
            ).scalars().all()
            events_map = {e.id: e for e in events}

    items = []
    for fav in favs:
        ev = events_map.get(fav.feed_id)
        items.append(FavoriteItem(
            id=fav.id,
            feed_id=fav.feed_id,
            note=fav.note,
            status=fav.status,
            created_at=fav.created_at,
            from_city=ev.from_city if ev else None,
            to_city=ev.to_city if ev else None,
            body_type=ev.body_type if ev else None,
            rate_rub=ev.rate_rub if ev else None,
            phone=ev.phone if ev else None,
            load_date=ev.load_date if ev else None,
            is_hot_deal=ev.is_hot_deal if ev else False,
        ))

    return FavoriteListResponse(items=items, total=total or 0)


@router.patch("/api/v1/favorites/{feed_id}", response_model=AddFavoriteResponse)
async def update_favorite(
    feed_id: int,
    body: UpdateFavoriteRequest,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> AddFavoriteResponse:
    """Update note or status of a saved cargo."""
    async with async_session() as session:
        fav = await session.scalar(
            select(Favorite).where(
                Favorite.user_id == tma_user.user_id,
                Favorite.feed_id == feed_id,
            )
        )
        if not fav:
            raise HTTPException(status_code=404, detail="favorite not found")

        if body.note is not None:
            fav.note = body.note
        if body.status is not None:
            if body.status not in ("saved", "in_progress", "completed", "cancelled"):
                raise HTTPException(status_code=400, detail="invalid status")
            fav.status = body.status
        await session.commit()

    return AddFavoriteResponse(ok=True, favorite_id=fav.id)


@router.delete("/api/v1/favorites/{feed_id}")
async def delete_favorite(
    feed_id: int,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    """Remove a cargo from favorites."""
    async with async_session() as session:
        fav = await session.scalar(
            select(Favorite).where(
                Favorite.user_id == tma_user.user_id,
                Favorite.feed_id == feed_id,
            )
        )
        if not fav:
            raise HTTPException(status_code=404, detail="favorite not found")
        await session.delete(fav)
        await session.commit()

    return {"ok": True}
