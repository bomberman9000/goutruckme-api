import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_, desc, text

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.config import settings
from src.core.database import async_session
from src.core.models import (
    Cargo,
    CargoPaymentStatus,
    CargoStatus,
    CargoResponse,
    CompanyDetails,
    EscrowDeal,
    EscrowEvent,
    EscrowStatus,
    ReferralInvite,
    ReferralReward,
    UserWallet,
    User,
    Claim,
    ClaimStatus,
    AuditEvent,
)
from src.core.services.referral import build_referral_deeplink

router = APIRouter(tags=["webapp"])
templates = Jinja2Templates(directory="src/webapp/templates")
TWA_DIST_DIR = Path("frontend/twa/dist")
TWA_INDEX_FILE = TWA_DIST_DIR / "index.html"
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s\-()]{7,}\d)")


def _safe_meta_json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _get_webapp_url() -> str:
    """Base URL for WebApp links."""
    return "/webapp"


def _mask_phone_text(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7:
        return phone
    masked = f"{digits[:-4]}****"
    return f"+{masked}" if phone.strip().startswith("+") else masked


def _sanitize_public_comment(comment: str | None) -> str | None:
    if not comment:
        return comment
    return PHONE_RE.sub(lambda match: _mask_phone_text(match.group(0)), comment)


async def _ensure_webapp_user(session, tma_user: TelegramTMAUser) -> User:
    user_id = int(tma_user.user_id)
    user = await session.scalar(
        select(User).where(User.id == user_id)
    )
    if user:
        return user

    raw = tma_user.raw or {}
    username = raw.get("username")
    first_name = (raw.get("first_name") or "").strip()
    last_name = (raw.get("last_name") or "").strip()
    full_name = " ".join(
        part for part in (first_name, last_name) if part
    ).strip()
    if not full_name:
        full_name = (username or "").strip() or f"User {user_id}"

    user = User(
        id=user_id,
        username=username,
        full_name=full_name,
    )
    session.add(user)

    wallet = await session.get(UserWallet, user_id)
    if not wallet:
        session.add(UserWallet(user_id=user_id))

    await session.flush()
    return user


# --------------- HTML page ---------------

@router.get("/billing", response_class=HTMLResponse)
@router.get("/tariffs", response_class=HTMLResponse)
async def billing_page():
    """Deep-link: open the SPA at the billing section."""
    if TWA_INDEX_FILE.exists():
        return FileResponse(TWA_INDEX_FILE)
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/webapp")


@router.get("/webapp", response_class=HTMLResponse)
@router.get("/webapp/", response_class=HTMLResponse)
async def webapp_page(request: Request):
    """Serve the WebApp SPA."""
    if TWA_INDEX_FILE.exists():
        return FileResponse(TWA_INDEX_FILE)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "bot_username": settings.bot_username or "",
    })


# --------------- JSON API ---------------

@router.get("/api/webapp/cargos")
async def webapp_cargos(
    from_city: str | None = None,
    to_city: str | None = None,
    min_weight: float | None = None,
    max_weight: float | None = None,
    limit: int = 30,
):
    """List active cargos with optional filters."""
    async with async_session() as session:
        query = (
            select(Cargo)
            .where(Cargo.status == CargoStatus.NEW)
            .order_by(Cargo.created_at.desc())
        )
        if from_city:
            query = query.where(
                Cargo.from_city.ilike(f"%{from_city}%")
            )
        if to_city:
            query = query.where(
                Cargo.to_city.ilike(f"%{to_city}%")
            )
        if min_weight is not None:
            query = query.where(Cargo.weight >= min_weight)
        if max_weight is not None:
            query = query.where(Cargo.weight <= max_weight)

        result = await session.execute(query.limit(min(limit, 50)))
        cargos = result.scalars().all()

        items = []
        for c in cargos:
            items.append({
                "id": c.id,
                "from_city": c.from_city,
                "to_city": c.to_city,
                "cargo_type": c.cargo_type,
                "weight": c.weight,
                "volume": c.volume,
                "price": c.price,
                "load_date": c.load_date.strftime("%d.%m.%Y"),
                "load_time": c.load_time,
                "comment": _sanitize_public_comment(c.comment),
                "status": c.status.value,
                "created_at": c.created_at.isoformat(),
            })

    return {"cargos": items, "count": len(items)}


@router.get("/api/webapp/cargo/{cargo_id}")
async def webapp_cargo_detail(cargo_id: int):
    """Single cargo with owner company rating."""
    async with async_session() as session:
        cargo = (
            await session.execute(
                text(
                    """
                    SELECT
                        id,
                        owner_id,
                        from_city,
                        to_city,
                        cargo_type,
                        weight,
                        volume,
                        price,
                        load_date,
                        load_time,
                        comment,
                        status,
                        created_at
                    FROM cargos
                    WHERE id = :cargo_id
                    LIMIT 1
                    """
                ),
                {"cargo_id": cargo_id},
            )
        ).mappings().first()
        if not cargo:
            raise HTTPException(status_code=404, detail="Not found")

        owner = await session.scalar(
            select(User).where(User.id == cargo["owner_id"])
        )
        company = await session.scalar(
            select(CompanyDetails).where(
                CompanyDetails.user_id == cargo["owner_id"]
            )
        )

        responses_count = await session.scalar(
            select(func.count())
            .select_from(CargoResponse)
            .where(CargoResponse.cargo_id == cargo_id)
        )

        company_data = None
        if company:
            open_claims = await session.scalar(
                select(func.count())
                .select_from(Claim)
                .where(
                    Claim.to_company_id == company.id,
                    Claim.status == ClaimStatus.OPEN,
                )
            )
            company_data = {
                "id": company.id,
                "name": company.company_name,
                "inn": company.inn,
                "rating": company.total_rating,
                "open_claims": open_claims or 0,
            }

    return {
        "id": cargo["id"],
        "from_city": cargo["from_city"],
        "to_city": cargo["to_city"],
        "cargo_type": cargo["cargo_type"],
        "weight": cargo["weight"],
        "volume": cargo["volume"],
        "price": cargo["price"],
        "load_date": cargo["load_date"].strftime("%d.%m.%Y"),
        "load_time": cargo["load_time"],
        "comment": _sanitize_public_comment(cargo["comment"]),
        "status": cargo["status"].value if hasattr(cargo["status"], "value") else str(cargo["status"]),
        "created_at": cargo["created_at"].isoformat(),
        "owner": {
            "id": owner.id if owner else None,
            "name": owner.full_name if owner else "N/A",
        },
        "company": company_data,
        "responses_count": responses_count or 0,
    }


@router.post("/api/webapp/respond/{cargo_id}")
async def webapp_respond(
    cargo_id: int,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    """Submit a response to a cargo (requires initData auth)."""
    user_id = tma_user.user_id

    async with async_session() as session:
        cargo = await session.scalar(
            select(Cargo).where(Cargo.id == cargo_id)
        )
        if not cargo:
            raise HTTPException(status_code=404, detail="Cargo not found")
        if cargo.status != CargoStatus.NEW:
            raise HTTPException(
                status_code=400, detail="Cargo is not available"
            )
        if cargo.owner_id == user_id:
            raise HTTPException(
                status_code=400, detail="Cannot respond to own cargo"
            )

        existing = await session.scalar(
            select(CargoResponse).where(
                CargoResponse.cargo_id == cargo_id,
                CargoResponse.carrier_id == user_id,
            )
        )
        if existing:
            raise HTTPException(
                status_code=400, detail="Already responded"
            )

        resp = CargoResponse(
            cargo_id=cargo_id,
            carrier_id=user_id,
        )
        session.add(resp)
        await session.commit()

    return {"ok": True, "message": "Response submitted"}


@router.get("/api/webapp/profile")
async def webapp_profile(
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    """Current user profile with cargos and company info."""
    user_id = tma_user.user_id

    async with async_session() as session:
        user = await _ensure_webapp_user(session, tma_user)

        company = await session.scalar(
            select(CompanyDetails).where(
                CompanyDetails.user_id == user_id
            )
        )

        cargos_result = await session.execute(
            select(Cargo)
            .where(Cargo.owner_id == user_id)
            .order_by(Cargo.created_at.desc())
            .limit(10)
        )
        user_cargos = cargos_result.scalars().all()
        escrow_by_cargo_id: dict[int, EscrowDeal] = {}
        if user_cargos:
            escrow_result = await session.execute(
                select(EscrowDeal)
                .where(EscrowDeal.cargo_id.in_([int(c.id) for c in user_cargos]))
                .order_by(EscrowDeal.id.desc())
            )
            for deal in escrow_result.scalars().all():
                cargo_key = int(deal.cargo_id)
                if cargo_key not in escrow_by_cargo_id:
                    escrow_by_cargo_id[cargo_key] = deal
        wallet = await session.get(UserWallet, user_id)
        participant_deals = (
            await session.execute(
                select(EscrowDeal)
                .where(
                    or_(
                        EscrowDeal.client_id == user_id,
                        EscrowDeal.carrier_id == user_id,
                    )
                )
                .order_by(EscrowDeal.id.desc())
                .limit(20)
            )
        ).scalars().all()
        participant_deal_map = {int(deal.id): deal for deal in participant_deals}
        participant_cargo_ids = sorted({int(deal.cargo_id) for deal in participant_deals})
        participant_cargos = {}
        if participant_cargo_ids:
            participant_cargo_rows = (
                await session.execute(select(Cargo).where(Cargo.id.in_(participant_cargo_ids)))
            ).scalars().all()
            participant_cargos = {int(c.id): c for c in participant_cargo_rows}

        note_events_by_deal: dict[int, EscrowEvent] = {}
        if participant_deal_map:
            note_rows = (
                await session.execute(
                    select(EscrowEvent)
                    .where(EscrowEvent.escrow_deal_id.in_(participant_deal_map.keys()))
                    .where(EscrowEvent.event_type.in_(["admin_disputed", "admin_cancelled", "user_disputed", "refund_requested"]))
                    .order_by(desc(EscrowEvent.created_at))
                )
            ).scalars().all()
            for row in note_rows:
                deal_key = int(row.escrow_deal_id)
                if deal_key not in note_events_by_deal:
                    note_events_by_deal[deal_key] = row
        note_meta_by_deal = {
            deal_id: _safe_meta_json(row.payload_json)
            for deal_id, row in note_events_by_deal.items()
        }

        company_data = None
        if company:
            company_data = {
                "id": company.id,
                "name": company.company_name,
                "inn": company.inn,
                "rating": company.total_rating,
            }

        verified_payment_count = sum(
            1
            for cargo in user_cargos
            if getattr(cargo, "payment_status", None)
            in {
                CargoPaymentStatus.FUNDED,
                CargoPaymentStatus.DELIVERY_MARKED,
                CargoPaymentStatus.RELEASED,
            }
        )
        released_payment_count = sum(
            1
            for cargo in user_cargos
            if getattr(cargo, "payment_status", None) == CargoPaymentStatus.RELEASED
        )
        secured_amount_rub = sum(
            int(deal.amount_rub)
            for deal in escrow_by_cargo_id.values()
            if deal.status in {
                EscrowStatus.FUNDED,
                EscrowStatus.DELIVERY_MARKED,
                EscrowStatus.RELEASED,
            }
        )
        released_amount_rub = sum(
            int(deal.carrier_amount_rub)
            for deal in escrow_by_cargo_id.values()
            if deal.status == EscrowStatus.RELEASED
        )
        invited_count = int(
            await session.scalar(
                select(func.count()).select_from(ReferralInvite).where(
                    ReferralInvite.inviter_user_id == user_id
                )
            )
            or 0
        )
        activated_count = int(
            await session.scalar(
                select(func.count()).select_from(ReferralInvite).where(
                    ReferralInvite.inviter_user_id == user_id,
                    ReferralInvite.rewarded_at.is_not(None),
                )
            )
            or 0
        )
        rewards_count = int(
            await session.scalar(
                select(func.count()).select_from(ReferralReward).where(
                    ReferralReward.inviter_user_id == user_id
                )
            )
            or 0
        )
        reward_days_total = int(
            await session.scalar(
                select(func.coalesce(func.sum(ReferralReward.reward_days), 0)).where(
                    ReferralReward.inviter_user_id == user_id
                )
            )
            or 0
        )
        engagement_actions = [
            "cargo_manual_create",
            "cargo_match_view",
            "vehicle_create",
            "vehicle_available",
            "vehicle_match_view",
            "subscription_create",
            "escrow_created",
        ]
        engagement_cutoff = datetime.utcnow() - timedelta(days=7)
        engagement_rows = (
            await session.execute(
                select(AuditEvent.action, func.count())
                .where(
                    AuditEvent.actor_user_id == user_id,
                    AuditEvent.action.in_(engagement_actions),
                    AuditEvent.created_at >= engagement_cutoff,
                )
                .group_by(AuditEvent.action)
            )
        ).all()
        engagement_counts = {str(action): int(count) for action, count in engagement_rows}
        referral_link = build_referral_deeplink(settings.bot_username, user_id)
        ambassador_target = max(1, int(settings.referral_ambassador_threshold))

    return {
        "user": {
            "id": user.id,
            "name": user.full_name,
            "username": user.username,
            "phone": user.phone,
            "is_carrier": user.is_carrier,
            "is_verified": user.is_verified,
            "is_premium": user.is_premium,
            "premium_until": user.premium_until.isoformat() if user.premium_until else None,
        },
        "company": company_data,
        "wallet": {
            "balance_rub": int(wallet.balance_rub) if wallet else 0,
            "frozen_balance_rub": int(wallet.frozen_balance_rub) if wallet else 0,
        },
        "stats": {
            "cargo_count": len(user_cargos),
            "verified_payment_count": verified_payment_count,
            "released_payment_count": released_payment_count,
            "secured_amount_rub": secured_amount_rub,
            "released_amount_rub": released_amount_rub,
        },
        "referral": {
            "link": referral_link,
            "invited_count": invited_count,
            "activated_count": activated_count,
            "rewards_count": rewards_count,
            "reward_days_total": reward_days_total,
            "invited_bonus_days": activated_count * max(0, int(settings.referral_invited_reward_days)),
            "ambassador_target": ambassador_target,
            "is_ambassador": activated_count >= ambassador_target,
        },
        "engagement": {
            "window_days": 7,
            "created_cargos": engagement_counts.get("cargo_manual_create", 0),
            "opened_cargo_matches": engagement_counts.get("cargo_match_view", 0),
            "created_vehicles": engagement_counts.get("vehicle_create", 0),
            "activated_vehicles": engagement_counts.get("vehicle_available", 0),
            "opened_vehicle_matches": engagement_counts.get("vehicle_match_view", 0),
            "created_subscriptions": engagement_counts.get("subscription_create", 0),
            "enabled_honest_route": engagement_counts.get("escrow_created", 0),
        },
        "cargos": [
            {
                "id": c.id,
                "from_city": c.from_city,
                "to_city": c.to_city,
                "weight": c.weight,
                "price": c.price,
                "status": c.status.value,
                "payment_status": c.payment_status.value if getattr(c, "payment_status", None) else "unsecured",
                "payment_verified": getattr(c, "payment_status", None)
                in {
                    CargoPaymentStatus.FUNDED,
                    CargoPaymentStatus.DELIVERY_MARKED,
                    CargoPaymentStatus.RELEASED,
                },
                "escrow_id": escrow_by_cargo_id.get(int(c.id)).id if escrow_by_cargo_id.get(int(c.id)) else None,
                "escrow_status": (
                    escrow_by_cargo_id.get(int(c.id)).status.value
                    if escrow_by_cargo_id.get(int(c.id))
                    else None
                ),
                "escrow_amount_rub": int(escrow_by_cargo_id.get(int(c.id)).amount_rub) if escrow_by_cargo_id.get(int(c.id)) else None,
                "platform_fee_rub": int(escrow_by_cargo_id.get(int(c.id)).platform_fee_rub) if escrow_by_cargo_id.get(int(c.id)) else None,
                "carrier_amount_rub": int(escrow_by_cargo_id.get(int(c.id)).carrier_amount_rub) if escrow_by_cargo_id.get(int(c.id)) else None,
                "load_date": c.load_date.strftime("%d.%m.%Y"),
            }
            for c in user_cargos
        ],
        "refund_journal": [
            {
                "escrow_id": int(deal.id),
                "cargo_id": int(deal.cargo_id),
                "from_city": participant_cargos.get(int(deal.cargo_id)).from_city if participant_cargos.get(int(deal.cargo_id)) else None,
                "to_city": participant_cargos.get(int(deal.cargo_id)).to_city if participant_cargos.get(int(deal.cargo_id)) else None,
                "role": "client" if int(deal.client_id) == user_id else "carrier",
                "status": deal.status.value if isinstance(deal.status, EscrowStatus) else str(deal.status),
                "reason": note_meta_by_deal.get(int(deal.id), {}).get("reason"),
                "note": note_meta_by_deal.get(int(deal.id), {}).get("note"),
                "refund_amount_rub": note_meta_by_deal.get(int(deal.id), {}).get("refund_amount_rub"),
                "updated_at": (
                    note_events_by_deal[int(deal.id)].created_at.isoformat()
                    if int(deal.id) in note_events_by_deal
                    else deal.updated_at.isoformat()
                ),
            }
            for deal in participant_deals
            if deal.status in {EscrowStatus.DISPUTED, EscrowStatus.CANCELLED}
        ],
    }
