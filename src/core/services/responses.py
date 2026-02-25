"""Suggested response generator for cargo feed items.

Builds a professional reply template that a carrier can send to a
dispatcher with a single tap in TWA.  The reply text is generated at
parse time and stored alongside the feed event so the frontend can
render a «Откликнуться» button with a pre-filled message.
"""

from __future__ import annotations

from urllib.parse import quote


def build_suggested_response(
    *,
    from_city: str,
    to_city: str,
    body_type: str | None = None,
    weight_t: float | None = None,
    load_date: str | None = None,
    carrier_name: str | None = None,
    carrier_phone: str | None = None,
) -> str:
    name = carrier_name or "Перевозчик"
    text = f"Добрый день! По вашему грузу {from_city} — {to_city}"
    if body_type:
        text += f" готов предоставить {body_type}"
    if weight_t:
        text += f" ({weight_t} т)"
    text += "."
    if load_date:
        text += f" Дата: {load_date}."
    text += " Готов обсудить условия."
    if carrier_phone:
        text += f" Мой номер: {carrier_phone}."
    text += f"\n— {name}, через ГрузПоток"
    return text


def build_default_response(
    *,
    from_city: str,
    to_city: str,
    body_type: str | None = None,
    weight_t: float | None = None,
    load_date: str | None = None,
) -> str:
    """Build a generic response template (no carrier info)."""
    text = f"Добрый день! По грузу {from_city} — {to_city}"
    if body_type:
        text += f" ({body_type}"
        if weight_t:
            text += f", {weight_t} т"
        text += ")"
    text += " готов ехать."
    if load_date:
        text += f" Дата: {load_date}."
    text += " Обсудим условия?"
    return text


def build_reply_deep_link(phone: str, message: str) -> str:
    """Build a ``tel:`` or ``https://t.me/`` deep link with pre-filled text.

    For phones we return ``tel:+7...``.  A Telegram deep link would
    require the username which we generally don't have from parsed
    messages, so we stick with the phone-based link.
    """
    return f"tel:{phone}"


def build_reply_link_with_text(phone: str, message: str) -> str:
    """Build an SMS-style link with pre-filled body (works on mobile)."""
    encoded = quote(message, safe="")
    return f"sms:{phone}?body={encoded}"
