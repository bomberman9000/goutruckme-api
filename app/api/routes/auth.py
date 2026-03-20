import json
import logging
import os
import random
import re
import smtplib
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.models import User, UserRole
from app.core.config import settings
from app.core.security import hash_password, verify_password, create_token, get_current_user
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse
from app.services.login_tokens import verify_login_token
from app.services.sync_warmup import get_warmup_context

logger = logging.getLogger(__name__)
router = APIRouter()

# ── in-memory OTP store: contact → (code, expires_at) ──────────────────────
_otp_store: dict[str, tuple[str, float]] = {}
_OTP_TTL = 600  # 10 min


def _gen_otp() -> str:
    return str(random.randint(100000, 999999))


def _contact_type(contact: str) -> str:
    return "email" if re.match(r"[^@]+@[^@]+\.[^@]+", contact) else "phone"


def _sms_transport_available() -> bool:
    provider_name = (os.getenv("SMS_PROVIDER", "stub") or "stub").strip().lower()
    if provider_name != "http":
        return False
    return bool(os.getenv("SMS_HTTP_URL", "").strip() and os.getenv("SMS_HTTP_TOKEN", "").strip())


def _email_transport_available() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_USER", "").strip())


def _otp_transport_available(contact: str) -> bool:
    return _email_transport_available() if _contact_type(contact) == "email" else _sms_transport_available()


def _otp_delivery_mode(contact: str) -> str:
    ctype = _contact_type(contact)
    if ctype == "email":
        return "email" if _email_transport_available() else "stub"
    return "sms" if _sms_transport_available() else "stub"


def _send_otp(contact: str, code: str) -> None:
    ctype = _contact_type(contact)
    if ctype == "email":
        _send_email_otp(contact, code)
    else:
        _send_sms_otp(contact, code)


def _send_sms_otp(phone: str, code: str) -> None:
    try:
        from app.services.sms_provider import send_otp as sms_send
        sms_send(phone, code)
    except Exception as e:
        logger.warning("SMS OTP failed phone=%s: %s", phone, e)
        logger.info("OTP CODE (stub) phone=%s code=%s", phone, code)


def _send_email_otp(email: str, code: str) -> None:
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)
    smtp_ssl = (os.getenv("SMTP_SSL", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    smtp_timeout = float(os.getenv("SMTP_TIMEOUT_SECONDS", "5"))
    if not smtp_host or not smtp_user:
        logger.info("Email OTP (stub) email=%s code=%s", email, code)
        return
    try:
        msg = f"From: ГрузПоток <{smtp_from}>\nTo: {email}\nSubject: Код подтверждения ГрузПоток\n\nВаш код: {code}\n\nКод действует 10 минут."
        smtp_cls = smtplib.SMTP_SSL if smtp_ssl else smtplib.SMTP
        with smtp_cls(smtp_host, smtp_port, timeout=smtp_timeout) as server:
            if not smtp_ssl:
                try:
                    server.starttls()
                except Exception:
                    logger.warning("Email OTP starttls skipped/failed email=%s", email, exc_info=True)
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, email, msg.encode("utf-8"))
    except Exception as e:
        logger.warning("Email OTP send error email=%s: %s", email, e)
        logger.info("Email OTP (stub fallback) email=%s code=%s", email, code)


# ── schemas ────────────────────────────────────────────────────────────────
class OtpSendRequest(BaseModel):
    contact: str  # phone or email


class OtpVerifyRequest(BaseModel):
    contact: str
    code: str


class OtpLoginRequest(BaseModel):
    contact: str
    code: str


class LoginFlexRequest(BaseModel):
    contact: str  # phone or email
    password: str


# ── helpers ────────────────────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _normalize_role(role: str) -> str:
    raw = role.value if hasattr(role, "value") else role
    value = str(raw or "").strip().lower()
    if value.startswith("userrole."):
        value = value.split(".", 1)[1]
    if value == "shipper":
        return "client"
    if value == "expeditor":
        return "forwarder"
    if value in {"carrier", "client", "forwarder", "admin"}:
        return value
    return "forwarder"


def _normalize_public_registration_role(role: str | None) -> str:
    value = _normalize_role(role or "forwarder")
    if value not in {"carrier", "client", "forwarder"}:
        return "forwarder"
    return value


def _normalize_magic_redirect_path(raw_value: str | None) -> str:
    value = (raw_value or "").strip()
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/dashboard"
    return value


def _ensure_admin_mutations_enabled() -> None:
    if not settings.ADMIN_MUTATIONS_ENABLED:
        raise HTTPException(status_code=403, detail="Административные изменения временно отключены")


def _find_user_by_contact(db: Session, contact: str) -> User | None:
    if _contact_type(contact) == "email":
        return db.query(User).filter(User.email == contact).first()
    return db.query(User).filter(User.phone == contact).first()


# ── OTP endpoints ──────────────────────────────────────────────────────────
@router.post("/send-otp")
def send_otp(data: OtpSendRequest) -> dict:
    """Отправить OTP на телефон или email."""
    contact = data.contact.strip()
    if not contact:
        raise HTTPException(status_code=400, detail="Укажите телефон или email")
    code = _gen_otp()
    _otp_store[contact] = (code, time.time() + _OTP_TTL)
    _send_otp(contact, code)
    ctype = _contact_type(contact)
    label = "email" if ctype == "email" else "SMS"
    delivery_mode = _otp_delivery_mode(contact)
    otp_optional = delivery_mode == "stub"
    message = (
        "OTP-транспорт не настроен. Можно завершить регистрацию без кода."
        if otp_optional
        else f"Код отправлен на {label}"
    )
    return {
        "ok": True,
        "message": message,
        "contact_type": ctype,
        "delivery_mode": delivery_mode,
        "otp_optional": otp_optional,
    }


@router.post("/verify-otp")
def verify_otp(data: OtpVerifyRequest) -> dict:
    """Проверить OTP код. Возвращает verified=true."""
    contact = data.contact.strip()
    entry = _otp_store.get(contact)
    if not entry:
        raise HTTPException(status_code=400, detail="Код не найден или истёк. Запросите новый.")
    code, expires_at = entry
    if time.time() > expires_at:
        _otp_store.pop(contact, None)
        raise HTTPException(status_code=400, detail="Код истёк. Запросите новый.")
    if data.code.strip() != code:
        raise HTTPException(status_code=400, detail="Неверный код")
    # keep code until registration/login consumes it
    return {"ok": True, "verified": True}


@router.post("/login-otp", response_model=TokenResponse)
def login_otp(data: OtpLoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Вход по OTP коду (без пароля)."""
    contact = data.contact.strip()
    entry = _otp_store.get(contact)
    if not entry:
        raise HTTPException(status_code=400, detail="Код не найден или истёк")
    code, expires_at = entry
    if time.time() > expires_at or data.code.strip() != code:
        raise HTTPException(status_code=400, detail="Неверный или истёкший код")
    _otp_store.pop(contact, None)
    user = _find_user_by_contact(db, contact)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден. Сначала зарегистрируйтесь.")
    name = user.organization_name or user.fullname or user.phone
    token = create_token({"id": user.id, "phone": user.phone, "name": name})
    return TokenResponse(access_token=token)


# ── register ───────────────────────────────────────────────────────────────
@router.post("/register", response_model=dict)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """Регистрация нового пользователя. OTP должен быть подтверждён заранее."""
    # Verify OTP for the contact (phone or email)
    contact = (data.email or data.phone or "").strip()
    otp_token = getattr(data, "otp_verified_contact", None) or contact
    entry = _otp_store.get(otp_token)
    otp_ok = False
    if entry:
        _, expires_at = entry
        if time.time() <= expires_at:
            otp_ok = True
            _otp_store.pop(otp_token, None)

    if not otp_ok:
        if _otp_transport_available(contact):
            raise HTTPException(status_code=400, detail="Подтвердите телефон или email через OTP перед регистрацией")
        logger.warning(
            "Registration without OTP verification because no OTP transport is configured contact=%s",
            contact,
        )

    exists_phone = db.query(User).filter(User.phone == data.phone).first()
    if exists_phone:
        raise HTTPException(status_code=400, detail="Телефон уже зарегистрирован")
    exists_inn = db.query(User).filter(User.inn == data.inn).first()
    if exists_inn:
        raise HTTPException(status_code=400, detail="ИНН уже зарегистрирован")

    public_role = _normalize_public_registration_role(data.role)
    new_user = User(
        organization_type=data.organization_type,
        inn=data.inn,
        organization_name=data.organization_name,
        phone=data.phone,
        email=getattr(data, "email", None),
        password_hash=hash_password(data.password),
        role=public_role,
        bank_name=data.bank_name,
        bank_account=data.bank_account,
        bank_bik=data.bank_bik,
        bank_ks=data.bank_ks,
        fullname=data.fullname or data.organization_name,
        company=data.company or data.organization_name,
        payment_confirmed=False,
        verified=False,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    response_message = "Регистрация успешна. Подтвердите аккаунт оплатой для полного доступа."
    if not otp_ok:
        response_message = "Регистрация успешна. Подтверждение контакта временно пропущено."
    return {
        "msg": "registered",
        "user_id": new_user.id,
        "organization_name": new_user.organization_name,
        "inn": new_user.inn,
        "payment_confirmed": new_user.payment_confirmed,
        "message": response_message,
        "otp_skipped": not otp_ok,
    }


# ── login ──────────────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == data.phone).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный телефон или пароль")
    name = user.organization_name or user.fullname or user.phone
    token = create_token({"id": user.id, "phone": user.phone, "name": name})
    return {"access_token": token, "token_type": "bearer"}


@router.post("/login-flex", response_model=TokenResponse)
def login_flex(data: LoginFlexRequest, db: Session = Depends(get_db)):
    """Вход по телефону ИЛИ email + пароль."""
    user = _find_user_by_contact(db, data.contact.strip())
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный телефон/email или пароль")
    name = user.organization_name or user.fullname or user.phone
    token = create_token({"id": user.id, "phone": user.phone, "name": name})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=dict)
def me(current_user: User = Depends(get_current_user)):
    role_raw = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    return {
        "id": current_user.id,
        "phone": current_user.phone,
        "organization_name": current_user.organization_name or current_user.company or current_user.fullname,
        "role": _normalize_role(role_raw),
        "role_raw": role_raw,
    }


@router.post("/confirm-payment/{user_id}")
def confirm_payment(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Только для администраторов")
    _ensure_admin_mutations_enabled()
    user_to_confirm = db.query(User).filter(User.id == user_id).first()
    if not user_to_confirm:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user_to_confirm.payment_confirmed:
        return {"msg": "already_confirmed", "message": "Аккаунт уже подтвержден"}
    from datetime import datetime
    user_to_confirm.payment_confirmed = True
    user_to_confirm.payment_date = datetime.utcnow()
    user_to_confirm.verified = True
    if user_to_confirm.trust_level == "new":
        user_to_confirm.trust_level = "trusted"
    db.commit()
    db.refresh(user_to_confirm)
    return {"msg": "payment_confirmed", "user_id": user_to_confirm.id, "organization_name": user_to_confirm.organization_name, "payment_confirmed": True, "verified": True, "message": "Аккаунт подтвержден!"}


def _build_autologin_response(*, access_token, redirect_path, search_id, warmup_payload):
    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Вход</title></head>
<body><script>
(function(){{
  try{{localStorage.setItem("authToken",{json.dumps(access_token)});
  if({json.dumps(search_id)}!==null)localStorage.setItem("gruzpotok_search_id",String({json.dumps(search_id)}));
  if({json.dumps(warmup_payload)}!==null)localStorage.setItem("gruzpotok_search_warmup",JSON.stringify({json.dumps(warmup_payload)}));
  }}catch(e){{}}
  window.location.replace({json.dumps(redirect_path)});
}})();
</script>Выполняем вход...</body></html>"""
    response = HTMLResponse(content=html, headers={"Cache-Control": "no-store, max-age=0"})
    response.set_cookie(key="auth_token", value=access_token, httponly=True, samesite="lax", max_age=86400, path="/")
    return response


@router.get("/telegram-autologin", response_class=HTMLResponse)
@router.get("/magic", response_class=HTMLResponse)
def telegram_autologin(token: str = Query(..., min_length=16), db: Session = Depends(get_db)):
    payload = verify_login_token(token, consume=True)
    if not payload:
        raise HTTPException(status_code=401, detail="Невалидный или просроченный токен")
    user = db.query(User).filter(User.id == int(payload.user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.telegram_id and int(user.telegram_id) != int(payload.telegram_user_id):
        raise HTTPException(status_code=409, detail="Telegram аккаунт уже привязан к другому пользователю")
    if not user.telegram_id:
        user.telegram_id = int(payload.telegram_user_id)
        db.commit()
    name = user.organization_name or user.fullname or user.phone
    access_token = create_token({"id": int(user.id), "phone": user.phone, "name": name})
    redirect_path = _normalize_magic_redirect_path(payload.redirect_path)
    search_id = payload.search_id
    warmup_payload = None
    if search_id:
        cached = get_warmup_context(str(search_id))
        if cached:
            warmup_payload = {"search_id": cached.get("search_id"), "from_city": cached.get("from_city"), "to_city": cached.get("to_city"), "query": cached.get("query"), "recommendations": cached.get("recommendations") or []}
    return _build_autologin_response(access_token=access_token, redirect_path=redirect_path, search_id=search_id, warmup_payload=warmup_payload)
