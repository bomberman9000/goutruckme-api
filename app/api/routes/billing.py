"""
Биллинг ГрузПоток: тарифы, баланс, ЮКасса.

Планы:
  free    — базовый (всегда)
  pro     — 990 ₽/мес | расширенные фичи
  business— 2990 ₽/мес | API + приоритет + все фичи

Эндпоинты:
  GET  /api/tariffs                      — список планов
  GET  /api/me/billing                   — мой баланс + план
  POST /api/payments/create              — создать платёж (ЮКасса или мок)
  POST /api/payments/yookassa/webhook    — вебхук подтверждения
"""

import hmac, hashlib, json, logging, uuid
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User, PlatformPayment
from app.core.config import settings

router = APIRouter()
log = logging.getLogger("billing")

# ── Тарифные планы ──────────────────────────────────────────────────────────
PLANS = {
    "free": {
        "id": "free", "name": "Free", "price_rub": 0, "period_days": 0,
        "features": [
            "До 5 грузов в месяц",
            "До 3 машин",
            "Базовый поиск",
            "Публичный профиль",
        ],
        "color": "#6b7280", "emoji": "🆓",
    },
    "pro": {
        "id": "pro", "name": "Pro", "price_rub": 990, "period_days": 30,
        "features": [
            "Неограниченные грузы",
            "Неограниченные машины",
            "AI-подбор перевозчиков",
            "Email-уведомления по маршруту",
            "Реферальная программа",
            "Приоритет в поиске",
            "Бейдж Pro в профиле",
        ],
        "color": "#2563eb", "emoji": "⭐",
    },
    "business": {
        "id": "business", "name": "Business", "price_rub": 2990, "period_days": 30,
        "features": [
            "Всё из Pro",
            "API-доступ",
            "Светофор Pro (арбитраж, лицензии)",
            "Выделенная поддержка",
            "Белый бейдж в реестре",
            "Аналитика по сделкам",
        ],
        "color": "#7c3aed", "emoji": "💼",
    },
}


# ── Auth helper ──────────────────────────────────────────────────────────────
def _auth(authorization: str | None, db: Session) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Необходима авторизация")
    token = authorization.split(" ", 1)[1]
    user = db.query(User).filter(User.api_token == token).first()
    if not user:
        raise HTTPException(401, "Недействительный токен")
    return user


def _current_plan(user: User) -> str:
    if user.pro_until and user.pro_until > datetime.utcnow():
        return user.billing_plan or "pro"
    return "free"


# ── GET /api/tariffs ─────────────────────────────────────────────────────────
@router.get("/tariffs")
def get_tariffs():
    return {"plans": list(PLANS.values())}


# ── GET /api/me/billing ──────────────────────────────────────────────────────
@router.get("/me/billing")
def get_billing(authorization: str | None = None, db: Session = Depends(get_db)):
    user = _auth(authorization, db)
    plan = _current_plan(user)
    pro_until = user.pro_until.isoformat() if (user.pro_until and user.pro_until > datetime.utcnow()) else None

    # Last payments
    history = db.query(PlatformPayment).filter(
        PlatformPayment.user_id == user.id,
    ).order_by(PlatformPayment.created_at.desc()).limit(10).all()

    return {
        "plan": plan,
        "plan_info": PLANS.get(plan, PLANS["free"]),
        "pro_until": pro_until,
        "payments": [
            {
                "id": p.id,
                "amount": p.amount_rub,
                "plan": p.plan,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in history
        ],
    }


# ── POST /api/payments/create ────────────────────────────────────────────────
@router.post("/payments/create")
async def create_payment(
    plan: str = Query(..., pattern="^(pro|business)$"),
    authorization: str | None = None,
    db: Session = Depends(get_db),
):
    """Создать платёж ЮКасса. Если ключ не настроен — возвращает mock URL."""
    user = _auth(authorization, db)

    plan_info = PLANS.get(plan)
    if not plan_info or plan_info["price_rub"] == 0:
        raise HTTPException(400, "Неверный план")

    amount = plan_info["price_rub"]
    idempotency_key = str(uuid.uuid4())

    # Записываем pending-платёж
    payment = PlatformPayment(
        user_id=user.id,
        plan=plan,
        amount_rub=amount,
        status="pending",
        idempotency_key=idempotency_key,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    # ЮКасса API
    yookassa_key = getattr(settings, "YOOKASSA_SECRET_KEY", "")
    yookassa_shop = getattr(settings, "YOOKASSA_SHOP_ID", "")

    if yookassa_key and yookassa_shop:
        try:
            return_url = f"{settings.PUBLIC_BASE_URL}/#/billing?success=1&plan={plan}"
            payload = {
                "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": return_url},
                "capture": True,
                "description": f"ГрузПоток {plan_info['name']} — {amount} ₽",
                "metadata": {"payment_db_id": payment.id, "user_id": user.id, "plan": plan},
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.yookassa.ru/v3/payments",
                    json=payload,
                    auth=(yookassa_shop, yookassa_key),
                    headers={"Idempotence-Key": idempotency_key},
                )
            data = resp.json()
            if resp.status_code == 200:
                payment.external_id = data.get("id")
                db.commit()
                return {
                    "payment_id": payment.id,
                    "confirmation_url": data["confirmation"]["confirmation_url"],
                    "amount": amount,
                }
            else:
                log.error(f"YooKassa error: {data}")
                raise HTTPException(502, "Ошибка платёжной системы")
        except httpx.RequestError as e:
            log.error(f"YooKassa request failed: {e}")
            raise HTTPException(502, "Платёжная система недоступна")
    else:
        # Мок: сразу активируем план (для тестирования)
        _activate_plan(user, plan, plan_info["period_days"], db)
        payment.status = "succeeded"
        payment.external_id = "mock-" + idempotency_key[:8]
        db.commit()
        log.info(f"Mock payment: user {user.id} → {plan}")
        return {
            "payment_id": payment.id,
            "confirmation_url": None,
            "mock": True,
            "message": f"YOOKASSA_SECRET_KEY не настроен — план {plan} активирован без оплаты (тест)",
            "amount": amount,
        }


# ── POST /api/payments/yookassa/webhook ─────────────────────────────────────
@router.post("/payments/yookassa/webhook")
async def yookassa_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    event = data.get("event", "")
    obj = data.get("object", {})

    if event != "payment.succeeded":
        return {"ok": True}

    metadata = obj.get("metadata", {})
    payment_db_id = metadata.get("payment_db_id")
    user_id = metadata.get("user_id")
    plan = metadata.get("plan")

    if not all([payment_db_id, user_id, plan]):
        log.warning(f"Webhook missing metadata: {metadata}")
        return {"ok": True}

    payment = db.query(PlatformPayment).filter(PlatformPayment.id == int(payment_db_id)).first()
    if not payment or payment.status == "succeeded":
        return {"ok": True}

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        return {"ok": True}

    plan_info = PLANS.get(plan, {})
    _activate_plan(user, plan, plan_info.get("period_days", 30), db)

    payment.status = "succeeded"
    payment.external_id = obj.get("id")
    db.commit()

    log.info(f"Payment confirmed: user {user_id} → {plan}")
    return {"ok": True}


def _activate_plan(user: User, plan: str, days: int, db: Session):
    now = datetime.utcnow()
    base = max(user.pro_until or now, now)
    user.pro_until = base + timedelta(days=days)
    user.billing_plan = plan
    db.commit()
