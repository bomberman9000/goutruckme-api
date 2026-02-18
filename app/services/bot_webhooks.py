"""
Сервис отправки webhooks в Telegram бота.
"""
import asyncio
import logging
import os
from typing import Any, Dict

import httpx

logger = logging.getLogger(__name__)

BOT_WEBHOOK_URL = os.getenv("BOT_WEBHOOK_URL", "http://localhost:8081")
INTERNAL_TOKEN = os.getenv("INTERNAL_WEBHOOK_TOKEN", "change-me-in-production")


async def send_event_to_bot(event_type: str, data: Dict[str, Any]):
    """
    Отправить событие в бота.

    Args:
        event_type: Тип события
        data: Данные события
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{BOT_WEBHOOK_URL}/internal/event",
                json={
                    "event_type": event_type,
                    "data": data
                },
                headers={
                    "X-Internal-Token": INTERNAL_TOKEN
                }
            )
            response.raise_for_status()
            logger.info("Webhook sent: %s", event_type)
    except Exception as e:
        logger.error("Failed to send webhook: %s", e)


async def notify_carrier_selected(deal_id: int, carrier_telegram_id: int):
    """Уведомить о выборе перевозчика."""
    await send_event_to_bot("carrier_selected", {
        "deal_id": deal_id,
        "telegram_id": carrier_telegram_id
    })


async def notify_application_sent(application_id: int, recipient_telegram_id: int):
    """Уведомить об отправке заявки."""
    await send_event_to_bot("application_sent", {
        "application_id": application_id,
        "telegram_id": recipient_telegram_id
    })


async def notify_application_signed(application_id: int, deal_id: int, signer_telegram_id: int):
    """Уведомить о подписании заявки."""
    await send_event_to_bot("application_signed", {
        "application_id": application_id,
        "deal_id": deal_id,
        "telegram_id": signer_telegram_id
    })


async def notify_deal_contracted(deal_id: int, shipper_telegram_id: int, carrier_telegram_id: int):
    """Уведомить о заключении договора."""
    await send_event_to_bot("deal_contracted", {
        "deal_id": deal_id,
        "shipper_telegram_id": shipper_telegram_id,
        "carrier_telegram_id": carrier_telegram_id
    })
