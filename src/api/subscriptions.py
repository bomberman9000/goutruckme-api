from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.database import async_session
from src.core.models import RouteSubscription

router = APIRouter(tags=["subscriptions"])


class SubscriptionCreate(BaseModel):
    from_city: str | None = Field(default=None, max_length=100)
    to_city: str | None = Field(default=None, max_length=100)
    body_type: str | None = Field(default=None, max_length=64)
    min_rate: int | None = Field(default=None, ge=0)
    max_weight: float | None = Field(default=None, ge=0)
    region: str | None = Field(default=None, max_length=64)


class SubscriptionItem(BaseModel):
    id: int
    from_city: str | None
    to_city: str | None
    body_type: str | None
    min_rate: int | None
    max_weight: float | None
    region: str | None
    is_active: bool


class SubscriptionListResponse(BaseModel):
    items: list[SubscriptionItem]


class SubscriptionResponse(BaseModel):
    ok: bool = True
    item: SubscriptionItem


def _clean_str(value: str | None) -> str | None:
    clean = (value or "").strip()
    return clean or None


def _serialize(sub: RouteSubscription) -> SubscriptionItem:
    return SubscriptionItem(
        id=sub.id,
        from_city=sub.from_city,
        to_city=sub.to_city,
        body_type=sub.body_type,
        min_rate=sub.min_rate,
        max_weight=sub.max_weight,
        region=sub.region,
        is_active=bool(sub.is_active),
    )


@router.get("/api/v1/subscriptions", response_model=SubscriptionListResponse)
async def list_subscriptions(
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> SubscriptionListResponse:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(RouteSubscription)
                .where(RouteSubscription.user_id == tma_user.user_id)
                .where(RouteSubscription.is_active.is_(True))
                .order_by(RouteSubscription.id.desc())
            )
        ).scalars().all()

    return SubscriptionListResponse(items=[_serialize(row) for row in rows])


@router.post("/api/v1/subscriptions", response_model=SubscriptionResponse)
async def create_subscription(
    body: SubscriptionCreate,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> SubscriptionResponse:
    payload = {
        "from_city": _clean_str(body.from_city),
        "to_city": _clean_str(body.to_city),
        "body_type": _clean_str(body.body_type),
        "min_rate": body.min_rate,
        "max_weight": body.max_weight,
        "region": _clean_str(body.region),
    }
    if not any(value is not None for value in payload.values()):
        raise HTTPException(status_code=400, detail="At least one filter is required")

    async with async_session() as session:
        existing_rows = (
            await session.execute(
                select(RouteSubscription)
                .where(RouteSubscription.user_id == tma_user.user_id)
                .where(RouteSubscription.is_active.is_(True))
                .order_by(RouteSubscription.id.desc())
                .limit(100)
            )
        ).scalars().all()

        for row in existing_rows:
            if (
                row.from_city == payload["from_city"]
                and row.to_city == payload["to_city"]
                and row.body_type == payload["body_type"]
                and row.min_rate == payload["min_rate"]
                and row.max_weight == payload["max_weight"]
                and row.region == payload["region"]
            ):
                return SubscriptionResponse(item=_serialize(row))

        sub = RouteSubscription(user_id=tma_user.user_id, is_active=True, **payload)
        session.add(sub)
        await session.commit()
        await session.refresh(sub)

    return SubscriptionResponse(item=_serialize(sub))


@router.delete("/api/v1/subscriptions/{subscription_id}")
async def delete_subscription(
    subscription_id: int,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> dict[str, bool]:
    async with async_session() as session:
        sub = await session.get(RouteSubscription, subscription_id)
        if not sub or int(sub.user_id) != tma_user.user_id:
            raise HTTPException(status_code=404, detail="Subscription not found")
        sub.is_active = False
        await session.commit()

    return {"ok": True}
