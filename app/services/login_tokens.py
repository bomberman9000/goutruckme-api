from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Any
from uuid import uuid4


@dataclass
class LoginTokenData:
    token: str
    user_id: int
    telegram_user_id: int
    search_id: str | None
    redirect_path: str
    expires_at: datetime


_LOCK = Lock()
_CACHE: dict[str, LoginTokenData] = {}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _cleanup_locked(now: datetime) -> None:
    expired = [token for token, payload in _CACHE.items() if payload.expires_at <= now]
    for token in expired:
        _CACHE.pop(token, None)


def create_login_token(
    *,
    user_id: int,
    telegram_user_id: int,
    search_id: str | None = None,
    redirect_path: str = "/",
    ttl_seconds: int = 300,
) -> LoginTokenData:
    now = _utcnow()
    ttl = max(30, int(ttl_seconds))
    token = str(uuid4())
    payload = LoginTokenData(
        token=token,
        user_id=int(user_id),
        telegram_user_id=int(telegram_user_id),
        search_id=search_id,
        redirect_path=redirect_path,
        expires_at=now + timedelta(seconds=ttl),
    )

    with _LOCK:
        _cleanup_locked(now)
        _CACHE[token] = payload

    return payload


def verify_login_token(
    token: str,
    *,
    consume: bool = False,
) -> LoginTokenData | None:
    now = _utcnow()
    with _LOCK:
        _cleanup_locked(now)
        payload = _CACHE.get(token)
        if not payload:
            return None
        if payload.expires_at <= now:
            _CACHE.pop(token, None)
            return None
        if consume:
            _CACHE.pop(token, None)
    return payload


def login_tokens_size() -> int:
    with _LOCK:
        _cleanup_locked(_utcnow())
        return len(_CACHE)


def dump_login_token(token: str) -> dict[str, Any] | None:
    payload = verify_login_token(token, consume=False)
    if not payload:
        return None
    return {
        "token": payload.token,
        "user_id": payload.user_id,
        "telegram_user_id": payload.telegram_user_id,
        "search_id": payload.search_id,
        "redirect_path": payload.redirect_path,
        "expires_at": payload.expires_at.isoformat(),
    }
