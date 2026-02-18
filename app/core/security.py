from typing import Optional
from jose import jwt
from datetime import datetime, timedelta
import os
import secrets
import warnings
import bcrypt
from fastapi import HTTPException, Depends, Header
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User

def _resolve_secret_key() -> str:
    key = os.getenv("SECRET_KEY", "")
    if not key or key == "supersecretkey123":
        generated_secret = secrets.token_urlsafe(64)
        os.environ["SECRET_KEY"] = generated_secret
        warnings.warn(
            "⚠️ SECRET_KEY не задана (или небезопасна). "
            "Сгенерирован временный ключ процесса; укажите SECRET_KEY в .env.",
            UserWarning,
        )
        return generated_secret
    return key


# Безопасность: читаем из окружения, без предсказуемого fallback
SECRET_KEY = _resolve_secret_key()
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

def _normalize_password_bytes(password: str) -> bytes:
    """
    bcrypt учитывает только первые 72 байта пароля.
    Нормализуем это поведение явно для hash/verify.
    """
    password_bytes = password.encode("utf-8")
    return password_bytes[:72]


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        hashed_bytes = hashed.encode("utf-8") if isinstance(hashed, str) else hashed
        return bcrypt.checkpw(_normalize_password_bytes(plain), hashed_bytes)
    except Exception:
        return False


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(_normalize_password_bytes(password), salt)
    return hashed.decode("utf-8")


def create_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(data: dict):
    """JWT токен для API (в т.ч. Telegram бот). Поддерживает sub и id."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    """Зависимость: получить текущего пользователя по Bearer токену."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    token = authorization.replace("Bearer ", "").strip()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub") or payload.get("id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Невалидный токен")
        user_id = int(user_id)
    except Exception:
        raise HTTPException(status_code=401, detail="Невалидный или истёкший токен")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user
