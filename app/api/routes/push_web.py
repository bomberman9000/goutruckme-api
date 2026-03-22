"""
Web Push Notifications API.
GET  /api/push/vapid-public-key  — получить публичный VAPID ключ
POST /api/push/subscribe          — сохранить подписку
POST /api/push/send-test          — тестовая рассылка (admin)
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import PushSubscription, User
from app.core.config import settings
from app.core.security import SECRET_KEY, ALGORITHM
from jose import jwt

logger = logging.getLogger(__name__)
router = APIRouter()


def _user_from_token(authorization: Optional[str], db: Session) -> Optional[User]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid = payload.get("sub") or payload.get("id")
        if not uid:
            return None
        return db.query(User).filter(User.id == int(uid), User.is_active == True).first()
    except Exception:
        return None


class SubscribeBody(BaseModel):
    endpoint: str
    p256dh:   str
    auth:     str


@router.get("/push/vapid-public-key")
def get_vapid_public_key():
    """Return VAPID public key for frontend PushManager.subscribe()."""
    if not settings.VAPID_PUBLIC_KEY:
        raise HTTPException(503, "Push notifications not configured")
    return {"publicKey": settings.VAPID_PUBLIC_KEY}


@router.post("/push/subscribe", status_code=201)
def subscribe(
    body: SubscribeBody,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    """Save or update a push subscription."""
    user = _user_from_token(authorization, db)
    existing = db.query(PushSubscription).filter(
        PushSubscription.endpoint == body.endpoint
    ).first()
    if existing:
        existing.p256dh  = body.p256dh
        existing.auth    = body.auth
        existing.user_id = user.id if user else existing.user_id
        db.commit()
        return {"ok": True, "action": "updated"}
    sub = PushSubscription(
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        user_id=user.id if user else None,
    )
    db.add(sub)
    db.commit()
    return {"ok": True, "action": "created"}


@router.delete("/push/subscribe")
def unsubscribe(
    body: SubscribeBody,
    db: Session = Depends(get_db),
):
    existing = db.query(PushSubscription).filter(
        PushSubscription.endpoint == body.endpoint
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
    return {"ok": True}
