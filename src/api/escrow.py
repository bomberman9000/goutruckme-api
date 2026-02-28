from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.cache import clear_cached
from src.core.config import settings
from src.core.database import async_session
from src.core.models import (
    AuditEvent,
    Cargo,
    CargoPaymentStatus,
    EscrowDeal,
    EscrowEvent,
    EscrowStatus,
    UserWallet,
)
from src.core.services.banking import get_bank_client

router = APIRouter(prefix="/api/v1/escrow", tags=["escrow"])


class EscrowCreateRequest(BaseModel):
    amount_rub: int | None = Field(default=None, gt=0, le=1_000_000_000)


class EscrowIssueRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class EscrowActionResponse(BaseModel):
    ok: bool = True
    cargo_id: int
    escrow_id: int
    status: str
    payment_status: str
    amount_rub: int
    platform_fee_rub: int
    carrier_amount_rub: int
    payment_url: str | None = None
    provider: str


class WalletResponse(BaseModel):
    user_id: int
    balance_rub: int
    frozen_balance_rub: int


def _payment_status_value(value: CargoPaymentStatus | str | None) -> str:
    if isinstance(value, CargoPaymentStatus):
        return value.value
    if value:
        return str(value)
    return CargoPaymentStatus.UNSECURED.value


def _verified_payment(status: CargoPaymentStatus | str | None) -> bool:
    return _payment_status_value(status) in {
        CargoPaymentStatus.FUNDED.value,
        CargoPaymentStatus.DELIVERY_MARKED.value,
        CargoPaymentStatus.RELEASED.value,
    }


def _fee_amount(amount_rub: int, percent: float) -> int:
    return int(round(amount_rub * (percent / 100.0)))


async def _ensure_wallet(session, user_id: int) -> UserWallet:
    wallet = await session.get(UserWallet, user_id)
    if wallet:
        return wallet
    wallet = UserWallet(user_id=user_id, balance_rub=0, frozen_balance_rub=0)
    session.add(wallet)
    await session.flush()
    return wallet


async def _get_latest_deal(session, cargo_id: int) -> EscrowDeal | None:
    return await session.scalar(
        select(EscrowDeal)
        .where(EscrowDeal.cargo_id == cargo_id)
        .order_by(EscrowDeal.id.desc())
        .limit(1)
    )


async def _append_escrow_event(
    session,
    *,
    deal_id: int,
    event_type: str,
    actor_user_id: int | None,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        EscrowEvent(
            escrow_deal_id=deal_id,
            event_type=event_type,
            actor_user_id=actor_user_id,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
        )
    )


async def _append_audit(session, *, cargo_id: int, action: str, actor_user_id: int | None, meta: dict[str, Any]) -> None:
    session.add(
        AuditEvent(
            entity_type="cargo",
            entity_id=cargo_id,
            action=action,
            actor_user_id=actor_user_id,
            actor_role="user" if actor_user_id else None,
            meta_json=json.dumps(meta, ensure_ascii=False),
        )
    )


def _serialize_response(deal: EscrowDeal, cargo: Cargo) -> EscrowActionResponse:
    return EscrowActionResponse(
        cargo_id=int(cargo.id),
        escrow_id=int(deal.id),
        status=deal.status.value if isinstance(deal.status, EscrowStatus) else str(deal.status),
        payment_status=_payment_status_value(getattr(cargo, "payment_status", None)),
        amount_rub=int(deal.amount_rub),
        platform_fee_rub=int(deal.platform_fee_rub),
        carrier_amount_rub=int(deal.carrier_amount_rub),
        payment_url=deal.payment_link,
        provider=deal.provider,
    )


@router.post("/{cargo_id}/create", response_model=EscrowActionResponse)
async def create_escrow_for_cargo(
    cargo_id: int,
    body: EscrowCreateRequest | None = None,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> EscrowActionResponse:
    if not settings.escrow_enabled:
        raise HTTPException(status_code=503, detail="Escrow is disabled")

    requested_amount = int(body.amount_rub) if body and body.amount_rub else None
    bank_client = get_bank_client()

    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo or int(cargo.owner_id) != tma_user.user_id:
            raise HTTPException(status_code=404, detail="Cargo not found")

        latest = await _get_latest_deal(session, cargo_id)
        if latest and latest.status in {
            EscrowStatus.PAYMENT_PENDING,
            EscrowStatus.FUNDED,
            EscrowStatus.DELIVERY_MARKED,
        }:
            return _serialize_response(latest, cargo)

        amount_rub = requested_amount or int(cargo.price)
        platform_fee = _fee_amount(amount_rub, settings.escrow_platform_fee_percent)
        carrier_amount = max(amount_rub - platform_fee, 0)

        await _ensure_wallet(session, int(cargo.owner_id))
        if cargo.carrier_id:
            await _ensure_wallet(session, int(cargo.carrier_id))

        deal = EscrowDeal(
            cargo_id=int(cargo.id),
            client_id=int(cargo.owner_id),
            carrier_id=int(cargo.carrier_id) if cargo.carrier_id else None,
            amount_rub=amount_rub,
            platform_fee_rub=platform_fee,
            carrier_amount_rub=carrier_amount,
            status=EscrowStatus.PAYMENT_PENDING,
        )
        session.add(deal)
        await session.flush()

        link = await bank_client.create_payment_link(
            cargo_id=int(cargo.id),
            escrow_id=int(deal.id),
            amount_rub=amount_rub,
        )
        deal.provider = link.provider
        deal.tochka_payment_id = link.provider_payment_id
        deal.payment_link = link.payment_url

        cargo.payment_status = CargoPaymentStatus.PAYMENT_PENDING
        cargo.payment_verified_at = None

        await _append_escrow_event(
            session,
            deal_id=int(deal.id),
            event_type="created",
            actor_user_id=tma_user.user_id,
            payload={"amount_rub": amount_rub, "provider": link.provider},
        )
        await _append_audit(
            session,
            cargo_id=int(cargo.id),
            action="escrow_created",
            actor_user_id=tma_user.user_id,
            meta={"escrow_id": int(deal.id), "amount_rub": amount_rub},
        )

        await session.commit()
        await session.refresh(deal)

    await clear_cached("feed")
    return _serialize_response(deal, cargo)


@router.get("/{cargo_id}", response_model=EscrowActionResponse)
async def get_escrow_status(
    cargo_id: int,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> EscrowActionResponse:
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo:
            raise HTTPException(status_code=404, detail="Cargo not found")
        if tma_user.user_id not in {int(cargo.owner_id), int(cargo.carrier_id or 0)}:
            raise HTTPException(status_code=403, detail="Forbidden")
        deal = await _get_latest_deal(session, cargo_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Escrow not found")
        return _serialize_response(deal, cargo)


@router.get("/wallet/me", response_model=WalletResponse)
async def get_my_wallet(
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> WalletResponse:
    async with async_session() as session:
        wallet = await _ensure_wallet(session, tma_user.user_id)
        await session.commit()
        return WalletResponse(
            user_id=tma_user.user_id,
            balance_rub=int(wallet.balance_rub),
            frozen_balance_rub=int(wallet.frozen_balance_rub),
        )


async def _apply_funded(session, *, cargo: Cargo, deal: EscrowDeal, amount_rub: int, payment_id: str, actor_user_id: int | None) -> None:
    if deal.status in {EscrowStatus.FUNDED, EscrowStatus.DELIVERY_MARKED, EscrowStatus.RELEASED}:
        return
    client_wallet = await _ensure_wallet(session, int(deal.client_id))
    client_wallet.balance_rub += int(amount_rub)
    client_wallet.frozen_balance_rub += int(amount_rub)
    deal.status = EscrowStatus.FUNDED
    deal.tochka_payment_id = payment_id or deal.tochka_payment_id
    deal.funded_at = datetime.now(UTC)
    cargo.payment_status = CargoPaymentStatus.FUNDED
    cargo.payment_verified_at = datetime.now(UTC)
    await _append_escrow_event(
        session,
        deal_id=int(deal.id),
        event_type="funded",
        actor_user_id=actor_user_id,
        payload={"amount_rub": int(amount_rub), "payment_id": payment_id},
    )
    await _append_audit(
        session,
        cargo_id=int(cargo.id),
        action="escrow_funded",
        actor_user_id=actor_user_id,
        meta={"escrow_id": int(deal.id), "amount_rub": int(amount_rub)},
    )


@router.get("/{cargo_id}/pay/mock", response_class=HTMLResponse)
async def complete_mock_payment(
    cargo_id: int,
    escrow_id: int = Query(..., ge=1),
    payment_id: str = Query(..., min_length=8),
    token: str = Query(..., min_length=16),
    amount: int = Query(..., gt=0),
) -> HTMLResponse:
    bank_client = get_bank_client()
    if not getattr(bank_client, "supports_mock_checkout", False):
        raise HTTPException(status_code=409, detail="Mock payment is unavailable")
    if not hasattr(bank_client, "verify_token") or not bank_client.verify_token(escrow_id, payment_id, token):
        raise HTTPException(status_code=403, detail="Invalid payment token")

    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        deal = await session.get(EscrowDeal, escrow_id)
        if not cargo or not deal or int(deal.cargo_id) != cargo_id:
            raise HTTPException(status_code=404, detail="Escrow not found")

        await _apply_funded(
            session,
            cargo=cargo,
            deal=deal,
            amount_rub=amount,
            payment_id=payment_id,
            actor_user_id=None,
        )
        await session.commit()

    await clear_cached("feed")
    return HTMLResponse(
        content=(
            "<html><body style='font-family:sans-serif;padding:24px'>"
            "<h2>Оплата подтверждена</h2>"
            "<p>Средства зарезервированы для безопасной сделки. Можно вернуться в ГрузПоток.</p>"
            "</body></html>"
        )
    )


@router.post("/{cargo_id}/mark-delivered", response_model=EscrowActionResponse)
async def mark_escrow_delivered(
    cargo_id: int,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> EscrowActionResponse:
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo:
            raise HTTPException(status_code=404, detail="Cargo not found")
        deal = await _get_latest_deal(session, cargo_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if deal.status != EscrowStatus.FUNDED:
            raise HTTPException(status_code=409, detail="Escrow is not funded")
        if tma_user.user_id not in {int(cargo.owner_id), int(cargo.carrier_id or 0)}:
            if cargo.carrier_id is None:
                cargo.carrier_id = tma_user.user_id
                deal.carrier_id = tma_user.user_id
                await _ensure_wallet(session, tma_user.user_id)
            else:
                raise HTTPException(status_code=403, detail="Forbidden")

        deal.status = EscrowStatus.DELIVERY_MARKED
        cargo.payment_status = CargoPaymentStatus.DELIVERY_MARKED
        await _append_escrow_event(
            session,
            deal_id=int(deal.id),
            event_type="delivery_marked",
            actor_user_id=tma_user.user_id,
            payload={"carrier_id": int(cargo.carrier_id or 0)},
        )
        await _append_audit(
            session,
            cargo_id=int(cargo.id),
            action="escrow_delivery_marked",
            actor_user_id=tma_user.user_id,
            meta={"escrow_id": int(deal.id)},
        )
        await session.commit()

    await clear_cached("feed")
    return _serialize_response(deal, cargo)


@router.post("/{cargo_id}/release", response_model=EscrowActionResponse)
async def release_escrow(
    cargo_id: int,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> EscrowActionResponse:
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo or int(cargo.owner_id) != tma_user.user_id:
            raise HTTPException(status_code=404, detail="Cargo not found")
        deal = await _get_latest_deal(session, cargo_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if deal.status != EscrowStatus.DELIVERY_MARKED:
            raise HTTPException(status_code=409, detail="Escrow is not ready for release")

        payout = await get_bank_client(deal.provider).release_funds(
            escrow_id=int(deal.id),
            cargo_id=int(cargo.id),
            amount_rub=int(deal.carrier_amount_rub),
            carrier_user_id=int(deal.carrier_id or cargo.carrier_id or deal.client_id),
        )

        client_wallet = await _ensure_wallet(session, int(deal.client_id))
        carrier_wallet = await _ensure_wallet(session, int(deal.carrier_id or cargo.carrier_id or deal.client_id))

        client_wallet.balance_rub = max(int(client_wallet.balance_rub) - int(deal.amount_rub), 0)
        client_wallet.frozen_balance_rub = max(int(client_wallet.frozen_balance_rub) - int(deal.amount_rub), 0)
        carrier_wallet.balance_rub += int(deal.carrier_amount_rub)

        deal.status = EscrowStatus.RELEASED
        deal.released_at = datetime.now(UTC)
        cargo.payment_status = CargoPaymentStatus.RELEASED
        cargo.payment_verified_at = cargo.payment_verified_at or datetime.now(UTC)

        await _append_escrow_event(
            session,
            deal_id=int(deal.id),
            event_type="released",
            actor_user_id=tma_user.user_id,
            payload={
                "amount_rub": int(deal.amount_rub),
                "carrier_amount_rub": int(deal.carrier_amount_rub),
                "platform_fee_rub": int(deal.platform_fee_rub),
                "provider": payout.provider,
                "provider_payout_id": payout.provider_payout_id,
            },
        )
        await _append_audit(
            session,
            cargo_id=int(cargo.id),
            action="escrow_released",
            actor_user_id=tma_user.user_id,
            meta={
                "escrow_id": int(deal.id),
                "amount_rub": int(deal.amount_rub),
                "carrier_amount_rub": int(deal.carrier_amount_rub),
                "provider": payout.provider,
                "provider_payout_id": payout.provider_payout_id,
            },
        )
        await session.commit()

    await clear_cached("feed")
    return _serialize_response(deal, cargo)


@router.post("/{cargo_id}/dispute", response_model=EscrowActionResponse)
async def dispute_escrow(
    cargo_id: int,
    body: EscrowIssueRequest | None = None,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> EscrowActionResponse:
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo:
            raise HTTPException(status_code=404, detail="Cargo not found")
        deal = await _get_latest_deal(session, cargo_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if tma_user.user_id not in {int(cargo.owner_id), int(cargo.carrier_id or 0)}:
            raise HTTPException(status_code=403, detail="Forbidden")
        if deal.status in {EscrowStatus.RELEASED, EscrowStatus.CANCELLED}:
            raise HTTPException(status_code=409, detail="Escrow is already closed")

        reason = (body.reason if body else None) or ""
        note = (body.note if body else None) or ""
        reason_value = reason.strip()[:120]
        note_value = note.strip()[:500]

        deal.status = EscrowStatus.DISPUTED
        cargo.payment_status = CargoPaymentStatus.DISPUTED
        await _append_escrow_event(
            session,
            deal_id=int(deal.id),
            event_type="user_disputed",
            actor_user_id=tma_user.user_id,
            payload={"reason": reason_value, "note": note_value},
        )
        await _append_audit(
            session,
            cargo_id=int(cargo.id),
            action="escrow_user_disputed",
            actor_user_id=tma_user.user_id,
            meta={"escrow_id": int(deal.id), "reason": reason_value, "note": note_value},
        )
        await session.commit()

    await clear_cached("feed")
    return _serialize_response(deal, cargo)


@router.post("/{cargo_id}/request-refund", response_model=EscrowActionResponse)
async def request_escrow_refund(
    cargo_id: int,
    body: EscrowIssueRequest | None = None,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> EscrowActionResponse:
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo or int(cargo.owner_id) != tma_user.user_id:
            raise HTTPException(status_code=404, detail="Cargo not found")
        deal = await _get_latest_deal(session, cargo_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if deal.status in {EscrowStatus.RELEASED, EscrowStatus.CANCELLED}:
            raise HTTPException(status_code=409, detail="Escrow is already closed")

        reason = (body.reason if body else None) or ""
        note = (body.note if body else None) or ""
        reason_value = reason.strip()[:120]
        note_value = note.strip()[:500]

        deal.status = EscrowStatus.DISPUTED
        cargo.payment_status = CargoPaymentStatus.DISPUTED
        await _append_escrow_event(
            session,
            deal_id=int(deal.id),
            event_type="refund_requested",
            actor_user_id=tma_user.user_id,
            payload={"reason": reason_value, "note": note_value},
        )
        await _append_audit(
            session,
            cargo_id=int(cargo.id),
            action="escrow_refund_requested",
            actor_user_id=tma_user.user_id,
            meta={"escrow_id": int(deal.id), "reason": reason_value, "note": note_value},
        )
        await session.commit()

    await clear_cached("feed")
    return _serialize_response(deal, cargo)


@router.post("/webhook/tochka", response_model=EscrowActionResponse)
async def handle_tochka_webhook(
    payload: dict[str, Any],
    x_internal_token: str | None = Header(default=None),
) -> EscrowActionResponse:
    if settings.escrow_provider.lower() == "mock" and settings.internal_token:
        if x_internal_token != settings.internal_token:
            raise HTTPException(status_code=401, detail="Invalid internal token")

    normalized = await get_bank_client(str(payload.get("provider") or None)).parse_webhook(payload)
    if normalized.status != "funded":
        raise HTTPException(status_code=409, detail="Unsupported webhook status")

    async with async_session() as session:
        deal = await session.get(EscrowDeal, int(normalized.escrow_id))
        cargo = await session.get(Cargo, int(normalized.cargo_id))
        if not deal or not cargo or int(deal.cargo_id) != int(cargo.id):
            raise HTTPException(status_code=404, detail="Escrow not found")
        await _apply_funded(
            session,
            cargo=cargo,
            deal=deal,
            amount_rub=int(normalized.amount_rub),
            payment_id=str(normalized.payment_id),
            actor_user_id=None,
        )
        await session.commit()

    await clear_cached("feed")
    return _serialize_response(deal, cargo)
