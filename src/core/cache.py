"""Lightweight Redis-backed response cache for API endpoints."""

from __future__ import annotations

import hashlib
import json
import logging

from src.core.redis import get_redis

logger = logging.getLogger(__name__)

FEED_CACHE_TTL = 45


def _cache_key(prefix: str, params: dict) -> str:
    raw = json.dumps(params, sort_keys=True, default=str)
    digest = hashlib.md5(raw.encode()).hexdigest()
    return f"cache:{prefix}:{digest}"


async def get_cached(prefix: str, params: dict) -> str | None:
    try:
        redis = await get_redis()
        key = _cache_key(prefix, params)
        return await redis.get(key)
    except Exception as exc:
        logger.debug("cache get failed: %s", exc)
        return None


async def set_cached(prefix: str, params: dict, value: str, ttl: int = FEED_CACHE_TTL) -> None:
    try:
        redis = await get_redis()
        key = _cache_key(prefix, params)
        await redis.set(key, value, ex=ttl)
    except Exception as exc:
        logger.debug("cache set failed: %s", exc)
