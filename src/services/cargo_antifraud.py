"""Background AI antifraud check for newly created cargos."""
from __future__ import annotations

import json
import logging
from typing import Any

from src.core.config import settings
from src.services.ai_kimi import kimi_service

logger = logging.getLogger(__name__)

_REDIS_KEY = "antifraud:cargo:{cargo_id}"
_REDIS_TTL = 7 * 24 * 3600  # 7 days

RISK_ADMIN_THRESHOLD = 70   # alert admin
RISK_HIGH_THRESHOLD = 85    # also tell the user


async def run_antifraud_check(
    cargo_id: int,
    cargo_text: str,
    owner_user_id: int,
) -> dict[str, Any]:
    """
    Run Kimi antifraud on a newly created cargo.
    Stores result in Redis; notifies admin if risky.
    Call as a background task — never awaited inline.
    """
    try:
        result = await kimi_service.antifraud_mode(cargo_text)
        result["cargo_id"] = cargo_id

        await _store_result(cargo_id, result)

        risk_score = int(result.get("risk_score") or 0)
        recommendation = result.get("recommendation", "accept")

        logger.info(
            "antifraud.check cargo_id=%d risk=%d rec=%s owner=%d",
            cargo_id, risk_score, recommendation, owner_user_id,
        )

        if risk_score >= RISK_ADMIN_THRESHOLD:
            await _notify_admin(cargo_id, owner_user_id, result)

        return result

    except Exception as e:
        logger.error("antifraud.check.error cargo_id=%d error=%s", cargo_id, e)
        return {"error": str(e), "cargo_id": cargo_id}


async def get_antifraud_result(cargo_id: int) -> dict[str, Any] | None:
    """Retrieve cached antifraud result for a cargo (None if not checked yet)."""
    try:
        from src.core.redis import get_redis
        redis = await get_redis()
        data = await redis.get(_REDIS_KEY.format(cargo_id=cargo_id))
        if data:
            return json.loads(data)
    except Exception as e:
        logger.warning("antifraud.get error cargo_id=%d error=%s", cargo_id, e)
    return None


async def _store_result(cargo_id: int, result: dict[str, Any]) -> None:
    try:
        from src.core.redis import get_redis
        redis = await get_redis()
        await redis.set(
            _REDIS_KEY.format(cargo_id=cargo_id),
            json.dumps(result, ensure_ascii=False),
            ex=_REDIS_TTL,
        )
    except Exception as e:
        logger.warning("antifraud.store error cargo_id=%d error=%s", cargo_id, e)


async def _notify_admin(
    cargo_id: int,
    owner_user_id: int,
    result: dict[str, Any],
) -> None:
    if not settings.admin_id:
        logger.info("antifraud.notify_admin skipped: admin_id not set")
        return
    try:
        from src.bot.bot import bot

        risk_score = int(result.get("risk_score") or 0)
        recommendation = result.get("recommendation", "?")
        flags = result.get("flags") or []
        explanation = result.get("explanation") or ""

        emoji = "🔴" if risk_score >= RISK_HIGH_THRESHOLD else "🟡"
        flags_text = ", ".join(str(f) for f in flags[:5]) if flags else "—"

        text = (
            f"{emoji} <b>AI Антифрод — Груз #{cargo_id}</b>\n\n"
            f"Риск: <b>{risk_score}/100</b> | <b>{recommendation.upper()}</b>\n"
            f"Владелец: <code>{owner_user_id}</code>\n"
            f"Флаги: {flags_text}\n\n"
            f"{explanation[:400]}"
        )
        await bot.send_message(settings.admin_id, text, parse_mode="HTML")
    except Exception as e:
        logger.warning("antifraud.notify_admin error cargo_id=%d error=%s", cargo_id, e)
