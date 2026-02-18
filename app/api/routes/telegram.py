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

router = APIRouter()


class LinkRequest(BaseModel):
    phone: str
    password: str
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
        "message": f"Привет, {user.organization_name or user.fullname}!"
    }


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Моя информация."""
    return {
        "id": current_user.id,
        "name": current_user.organization_name or current_user.fullname,
        "role": current_user.role
    }
