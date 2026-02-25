from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import or_, select

from src.bot.bot import bot
from src.core.config import settings
from src.core.database import async_session
from src.core.logger import logger
from src.core.models import RouteSubscription
from src.core.schemas.sync import BotInternalEvent, InternalNotifyUserRequest, SharedSyncEvent


router = APIRouter(tags=["internal"])


def _resolved_internal_tokens() -> set[str]:
    tokens = {
        (settings.internal_token or "").strip(),
        (settings.internal_api_token or "").strip(),
    }
    return {token for token in tokens if token}


def _require_internal_token(x_internal_token: str | None) -> None:
    expected_tokens = _resolved_internal_tokens()
    if not expected_tokens:
        raise HTTPException(status_code=503, detail="Internal API token is not configured")
    if not x_internal_token or x_internal_token not in expected_tokens:
        raise HTTPException(status_code=403, detail="Forbidden")


def _default_event_message(event: SharedSyncEvent) -> str:
    order = event.order
    vehicle = event.vehicle

    if event.message:
        return event.message

    if event.event_type in {"cargo.created", "order.created"} and order:
        route = " -> ".join([x for x in [order.from_city, order.to_city] if x])
        return f"📦 Новый груз #{order.id} на сайте.\nМаршрут: {route or 'не указан'}"

    if event.event_type in {"vehicle.match_found", "order.match_found"}:
        match_count = event.metadata.get("match_count")
        if isinstance(match_count, int) and match_count > 0:
            return f"🚚 Нашли {match_count} подходящих машин по вашей заявке."
        return "🚚 По вашей заявке найдено новое совпадение."

    if event.event_type in {"vehicle.created", "car.created"} and vehicle:
        route = " -> ".join([x for x in [vehicle.from_city, vehicle.to_city] if x])
        return f"🚛 Добавлен транспорт #{vehicle.id}. Маршрут: {route or 'не указан'}"

    return f"🔔 Новое событие синхронизации: {event.event_type}"


def _event_user_id(event: SharedSyncEvent) -> int | None:
    if event.user_id:
        return event.user_id
    if event.order and event.order.user_id:
        return event.order.user_id
    if event.vehicle and event.vehicle.user_id:
        return event.vehicle.user_id
    return None


async def _send_user_message(payload: InternalNotifyUserRequest) -> dict[str, Any]:
    reply_markup = None
    if payload.action_link:
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=payload.action_text,
                        url=payload.action_link,
                    )
                ]
            ]
        )

    sent = await bot.send_message(
        payload.user_id,
        payload.message,
        disable_web_page_preview=payload.disable_web_page_preview,
        reply_markup=reply_markup,
    )
    return {"ok": True, "message_id": sent.message_id}


@router.post("/internal/notify-user")
@router.post("/internal/notify")
async def internal_notify_user(
    body: InternalNotifyUserRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_internal_token(x_internal_token)
    try:
        result = await _send_user_message(body)
        logger.info("internal.notify_user ok user_id=%s", body.user_id)
        return result
    except Exception as exc:
        logger.warning("internal.notify_user failed user_id=%s error=%s", body.user_id, str(exc)[:200])
        raise HTTPException(status_code=502, detail=f"Failed to send Telegram message: {str(exc)[:200]}") from exc


def _normalize_event_type(value: str | None) -> str:
    return str(value or "").strip().lower()


def _extract_user_id(value: Any) -> int | None:
    try:
        if value is None:
            return None
        user_id = int(value)
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


def _extract_route_from_data(data: dict[str, Any]) -> tuple[str | None, str | None]:
    for key in ("route", "order", "cargo", "payload"):
        route_obj = data.get(key)
        if isinstance(route_obj, dict):
            from_city = route_obj.get("from_city")
            to_city = route_obj.get("to_city")
            if from_city or to_city:
                return (
                    str(from_city).strip() if from_city else None,
                    str(to_city).strip() if to_city else None,
                )

    from_city = data.get("from_city")
    to_city = data.get("to_city")
    return (
        str(from_city).strip() if from_city else None,
        str(to_city).strip() if to_city else None,
    )


def _build_internal_event_message(event_type: str, data: dict[str, Any]) -> str:
    route_from, route_to = _extract_route_from_data(data)
    route = " -> ".join([x for x in [route_from, route_to] if x])

    if event_type in {"cargo.created", "load.created", "order.created"}:
        load_id = data.get("load_id") or data.get("cargo_id") or data.get("id")
        if load_id:
            return f"📦 Новый груз #{load_id}.\nМаршрут: {route or 'не указан'}"
        return f"📦 Добавлен новый груз.\nМаршрут: {route or 'не указан'}"

    if event_type == "carrier_selected":
        deal_id = data.get("deal_id")
        return f"✅ Вас выбрали перевозчиком по сделке #{deal_id}." if deal_id else "✅ Вас выбрали перевозчиком."

    if event_type == "application_sent":
        app_id = data.get("application_id")
        return f"📝 Вам отправили заявку #{app_id}." if app_id else "📝 Вам отправили новую заявку."

    if event_type == "application_signed":
        app_id = data.get("application_id")
        deal_id = data.get("deal_id")
        if app_id and deal_id:
            return f"✍️ Заявка #{app_id} подписана (сделка #{deal_id})."
        return "✍️ Заявка подписана."

    if event_type == "deal_contracted":
        deal_id = data.get("deal_id")
        return f"📄 Договор по сделке #{deal_id} заключён." if deal_id else "📄 Договор заключён."

    if event_type.startswith("search."):
        query = data.get("query") or data.get("truck_type")
        if query:
            return f"🔎 Поиск синхронизирован: {query}"
        return "🔎 Поиск синхронизирован с сайтом."

    return f"🔔 Новое событие: {event_type}"


async def _resolve_event_targets(event_type: str, data: dict[str, Any]) -> list[int]:
    explicit_candidates = [
        _extract_user_id(data.get("telegram_id")),
        _extract_user_id(data.get("user_id")),
        _extract_user_id(data.get("target_user_id")),
        _extract_user_id(data.get("shipper_telegram_id")),
        _extract_user_id(data.get("carrier_telegram_id")),
    ]
    target_ids = {candidate for candidate in explicit_candidates if candidate}

    if target_ids:
        return sorted(target_ids)

    if event_type not in {"cargo.created", "load.created", "order.created"}:
        return []

    from_city, to_city = _extract_route_from_data(data)
    if not from_city and not to_city:
        return []

    async with async_session() as session:
        query = select(RouteSubscription.user_id).where(RouteSubscription.is_active.is_(True))
        if from_city:
            query = query.where(
                or_(
                    RouteSubscription.from_city.is_(None),
                    RouteSubscription.from_city.ilike(f"%{from_city}%"),
                )
            )
        if to_city:
            query = query.where(
                or_(
                    RouteSubscription.to_city.is_(None),
                    RouteSubscription.to_city.ilike(f"%{to_city}%"),
                )
            )

        result = await session.execute(query)
        return sorted({int(user_id) for user_id in result.scalars().all() if user_id})


async def _notify_many_users(
    user_ids: list[int],
    *,
    message: str,
    action_link: str | None,
    action_text: str,
) -> tuple[int, list[int]]:
    if not user_ids:
        return 0, []

    sent_count = 0
    failed_users: list[int] = []
    for user_id in user_ids:
        try:
            await _send_user_message(
                InternalNotifyUserRequest(
                    user_id=user_id,
                    message=message,
                    action_link=action_link,
                    action_text=action_text,
                )
            )
            sent_count += 1
        except Exception as exc:
            logger.warning("internal.event notify failed user_id=%s error=%s", user_id, str(exc)[:200])
            failed_users.append(user_id)
    return sent_count, failed_users


@router.post("/internal/event")
async def internal_event(
    body: BotInternalEvent,
    x_internal_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_internal_token(x_internal_token)

    event_type = _normalize_event_type(body.event_type)
    data = body.data if isinstance(body.data, dict) else {}

    action_link = data.get("action_link")
    if not isinstance(action_link, str) or not action_link.strip():
        action_link = None

    action_text = data.get("action_text")
    if not isinstance(action_text, str) or not action_text.strip():
        action_text = "Открыть"

    target_user_ids = await _resolve_event_targets(event_type, data)
    message = _build_internal_event_message(event_type, data)
    sent_count, failed_users = await _notify_many_users(
        target_user_ids,
        message=message,
        action_link=action_link,
        action_text=action_text,
    )

    logger.info(
        "internal.event accepted event_type=%s event_id=%s targets=%s sent=%s",
        event_type,
        body.event_id,
        len(target_user_ids),
        sent_count,
    )
    return {
        "ok": True,
        "event_type": event_type,
        "event_id": body.event_id,
        "target_count": len(target_user_ids),
        "sent_count": sent_count,
        "failed_users": failed_users,
    }


@router.post("/internal/sync")
@router.post("/internal/sync-data")
@router.post("/api/sync")
async def internal_sync_data(
    body: SharedSyncEvent,
    x_internal_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_internal_token(x_internal_token)

    user_id = _event_user_id(body)
    notified = False
    message_id = None

    if user_id:
        notify_payload = InternalNotifyUserRequest(
            user_id=user_id,
            message=_default_event_message(body),
            action_link=body.action_link,
        )
        try:
            result = await _send_user_message(notify_payload)
            notified = True
            message_id = result.get("message_id")
        except Exception as exc:
            logger.warning(
                "internal.sync_data.notify_failed event_id=%s user_id=%s error=%s",
                body.event_id,
                user_id,
                str(exc)[:200],
            )

    logger.info(
        "internal.sync_data accepted event_type=%s event_id=%s search_id=%s notified=%s",
        body.event_type,
        body.event_id,
        body.search_id,
        notified,
    )
    return {
        "ok": True,
        "event_id": body.event_id,
        "event_type": body.event_type,
        "search_id": body.search_id,
        "notified": notified,
        "message_id": message_id,
    }
