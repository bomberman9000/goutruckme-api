"""Utility to send Web Push notifications to all or specific users."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_vapid_claims():
    from app.core.config import settings
    return {
        "sub": settings.VAPID_SUBJECT or "mailto:admin@gruzpotok.ru",
    }


def send_web_push(endpoint: str, p256dh: str, auth: str, title: str, body: str, url: str = "/") -> bool:
    try:
        from pywebpush import webpush, WebPushException
        from app.core.config import settings
        import json

        payload = json.dumps({
            "title": title,
            "body":  body,
            "url":   url,
        })

        webpush(
            subscription_info={
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth},
            },
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims=_get_vapid_claims(),
        )
        return True
    except Exception as e:
        logger.warning("web_push error endpoint=%s error=%s", endpoint[:60], e)
        return False


def broadcast_push(title: str, body: str, url: str = "/", user_id: Optional[int] = None) -> int:
    """Send push to all subscriptions (or specific user if user_id given)."""
    from app.db.database import SessionLocal
    from app.models.models import PushSubscription

    db = SessionLocal()
    try:
        q = db.query(PushSubscription)
        if user_id:
            q = q.filter(PushSubscription.user_id == user_id)
        subs = q.all()
        sent = 0
        dead = []
        for sub in subs:
            ok = send_web_push(sub.endpoint, sub.p256dh, sub.auth, title, body, url)
            if ok:
                sent += 1
            else:
                dead.append(sub.id)
        # Remove dead subscriptions (410 Gone)
        if dead:
            db.query(PushSubscription).filter(PushSubscription.id.in_(dead)).delete(synchronize_session=False)
            db.commit()
        return sent
    finally:
        db.close()
