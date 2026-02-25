"""
Сервис отправки событий в Telegram-бот по внутреннему API.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)


def _resolve_internal_token() -> str:
    return (settings.INTERNAL_TOKEN or "").strip()


def _resolve_event_url() -> str:
    base = (settings.TG_BOT_URL or settings.TG_BOT_INTERNAL_URL or "").rstrip("/")
    path = settings.TG_BOT_INTERNAL_EVENT_PATH or "/internal/event"
    if not path.startswith("/"):
        path = f"/{path}"
    if not base:
        return ""
    return f"{base}{path}"


def _build_headers() -> Dict[str, str]:
    token = _resolve_internal_token()
    return {"X-Internal-Token": token} if token else {}


def _build_payload(event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_type": event_type,
        "source": "gruzpotok-api",
        "data": data,
    }


async def send_event_to_bot(event_type: str, data: Dict[str, Any]) -> bool:
    """Асинхронная отправка события в tg-bot."""
    event_url = _resolve_event_url()
    if not event_url:
        logger.warning("TG_BOT_INTERNAL_URL is empty, skip event=%s", event_type)
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                event_url,
                json=_build_payload(event_type, data),
                headers=_build_headers(),
            )
            response.raise_for_status()
        logger.info("Webhook sent: %s", event_type)
        return True
    except Exception as exc:
        logger.error("Failed to send webhook (%s): %s", event_type, exc)
        return False


def send_event_to_bot_sync(event_type: str, data: Dict[str, Any]) -> bool:
    """Синхронная отправка события (для sync-endpoint'ов)."""
    event_url = _resolve_event_url()
    if not event_url:
        logger.warning("TG_BOT_INTERNAL_URL is empty, skip event=%s", event_type)
        return False

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                event_url,
                json=_build_payload(event_type, data),
                headers=_build_headers(),
            )
            response.raise_for_status()
        logger.info("Webhook sent(sync): %s", event_type)
        return True
    except Exception as exc:
        logger.error("Failed to send webhook sync (%s): %s", event_type, exc)
        return False


async def notify_carrier_selected(deal_id: int, carrier_telegram_id: int):
    """Уведомить о выборе перевозчика."""
    await send_event_to_bot("carrier_selected", {
        "deal_id": deal_id,
        "telegram_id": carrier_telegram_id,
    })


async def notify_application_sent(application_id: int, recipient_telegram_id: int):
    """Уведомить об отправке заявки."""
    await send_event_to_bot("application_sent", {
        "application_id": application_id,
        "telegram_id": recipient_telegram_id,
    })


async def notify_application_signed(application_id: int, deal_id: int, signer_telegram_id: int):
    """Уведомить о подписании заявки."""
    await send_event_to_bot("application_signed", {
        "application_id": application_id,
        "deal_id": deal_id,
        "telegram_id": signer_telegram_id,
    })


async def notify_deal_contracted(deal_id: int, shipper_telegram_id: int, carrier_telegram_id: int):
    """Уведомить о заключении договора."""
    await send_event_to_bot("deal_contracted", {
        "deal_id": deal_id,
        "shipper_telegram_id": shipper_telegram_id,
        "carrier_telegram_id": carrier_telegram_id,
    })
