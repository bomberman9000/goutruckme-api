"""Billing API: tariff plans + YooKassa payment integration."""
from __future__ import annotations

import json
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from src.core.config import settings
from src.core.database import async_session
from src.core.logger import logger
from src.core.models import PremiumPayment, User

router = APIRouter(tags=["billing"])

# ---------------------------------------------------------------------------
# Plan definitions
# ---------------------------------------------------------------------------

PLANS: dict[str, dict] = {
    "free": {
        "id": "free",
        "name": "Free",
        "price_rub": 0,
        "price_stars": 0,
        "period_days": 30,
        "features": [
            "5 AI-запросов в день",
            "Размещение грузов",
            "Поиск грузов и машин",
            "Уведомления по маршруту",
        ],
        "highlighted": False,
    },
    "pro": {
        "id": "pro",
        "name": "Pro",
        "price_rub": 499,
        "price_stars": settings.premium_stars_30d,
        "period_days": 30,
        "features": [
            "Безлимитный AI-ассистент",
            "Приоритет в ленте грузов",
            "AI-антифрод при размещении",
            "Скачивание PDF документов",
            "Расширенная аналитика",
        ],
        "highlighted": True,
    },
    "business": {
        "id": "business",
        "name": "Business",
        "price_rub": 999,
        "price_stars": 4500,
        "period_days": 30,
        "features": [
            "Всё из Pro",
            "API доступ",
            "До 10 сотрудников",
            "Эскроу сделки",
            "Приоритетная поддержка",
        ],
        "highlighted": False,
    },
}


@router.get("/api/billing/plans")
async def get_plans():
    """Public: list all available tariff plans."""
    return {"plans": list(PLANS.values())}


@router.get("/api/billing/status")
async def billing_status(user_id: int):
    """Current subscription status for a user."""
    async with async_session() as session:
        user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    active = user.is_premium and (
        user.premium_until is None or user.premium_until >= datetime.utcnow()
    )
    return {
        "user_id": user_id,
        "is_premium": active,
        "premium_until": user.premium_until.isoformat() if user.premium_until else None,
        "plan": "pro" if active else "free",
    }


# ---------------------------------------------------------------------------
# YooKassa helpers
# ---------------------------------------------------------------------------

YOOKASSA_BASE = "https://api.yookassa.ru/v3"


def _yk_client() -> httpx.AsyncClient:
    shop_id = settings.yookassa_shop_id
    secret = settings.yookassa_secret_key
    if not shop_id or not secret:
        raise HTTPException(status_code=503, detail="YooKassa не настроена")
    return httpx.AsyncClient(
        base_url=YOOKASSA_BASE,
        auth=(shop_id, secret),
        timeout=15.0,
    )


# ---------------------------------------------------------------------------
# Create payment
# ---------------------------------------------------------------------------

class CreatePaymentRequest(BaseModel):
    plan_id: str = Field(..., pattern="^(pro|business)$")
    user_id: int
    return_url: str = Field(..., min_length=10)


@router.post("/api/billing/create-payment")
async def create_payment(req: CreatePaymentRequest):
    """Create a YooKassa payment and return the redirect URL."""
    plan = PLANS.get(req.plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail="Неизвестный тариф")

    idempotence_key = str(uuid.uuid4())
    payload = {
        "amount": {"value": f"{plan['price_rub']}.00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": req.return_url},
        "capture": True,
        "description": f"GoTruck {plan['name']} — {plan['period_days']} дней",
        "metadata": {
            "user_id": str(req.user_id),
            "plan_id": req.plan_id,
            "period_days": str(plan["period_days"]),
        },
    }

    async with _yk_client() as client:
        resp = await client.post(
            "/payments",
            json=payload,
            headers={"Idempotence-Key": idempotence_key},
        )

    if resp.status_code not in (200, 201):
        logger.error(
            "billing.create_payment status=%d body=%s", resp.status_code, resp.text[:500]
        )
        raise HTTPException(status_code=502, detail="Ошибка создания платежа")

    data = resp.json()
    confirmation = data.get("confirmation", {})
    return {
        "payment_id": data["id"],
        "status": data["status"],
        "payment_url": confirmation.get("confirmation_url", ""),
    }


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

async def _activate_premium(
    user_id: int, plan_id: str, period_days: int, payment_id: str
) -> None:
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            logger.warning("billing.activate user_not_found user_id=%d", user_id)
            return

        from src.core.services.referral import extend_premium_until
        await extend_premium_until(session, user, days=period_days)

        rec = PremiumPayment(
            user_id=user_id,
            plan_days=period_days,
            amount_stars=0,
            currency="RUB",
            status="success",
            invoice_payload=plan_id,
            provider_payment_charge_id=payment_id,
        )
        session.add(rec)
        await session.commit()

    logger.info(
        "billing.premium_activated user_id=%d plan=%s days=%d",
        user_id, plan_id, period_days,
    )

    # Notify user via bot
    try:
        from src.bot.bot import bot
        plan = PLANS.get(plan_id, PLANS["pro"])
        await bot.send_message(
            user_id,
            f"✅ <b>Подписка {plan['name']} активирована!</b>\n\n"
            f"Действует <b>{period_days} дней</b>.\n"
            f"Наслаждайся безлимитным AI и приоритетом в ленте 🚀",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("billing.notify_user error=%s", e)


@router.post("/api/billing/webhook")
async def yookassa_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle YooKassa payment.succeeded events."""
    body = await request.body()
    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = data.get("event")
    obj = data.get("object", {})

    logger.info(
        "billing.webhook event=%s payment_id=%s status=%s",
        event, obj.get("id"), obj.get("status"),
    )

    if event == "payment.succeeded" and obj.get("status") == "succeeded":
        meta = obj.get("metadata", {})
        try:
            user_id = int(meta.get("user_id", 0))
            plan_id = meta.get("plan_id", "pro")
            period_days = int(meta.get("period_days", 30))
            payment_id = obj.get("id", "")
        except (TypeError, ValueError) as e:
            logger.error("billing.webhook meta_parse error=%s", e)
            return {"ok": False}

        if user_id:
            background_tasks.add_task(
                _activate_premium, user_id, plan_id, period_days, payment_id
            )

    return {"ok": True}
