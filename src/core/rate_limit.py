"""Redis-backed API rate limiter.

Limits requests per IP or user_id using a sliding window counter
stored in Redis.  Returns HTTP 429 when the limit is exceeded.
"""

from __future__ import annotations

import logging
import os

import aiohttp
from fastapi import Request, HTTPException

from src.core.redis import get_redis

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 60
DEFAULT_WINDOW_SEC = 60

# Login brute-force thresholds
_LOGIN_SOFT_LIMIT = 5    # → 429
_LOGIN_HARD_LIMIT = 10   # → 15-min ban + alert
_LOGIN_WINDOW_SEC = 60
_LOGIN_BAN_SEC    = 900  # 15 minutes


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")


def _client_key(request: Request) -> str:
    return f"ratelimit:{_client_ip(request)}"


async def _send_admin_alert(text: str) -> None:
    token = os.getenv("ADMIN_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
    chat_id = os.getenv("ADMIN_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=aiohttp.ClientTimeout(total=5),
            )
    except Exception:
        pass


async def check_login_rate_limit(request: Request, username: str = "") -> None:
    """Brute-force protection for /admin/login.

    Counters per IP and per username:
      ≤5 attempts/min  → allowed
      >5 attempts/min  → 429
      >10 attempts/min → 15-min Redis ban + Telegram alert
    """
    try:
        redis = await get_redis()
        ip = _client_ip(request)

        ban_key = f"login:ban:{ip}"
        if await redis.exists(ban_key):
            ttl = await redis.ttl(ban_key)
            raise HTTPException(
                status_code=429,
                detail=f"IP заблокирован на {ttl}с за множественные попытки входа.",
                headers={"Retry-After": str(max(1, ttl))},
            )

        ip_key   = f"login:ip:{ip}"
        user_key = f"login:user:{username}" if username else None

        ip_count = await redis.incr(ip_key)
        if ip_count == 1:
            await redis.expire(ip_key, _LOGIN_WINDOW_SEC)

        if user_key:
            u_count = await redis.incr(user_key)
            if u_count == 1:
                await redis.expire(user_key, _LOGIN_WINDOW_SEC)
        else:
            u_count = 0

        count = max(ip_count, u_count)

        if count > _LOGIN_HARD_LIMIT:
            await redis.setex(ban_key, _LOGIN_BAN_SEC, "1")
            await redis.delete(ip_key)
            logger.warning("login.brute_force.banned ip=%s user=%s", ip, username)
            await _send_admin_alert(
                f"🚨 <b>Brute-force атака!</b>\n"
                f"IP: <code>{ip}</code>\n"
                f"Username: <code>{username or '—'}</code>\n"
                f"Попыток: {count}\n"
                f"Заблокирован на 15 минут."
            )
            raise HTTPException(
                status_code=429,
                detail="Слишком много попыток. IP заблокирован на 15 минут.",
                headers={"Retry-After": str(_LOGIN_BAN_SEC)},
            )

        if count > _LOGIN_SOFT_LIMIT:
            ttl = await redis.ttl(ip_key)
            logger.warning("login.rate_limit ip=%s count=%s", ip, count)
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много попыток входа. Подождите {ttl}с.",
                headers={"Retry-After": str(max(1, ttl))},
            )

    except HTTPException:
        raise
    except Exception as exc:
        logger.debug("login_rate_limit check failed (allowing): %s", exc)


async def check_rate_limit(
    request: Request,
    *,
    limit: int = DEFAULT_LIMIT,
    window_sec: int = DEFAULT_WINDOW_SEC,
) -> None:
    """Raise HTTP 429 if client exceeds the rate limit.

    Call this at the top of any endpoint that needs protection.
    Uses a simple Redis INCR + EXPIRE sliding counter.
    """
    try:
        redis = await get_redis()
        key = _client_key(request)
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_sec)
        ttl = await redis.ttl(key)

        request.state.rate_limit_remaining = max(0, limit - count)
        request.state.rate_limit_reset = ttl

        if count > limit:
            logger.warning("rate_limit_exceeded key=%s count=%s", key, count)
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests. Limit: {limit}/{window_sec}s. Retry after {ttl}s.",
                headers={
                    "Retry-After": str(max(1, ttl)),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(ttl),
                },
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.debug("rate_limit check failed (allowing request): %s", exc)
