"""
Сервис привязки Telegram аккаунтов.
"""
import secrets
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.models import User, TelegramLinkCode


def generate_link_code(db: Session, user_id: int) -> str:
    """
    Сгенерировать одноразовый код для привязки Telegram.

    Args:
        db: Сессия БД
        user_id: ID пользователя

    Returns:
        str: Код привязки
    """
    code = secrets.token_urlsafe(16)
    expires_at = datetime.utcnow() + timedelta(minutes=10)

    link_code = TelegramLinkCode(
        code=code,
        user_id=user_id,
        expires_at=expires_at
    )
    db.add(link_code)
    db.commit()

    return code


def confirm_link(
    db: Session,
    code: str,
    telegram_id: int,
    telegram_username: str
) -> User:
    """
    Подтвердить привязку Telegram по коду.

    Args:
        db: Сессия БД
        code: Код привязки
        telegram_id: Telegram ID
        telegram_username: Telegram username

    Returns:
        User: Пользователь

    Raises:
        HTTPException: Если код невалиден
    """
    link_code = db.query(TelegramLinkCode).filter(
        TelegramLinkCode.code == code,
        TelegramLinkCode.used_at.is_(None)
    ).first()

    if not link_code:
        raise HTTPException(status_code=404, detail="Код не найден или уже использован")

    if datetime.utcnow() > link_code.expires_at:
        raise HTTPException(status_code=400, detail="Код истёк (10 минут)")

    existing = db.query(User).filter(
        User.telegram_id == telegram_id
    ).first()

    if existing and existing.id != link_code.user_id:
        raise HTTPException(
            status_code=400,
            detail="Этот Telegram аккаунт уже привязан к другому пользователю"
        )

    user = db.query(User).filter(User.id == link_code.user_id).first()
    user.telegram_id = telegram_id
    user.telegram_username = telegram_username
    user.telegram_linked_at = datetime.utcnow()

    link_code.used_at = datetime.utcnow()

    db.commit()
    db.refresh(user)

    return user


def get_user_by_telegram_id(db: Session, telegram_id: int) -> User:
    """
    Получить пользователя по Telegram ID.

    Args:
        db: Сессия БД
        telegram_id: Telegram ID

    Returns:
        User: Пользователь

    Raises:
        HTTPException: Если не найден
    """
    user = db.query(User).filter(User.telegram_id == telegram_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    return user
