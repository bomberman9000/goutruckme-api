import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.models import User, UserRole
from app.core.security import hash_password, verify_password, create_token, get_current_user
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse
from app.services.login_tokens import verify_login_token
from app.services.sync_warmup import get_warmup_context

router = APIRouter()


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


def _normalize_magic_redirect_path(raw_value: str | None) -> str:
    value = (raw_value or "").strip()
    if not value:
        return "/dashboard"
    if not value.startswith("/"):
        return "/dashboard"
    if value.startswith("//"):
        return "/dashboard"
    return value


@router.post("/register", response_model=dict)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """Регистрация нового пользователя по ИНН (ИП или ООО)"""
    # Проверка на существующий телефон
    exists_phone = db.query(User).filter(User.phone == data.phone).first()
    if exists_phone:
        raise HTTPException(status_code=400, detail="Телефон уже зарегистрирован")
    
    # Проверка на существующий ИНН
    exists_inn = db.query(User).filter(User.inn == data.inn).first()
    if exists_inn:
        raise HTTPException(status_code=400, detail="ИНН уже зарегистрирован")

    new_user = User(
        organization_type=data.organization_type,
        inn=data.inn,
        organization_name=data.organization_name,
        phone=data.phone,
        password_hash=hash_password(data.password),
        role=data.role,
        # Банковские реквизиты
        bank_name=data.bank_name,
        bank_account=data.bank_account,
        bank_bik=data.bank_bik,
        bank_ks=data.bank_ks,
        # Обратная совместимость
        fullname=data.fullname or data.organization_name,
        company=data.company or data.organization_name,
        # Статус подтверждения (будет подтвержден после оплаты)
        payment_confirmed=False,
        verified=False
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "msg": "registered",
        "user_id": new_user.id,
        "organization_name": new_user.organization_name,
        "inn": new_user.inn,
        "payment_confirmed": new_user.payment_confirmed,
        "message": "Регистрация успешна. Подтвердите аккаунт оплатой для полного доступа."
    }


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    """Вход в систему"""
    user = db.query(User).filter(User.phone == data.phone).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный телефон или пароль")

    # Используем organization_name или fullname для токена
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
def confirm_payment(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Подтверждение оплаты и активация аккаунта (только администратор).
    """
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Только для администраторов")

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
    
    return {
        "msg": "payment_confirmed",
        "user_id": user_to_confirm.id,
        "organization_name": user_to_confirm.organization_name,
        "payment_confirmed": True,
        "verified": True,
        "message": "Аккаунт подтвержден! Полный доступ активирован."
    }


def _build_autologin_response(
    *,
    access_token: str,
    redirect_path: str,
    search_id: str | None,
    warmup_payload: dict | None,
) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Вход в ГрузПоток</title>
</head>
<body>
<script>
  (function () {{
    try {{
      localStorage.setItem("authToken", {json.dumps(access_token)});
      if ({json.dumps(search_id)} !== null) {{
        localStorage.setItem("gruzpotok_search_id", String({json.dumps(search_id)}));
      }}
      if ({json.dumps(warmup_payload)} !== null) {{
        localStorage.setItem("gruzpotok_search_warmup", JSON.stringify({json.dumps(warmup_payload)}));
      }}
    }} catch (e) {{}}
    window.location.replace({json.dumps(redirect_path)});
  }})();
</script>
Выполняем вход...
</body>
</html>"""
    response = HTMLResponse(content=html, headers={"Cache-Control": "no-store, max-age=0"})
    response.set_cookie(
        key="auth_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        max_age=86400,
        path="/",
    )
    return response


@router.get("/telegram-autologin", response_class=HTMLResponse)
@router.get("/magic", response_class=HTMLResponse)
def telegram_autologin(
    token: str = Query(..., min_length=16),
    db: Session = Depends(get_db),
):
    """
    Автологин из Telegram magic link:
    - верифицирует временный login token из internal слоя
    - ставит JWT cookie
    - редиректит на dashboard.
    """
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
            warmup_payload = {
                "search_id": cached.get("search_id"),
                "from_city": cached.get("from_city"),
                "to_city": cached.get("to_city"),
                "query": cached.get("query"),
                "recommendations": cached.get("recommendations") or [],
            }

    return _build_autologin_response(
        access_token=access_token,
        redirect_path=redirect_path,
        search_id=search_id,
        warmup_payload=warmup_payload,
    )
