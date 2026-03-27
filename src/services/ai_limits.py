"""Daily AI request rate limiting and history logging (Redis-backed)."""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

FREE_DAILY_LIMIT = 5
_HISTORY_KEY = "ai_history:{user_id}"
_HISTORY_MAX = 20  # keep last N requests per user
_HISTORY_TTL = 30 * 24 * 3600  # 30 days


def _key(user_id: int) -> str:
    return f"ai_limit:{user_id}:{date.today().isoformat()}"


def _ttl_until_midnight() -> int:
    now = datetime.utcnow()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((midnight - now).total_seconds()))


async def check_and_increment(user_id: int, *, is_premium: bool = False) -> tuple[bool, int]:
    """
    Check the user's daily AI limit and increment the counter.

    Returns:
        (allowed, remaining) — allowed=True means the request can proceed.
    """
    if is_premium:
        return True, FREE_DAILY_LIMIT  # unlimited, show full bar

    try:
        from src.core.redis import get_redis
        redis = await get_redis()
        key = _key(user_id)

        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, _ttl_until_midnight())

        allowed = count <= FREE_DAILY_LIMIT
        remaining = max(0, FREE_DAILY_LIMIT - count)
        return allowed, remaining

    except Exception as e:
        logger.warning("ai_limits.check error user_id=%d error=%s", user_id, e)
        return True, FREE_DAILY_LIMIT  # fail-open


async def get_remaining(user_id: int) -> int:
    """Return how many AI requests remain today (without incrementing)."""
    try:
        from src.core.redis import get_redis
        redis = await get_redis()
        count = int(await redis.get(_key(user_id)) or 0)
        return max(0, FREE_DAILY_LIMIT - count)
    except Exception as e:
        logger.warning("ai_limits.get_remaining error user_id=%d error=%s", user_id, e)
        return FREE_DAILY_LIMIT


async def log_ai_request(
    user_id: int,
    mode: str,
    prompt: str,
    result: dict[str, Any],
) -> None:
    """Append a request to user's AI history in Redis (capped at _HISTORY_MAX)."""
    try:
        from src.core.redis import get_redis
        redis = await get_redis()
        entry = json.dumps(
            {
                "ts": int(time.time()),
                "mode": mode,
                "prompt": prompt[:200],
                "ok": "error" not in result,
            },
            ensure_ascii=False,
        )
        key = _HISTORY_KEY.format(user_id=user_id)
        await redis.lpush(key, entry)
        await redis.ltrim(key, 0, _HISTORY_MAX - 1)
        await redis.expire(key, _HISTORY_TTL)
    except Exception as e:
        logger.warning("ai_limits.log_request error user_id=%d error=%s", user_id, e)


async def get_ai_history(user_id: int) -> list[dict[str, Any]]:
    """Return last _HISTORY_MAX AI requests for the user."""
    try:
        from src.core.redis import get_redis
        redis = await get_redis()
        raw = await redis.lrange(_HISTORY_KEY.format(user_id=user_id), 0, _HISTORY_MAX - 1)
        return [json.loads(r) for r in raw]
    except Exception as e:
        logger.warning("ai_limits.get_history error user_id=%d error=%s", user_id, e)
        return []


async def is_premium_user(user_id: int) -> bool:
    """Quick DB check: is the user on an active premium plan."""
    try:
        from src.core.database import async_session
        from src.core.models import User
        from sqlalchemy import select

        async with async_session() as session:
            user = await session.scalar(select(User).where(User.id == user_id))
            if user is None:
                return False
            if user.is_premium and user.premium_until and user.premium_until > datetime.utcnow():
                return True
    except Exception as e:
        logger.warning("ai_limits.is_premium error user_id=%d error=%s", user_id, e)
    return False
