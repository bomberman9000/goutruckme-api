from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)


def _bot_base_url() -> str:
    return (settings.TG_BOT_URL or settings.TG_BOT_INTERNAL_URL or "").rstrip("/")


def _headers() -> dict[str, str]:
    token = (settings.INTERNAL_TOKEN or "").strip()
    if not token:
        return {}
    return {"X-Internal-Token": token}


async def notify_user_on_bot(
    *,
    user_id: int,
    message: str,
    action_link: str | None = None,
    action_text: str = "Открыть",
) -> bool:
    """Отправить пользователю Telegram push через tg-bot /internal/notify-user."""
    base = _bot_base_url()
    if not base:
        logger.warning("TG_BOT_URL is empty, skip notify")
        return False

    payload: dict[str, Any] = {
        "user_id": int(user_id),
        "message": message,
        "action_text": action_text,
    }
    if action_link:
        payload["action_link"] = action_link

    url = f"{base}/internal/notify-user"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=_headers())
            response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("notify_user_on_bot failed user_id=%s error=%s", user_id, str(exc)[:200])
        return False


def notify_user_on_bot_sync(
    *,
    user_id: int,
    message: str,
    action_link: str | None = None,
    action_text: str = "Открыть",
) -> bool:
    """Синхронная версия для sync endpoint'ов."""
    base = _bot_base_url()
    if not base:
        logger.warning("TG_BOT_URL is empty, skip notify")
        return False

    payload: dict[str, Any] = {
        "user_id": int(user_id),
        "message": message,
        "action_text": action_text,
    }
    if action_link:
        payload["action_link"] = action_link

    url = f"{base}/internal/notify-user"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload, headers=_headers())
            response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("notify_user_on_bot_sync failed user_id=%s error=%s", user_id, str(exc)[:200])
        return False
