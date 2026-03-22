"""
Email-рассылка — дайджест новых грузов по подписанным маршрутам.

API:
  GET  /api/me/route-subscriptions       — список подписок
  POST /api/me/route-subscriptions       — подписаться
  DELETE /api/me/route-subscriptions/{id} — отписаться

Джоб: send_daily_digest() — вызывается из lifespan раз в 24 ч.
"""
import smtplib
import ssl
import asyncio
import logging
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from app.db.database import get_db, SessionLocal
from app.models.models import User, Load, RouteSubscription
from app.core.config import settings

router = APIRouter()
log = logging.getLogger("email_digest")

# ── Auth helper ────────────────────────────────────────────────────────────
def _auth(authorization: str | None, db: Session) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Необходима авторизация")
    token = authorization.split(" ", 1)[1]
    user = db.query(User).filter(User.api_token == token).first()
    if not user:
        raise HTTPException(401, "Недействительный токен")
    return user


# ── API ────────────────────────────────────────────────────────────────────
@router.get("/me/route-subscriptions")
def list_subscriptions(authorization: str | None = None, db: Session = Depends(get_db)):
    user = _auth(authorization, db)
    subs = db.query(RouteSubscription).filter(
        RouteSubscription.user_id == user.id,
        RouteSubscription.active == True,
    ).all()
    return [
        {"id": s.id, "from_city": s.from_city, "to_city": s.to_city,
         "email": s.email, "created_at": s.created_at.isoformat() if s.created_at else None}
        for s in subs
    ]


@router.post("/me/route-subscriptions", status_code=201)
def subscribe(
    email: str = Query(...),
    from_city: str | None = Query(None),
    to_city: str | None = Query(None),
    authorization: str | None = None,
    db: Session = Depends(get_db),
):
    user = _auth(authorization, db)

    # Limit: 5 active subscriptions per user
    count = db.query(RouteSubscription).filter(
        RouteSubscription.user_id == user.id,
        RouteSubscription.active == True,
    ).count()
    if count >= 5:
        raise HTTPException(400, "Максимум 5 подписок. Удалите старые.")

    # No duplicates
    exists = db.query(RouteSubscription).filter(
        RouteSubscription.user_id == user.id,
        RouteSubscription.email == email,
        RouteSubscription.from_city == from_city,
        RouteSubscription.to_city == to_city,
        RouteSubscription.active == True,
    ).first()
    if exists:
        raise HTTPException(409, "Такая подписка уже есть")

    sub = RouteSubscription(
        user_id=user.id, email=email,
        from_city=from_city or None, to_city=to_city or None,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"ok": True, "id": sub.id, "message": "Подписка создана — дайджест приходит раз в сутки"}


@router.delete("/me/route-subscriptions/{sub_id}")
def unsubscribe(sub_id: int, authorization: str | None = None, db: Session = Depends(get_db)):
    user = _auth(authorization, db)
    sub = db.query(RouteSubscription).filter(
        RouteSubscription.id == sub_id,
        RouteSubscription.user_id == user.id,
    ).first()
    if not sub:
        raise HTTPException(404, "Подписка не найдена")
    sub.active = False
    db.commit()
    return {"ok": True}


# ── Email sending ──────────────────────────────────────────────────────────
def _build_html(loads: list[Load], from_city: str | None, to_city: str | None) -> str:
    route_label = ""
    if from_city and to_city:
        route_label = f"{from_city} → {to_city}"
    elif from_city:
        route_label = f"из {from_city}"
    elif to_city:
        route_label = f"в {to_city}"
    else:
        route_label = "все маршруты"

    load_rows = ""
    for l in loads[:10]:
        price = f"{int(l.total_price):,} ₽".replace(",", " ") if l.total_price else "Договорная"
        weight = f" · {l.weight} т" if l.weight else ""
        date = l.loading_date.strftime("%-d %b") if l.loading_date else ""
        load_rows += f"""
        <tr>
          <td style="padding:10px 8px;border-bottom:1px solid #f0f0f0">
            <b>{l.from_city} → {l.to_city}</b><br>
            <span style="font-size:12px;color:#6b7280">{date}{weight} · {l.required_body_type or 'Тип не указан'}</span>
          </td>
          <td style="padding:10px 8px;border-bottom:1px solid #f0f0f0;text-align:right;font-weight:700;color:#1e3a8a">{price}</td>
        </tr>"""

    count = len(loads)
    more = f"<p style='color:#6b7280;font-size:13px'>...и ещё {count - 10} грузов на сайте</p>" if count > 10 else ""

    return f"""
<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;margin:0;padding:20px">
<div style="max-width:580px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)">
  <div style="background:#1e3a8a;padding:28px 32px;color:#fff">
    <div style="font-size:22px;font-weight:800">ГрузПоток</div>
    <div style="opacity:.8;font-size:14px;margin-top:4px">Биржа грузоперевозок</div>
  </div>
  <div style="padding:28px 32px">
    <h2 style="margin:0 0 6px;font-size:18px;color:#111">Новые грузы: {route_label}</h2>
    <p style="color:#6b7280;font-size:14px;margin:0 0 20px">За последние 24 часа добавлено {count} {'груз' if count==1 else 'грузов'}</p>
    <table width="100%" cellspacing="0" style="border-collapse:collapse">{load_rows}</table>
    {more}
    <div style="margin-top:24px;text-align:center">
      <a href="https://gruzpotok.ru" style="display:inline-block;background:#f97316;color:#fff;padding:12px 32px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px">
        Смотреть все грузы →
      </a>
    </div>
  </div>
  <div style="background:#f8fafc;padding:16px 32px;font-size:12px;color:#9ca3af;text-align:center">
    Вы получили это письмо, потому что подписались на уведомления ГрузПоток.<br>
    <a href="https://gruzpotok.ru" style="color:#6b7280">Отписаться</a> можно в настройках профиля.
  </div>
</div>
</body></html>"""


def _send_email(to: str, subject: str, html: str) -> bool:
    host = settings.SMTP_HOST
    if not host:
        log.warning("SMTP_HOST not configured, skipping email")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to
        msg.attach(MIMEText(html, "html", "utf-8"))

        if settings.SMTP_SSL:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, settings.SMTP_PORT, context=ctx, timeout=15) as s:
                s.login(settings.SMTP_USER, settings.SMTP_PASS)
                s.sendmail(settings.SMTP_FROM, [to], msg.as_bytes())
        else:
            with smtplib.SMTP(host, settings.SMTP_PORT, timeout=15) as s:
                s.starttls()
                s.login(settings.SMTP_USER, settings.SMTP_PASS)
                s.sendmail(settings.SMTP_FROM, [to], msg.as_bytes())
        return True
    except Exception as e:
        log.error(f"Email send error to {to}: {e}")
        return False


# ── Daily digest job ───────────────────────────────────────────────────────
def send_daily_digest() -> int:
    """Run once per day. Returns number of emails sent."""
    db = SessionLocal()
    sent = 0
    try:
        since = datetime.utcnow() - timedelta(hours=24)
        subs = db.query(RouteSubscription).filter(RouteSubscription.active == True).all()

        for sub in subs:
            # Find new loads matching this subscription
            q = db.query(Load).filter(Load.created_at >= since)
            if sub.from_city:
                q = q.filter(Load.from_city.ilike(f"%{sub.from_city}%"))
            if sub.to_city:
                q = q.filter(Load.to_city.ilike(f"%{sub.to_city}%"))
            loads = q.order_by(Load.created_at.desc()).limit(20).all()

            if not loads:
                continue

            route = ""
            if sub.from_city and sub.to_city:
                route = f"{sub.from_city} → {sub.to_city}"
            elif sub.from_city:
                route = f"из {sub.from_city}"
            elif sub.to_city:
                route = f"в {sub.to_city}"
            else:
                route = "все маршруты"

            subject = f"ГрузПоток: {len(loads)} новых грузов — {route}"
            html = _build_html(loads, sub.from_city, sub.to_city)

            if _send_email(sub.email, subject, html):
                sub.last_sent = datetime.utcnow()
                sent += 1

        db.commit()
        log.info(f"Daily digest: {sent} emails sent")
        return sent
    except Exception as e:
        log.error(f"Daily digest error: {e}")
        return sent
    finally:
        db.close()


# ── Background scheduler ───────────────────────────────────────────────────
async def daily_digest_scheduler():
    """Runs send_daily_digest every 24 hours."""
    await asyncio.sleep(60)  # wait 1 min after startup
    while True:
        try:
            loop = asyncio.get_event_loop()
            sent = await loop.run_in_executor(None, send_daily_digest)
            log.info(f"Digest scheduler: sent {sent} emails")
        except Exception as e:
            log.error(f"Digest scheduler error: {e}")
        await asyncio.sleep(86400)  # 24 hours
