"""
Упрощенный API для Telegram бота (телефон + пароль, только JWT).
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import create_access_token, get_current_user, verify_password
from app.db.database import get_db
from app.models.models import User
from app.services.telegram_link import confirm_link as confirm_telegram_link

router = APIRouter()


class LinkRequest(BaseModel):
    phone: str
    password: str
    telegram_id: int
    telegram_username: Optional[str] = None


class ConfirmLinkRequest(BaseModel):
    code: str
    telegram_id: int
    telegram_username: Optional[str] = None


@router.post("/link")
async def link_telegram(data: LinkRequest, db: Session = Depends(get_db)):
    """Привязать Telegram (телефон + пароль)."""
    user = db.query(User).filter(User.phone == data.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный пароль")

    existing = db.query(User).filter(
        User.telegram_id == data.telegram_id,
        User.id != user.id
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Telegram уже привязан к другому аккаунту"
        )

    user.telegram_id = data.telegram_id
    user.telegram_username = data.telegram_username
    db.commit()

    token = create_access_token(data={"sub": str(user.id)})

    return {
        "success": True,
        "user_id": user.id,
        "access_token": token,
        "message": f"Добро пожаловать в ГрузПоток, {user.organization_name or user.fullname}!"
    }


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Моя информация."""
    return {
        "id": current_user.id,
        "name": current_user.organization_name or current_user.fullname,
        "role": current_user.role
    }


@router.post("/confirm-link")
async def confirm_link_route(data: ConfirmLinkRequest, db: Session = Depends(get_db)):
    """Подтвердить привязку Telegram по одноразовому коду."""
    user = confirm_telegram_link(
        db=db,
        code=data.code,
        telegram_id=data.telegram_id,
        telegram_username=data.telegram_username or "",
    )
    token = create_access_token(data={"sub": str(user.id)})
    return {
        "success": True,
        "user_id": user.id,
        "access_token": token,
        "message": f"Telegram успешно привязан к {user.organization_name or user.fullname}!",
    }


@router.post("/confirm_link")
async def confirm_link_route_legacy(data: ConfirmLinkRequest, db: Session = Depends(get_db)):
    """Legacy alias for old Telegram bot clients."""
    return await confirm_link_route(data, db)
