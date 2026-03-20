"""
Реферальная программа ГрузПоток.
GET  /api/me/referral              — мой код, ссылка, статистика
POST /api/me/referral/apply        — применить чужой код при регистрации
GET  /r/{code}                     — редирект на регистрацию (SEO-friendly)
"""
import secrets
import string
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User
from app.core.config import settings

router = APIRouter()

SITE_URL = getattr(settings, "PUBLIC_BASE_URL", "https://gruzpotok.ru")
PRO_DAYS = 30


def _make_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "GTP-" + "".join(secrets.choice(chars) for _ in range(6))


def _ensure_code(user: User, db: Session) -> str:
    if user.referral_code:
        return user.referral_code
    for _ in range(10):
        code = _make_code()
        if not db.query(User).filter(User.referral_code == code).first():
            user.referral_code = code
            db.commit()
            return code
    raise RuntimeError("Could not generate unique referral code")


def _auth(authorization: str | None, db: Session) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Необходима авторизация")
    token = authorization.split(" ", 1)[1]
    user = db.query(User).filter(User.api_token == token).first()
    if not user:
        raise HTTPException(401, "Недействительный токен")
    return user


def _is_pro(user: User) -> bool:
    if user.pro_until and user.pro_until > datetime.utcnow():
        return True
    return bool(user.verified)  # verified = pro for now


# ── GET /api/me/referral ───────────────────────────────────────────────────
@router.get("/me/referral")
def get_my_referral(authorization: str | None = None, db: Session = Depends(get_db)):
    user = _auth(authorization, db)
    code = _ensure_code(user, db)
    referral_url = f"{SITE_URL}/?ref={code}"

    pro = _is_pro(user)
    pro_until = user.pro_until.isoformat() if (user.pro_until and user.pro_until > datetime.utcnow()) else None

    return {
        "code":         code,
        "url":          referral_url,
        "referral_count": user.referral_count or 0,
        "is_pro":       pro,
        "pro_until":    pro_until,
        "reward":       f"{PRO_DAYS} дней Pro за каждого приглашённого",
    }


# ── POST /api/me/referral/apply ────────────────────────────────────────────
@router.post("/me/referral/apply")
def apply_referral(
    code: str = Query(..., min_length=4),
    authorization: str | None = None,
    db: Session = Depends(get_db),
):
    """Применить реферальный код — засчитывает инвайтеру +30 дней Pro."""
    user = _auth(authorization, db)

    if user.referred_by:
        raise HTTPException(409, "Реферальный код уже применён")
    if user.referral_code == code:
        raise HTTPException(400, "Нельзя использовать свой код")

    inviter = db.query(User).filter(User.referral_code == code).first()
    if not inviter:
        raise HTTPException(404, "Код не найден")

    # Mark user as referred
    user.referred_by = inviter.id

    # Credit inviter: extend pro_until
    now = datetime.utcnow()
    base = max(inviter.pro_until or now, now)
    inviter.pro_until = base + timedelta(days=PRO_DAYS)
    inviter.referral_count = (inviter.referral_count or 0) + 1

    db.commit()
    return {
        "ok": True,
        "inviter_id": inviter.id,
        "message": f"Код применён! {inviter.company or inviter.fullname or 'Партнёр'} получил +{PRO_DAYS} дней Pro",
    }


# ── GET /r/{code} ─────────────────────────────────────────────────────────
@router.get("/r/{code}", response_class=HTMLResponse)
def referral_redirect(code: str, db: Session = Depends(get_db)):
    """Реферальный лендинг — редирект на регистрацию с кодом."""
    inviter = db.query(User).filter(User.referral_code == code).first()

    inviter_name = "Коллега из ГрузПоток"
    if inviter:
        inviter_name = inviter.company or inviter.organization_name or inviter.fullname or inviter_name

    # Meta-redirect + JS redirect
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Приглашение в ГрузПоток — {inviter_name}</title>
<meta name="description" content="{inviter_name} приглашает вас на ГрузПоток — биржу грузоперевозок. Бесплатная регистрация.">
<meta http-equiv="refresh" content="0; url=/?ref={code}">
</head>
<body style="font-family:sans-serif;text-align:center;padding:60px 20px;background:#f8fafc">
<div style="max-width:400px;margin:0 auto">
  <div style="font-size:48px;margin-bottom:16px">🚚</div>
  <h1 style="color:#1e3a8a;margin:0 0 8px">ГрузПоток</h1>
  <p style="color:#6b7280">{inviter_name} приглашает вас на платформу</p>
  <p style="color:#6b7280;font-size:14px">Перенаправляем...</p>
  <a href="/?ref={code}" style="display:inline-block;margin-top:20px;padding:12px 32px;background:#1e3a8a;color:#fff;border-radius:10px;text-decoration:none;font-weight:700">
    Зарегистрироваться →
  </a>
</div>
<script>window.location.href = '/?ref={code}';</script>
</body>
</html>"""
