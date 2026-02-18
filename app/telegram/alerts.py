"""
Отправка уведомлений в Telegram при HIGH risk (модерация).
Использует TELEGRAM_BOT_TOKEN и ADMIN_CHAT_ID из env.
"""

import logging
from typing import Any, Dict

import httpx

logger = logging.getLogger(__name__)


def send_high_risk_alert(
    entity_type: str,
    entity_id: int,
    result: Dict[str, Any],
    entity_row: Any,
) -> None:
    """
    Отправить сообщение в ADMIN_CHAT_ID о высоком риске.
    Если ADMIN_CHAT_ID или TELEGRAM_BOT_TOKEN не заданы — ничего не делаем.
    """
    try:
        from app.core.config import get_settings
        s = get_settings()
        token = getattr(s, "TELEGRAM_BOT_TOKEN", None) or ""
        chat_id = getattr(s, "ADMIN_CHAT_ID", None) or ""
        if not token.strip() or not chat_id.strip():
            return
    except Exception as e:
        logger.warning("Config for Telegram alert: %s", e)
        return

    route_summary = ""
    if entity_type == "deal" and entity_row is not None:
        payload = getattr(entity_row, "payload", None) or {}
        cargo = payload.get("cargoSnapshot") or {}
        from_city = (cargo.get("from_city") or payload.get("from_city") or "").strip()
        to_city = (cargo.get("to_city") or payload.get("to_city") or "").strip()
        route_summary = f"{from_city} → {to_city}" if (from_city or to_city) else "—"
    elif entity_type == "document" and entity_row is not None:
        route_summary = f"doc_type={getattr(entity_row, 'doc_type', '')}"

    flags = result.get("flags") or []
    flags_str = ", ".join(flags) if isinstance(flags, list) else str(flags)
    action = result.get("recommended_action") or ""

    text = (
        f"⚠️ AI Moderation: HIGH risk\n"
        f"Entity: {entity_type} #{entity_id}\n"
        f"Route: {route_summary}\n"
        f"Flags: {flags_str}\n"
        f"Action: {action}"
    )

    url = f"https://api.telegram.org/bot{token.strip()}/sendMessage"
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                url,
                json={"chat_id": chat_id.strip(), "text": text},
            )
            if r.status_code != 200:
                logger.warning("Telegram sendMessage: %s %s", r.status_code, r.text)
    except Exception as e:
        logger.warning("Telegram alert request failed: %s", e)
