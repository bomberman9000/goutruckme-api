"""Redis-backed API rate limiter.

Limits requests per IP or user_id using a sliding window counter
stored in Redis.  Returns HTTP 429 when the limit is exceeded.
"""

from __future__ import annotations

import logging

from fastapi import Request, HTTPException

from src.core.redis import get_redis

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 60
DEFAULT_WINDOW_SEC = 60


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    return f"ratelimit:{ip}"


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
