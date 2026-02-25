from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from src.core.config import settings
from src.webapp.auth import validate_init_data


@dataclass(slots=True)
class TelegramTMAUser:
    user_id: int
    raw: dict


def _extract_tma_token(authorization: str | None) -> str | None:
    raw = (authorization or "").strip()
    if not raw:
        return None
    parts = raw.split(" ", maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "tma":
        raise HTTPException(status_code=401, detail="Invalid authorization scheme")
    token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing tma payload")
    return token


def _parse_user(init_data: str) -> TelegramTMAUser:
    user = validate_init_data(init_data, max_age=settings.telegram_tma_max_age_sec)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid Telegram initData")
    user_id = user.get("id")
    if not isinstance(user_id, int) or user_id <= 0:
        raise HTTPException(status_code=401, detail="Invalid Telegram user payload")
    return TelegramTMAUser(user_id=user_id, raw=user)


async def get_optional_tma_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> TelegramTMAUser | None:
    init_data = _extract_tma_token(authorization)
    if not init_data:
        return None
    return _parse_user(init_data)


async def get_required_tma_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> TelegramTMAUser:
    init_data = _extract_tma_token(authorization)
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing Authorization")
    return _parse_user(init_data)
