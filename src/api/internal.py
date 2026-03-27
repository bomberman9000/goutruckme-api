from __future__ import annotations

from datetime import datetime
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import or_, select

from src.bot.bot import bot
from src.bot.keyboards import cargo_open_rows
from src.core.config import settings
from src.core.database import async_session
from src.core.logger import logger
from src.core.models import Cargo, CargoPaymentStatus, CargoStatus, RouteSubscription, UserVehicle
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


def _build_cargo_notify_kb(
    cargo_id: int | None,
    external_url: str | None,
    *,
    site_action_link: str | None = None,
    allow_synthetic_site_link: bool = False,
) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []

    if cargo_id:
        resolved_site_link = (site_action_link or "").strip()
        if not resolved_site_link and allow_synthetic_site_link:
            site_url = (settings.gruzpotok_public_url or "").rstrip("/")
            if site_url:
                resolved_site_link = f"{site_url}/?cargo_id={cargo_id}"
        rows.extend(cargo_open_rows(cargo_id, site_action_link=resolved_site_link))

    if external_url:
        rows.append([InlineKeyboardButton(text="🔗 Источник", url=external_url)])

    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _default_event_message(event: SharedSyncEvent) -> str:
    order = event.order
    vehicle = event.vehicle

    if event.message:
        return event.message

    if event.event_type in {"cargo.created", "order.created"} and order:
        route = " → ".join([x for x in [order.from_city, order.to_city] if x])
        parts = [f"📦 <b>Новый груз #{order.id}</b>"]
        parts.append(f"📍 {route or 'маршрут не указан'}")

        details = []
        if order.cargo_type or order.body_type:
            details.append(str(order.cargo_type or order.body_type))
        if order.weight_t and float(order.weight_t) > 0:
            details.append(f"{order.weight_t} т")
        if details:
            parts.append("🚛 " + " | ".join(details))

        if order.price_rub and int(order.price_rub) > 0:
            parts.append(f"💰 {int(order.price_rub):,} ₽")
        if order.load_date:
            parts.append(f"📅 {order.load_date}")

        meta = event.metadata or {}
        phone = str(meta.get("phone") or order.meta.get("phone") or "").strip()
        if phone:
            masked = phone[:-4] + "****" if len(phone) > 4 else "****"
            parts.append(f"🔒 📞 {masked}")
        return "\n".join(parts)

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


def _extract_external_url(event: SharedSyncEvent) -> str | None:
    candidates = [
        event.action_link,
        event.metadata.get("external_url") if isinstance(event.metadata, dict) else None,
    ]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        value = candidate.strip()
        if value.startswith("http://") or value.startswith("https://"):
            return value[:500]
    return None


def _normalize_source_platform(event: SharedSyncEvent) -> str:
    source_value = event.source
    if (not source_value or not str(source_value).strip()) and event.order:
        source_value = event.order.source
    source = str((source_value or "").strip() or "unknown")
    return source[:64]


def _parse_load_date(value: str | None) -> datetime:
    raw = (value or "").strip()
    if not raw:
        return datetime.utcnow()
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.utcnow()


async def _create_cargo_from_sync_event(event: SharedSyncEvent) -> int | None:
    order = event.order
    if event.event_type not in {"order.created", "cargo.created"} or order is None:
        return None

    source_platform = _normalize_source_platform(event)
    if source_platform == "gruzpotok-api":
        return None

    owner_id = _event_user_id(event) or settings.parser_default_user_id
    if owner_id is None:
        return None

    from_city = str((order.from_city or "").strip() or "Не указан")[:100]
    to_city = str((order.to_city or "").strip() or "Не указан")[:100]
    cargo_type = str((order.cargo_type or order.body_type or "тент").strip() or "тент")[:100]
    try:
        weight = float(order.weight_t) if order.weight_t is not None else 0.1
    except (TypeError, ValueError):
        weight = 0.1
    if weight <= 0:
        weight = 0.1

    try:
        price = int(order.price_rub) if order.price_rub is not None else 0
    except (TypeError, ValueError):
        price = 0
    if price < 0:
        price = 0

    raw_preview = None
    if isinstance(event.metadata, dict):
        raw_preview = event.metadata.get("raw_text")
    if not isinstance(raw_preview, str):
        raw_preview = None

    external_url = _extract_external_url(event)

    async with async_session() as session:
        if external_url:
            existing = await session.scalar(
                select(Cargo).where(
                    Cargo.external_url == external_url,
                    Cargo.source_platform == source_platform,
                )
            )
            if existing:
                return int(existing.id)

        cargo = Cargo(
            owner_id=int(owner_id),
            from_city=from_city,
            to_city=to_city,
            cargo_type=cargo_type,
            weight=weight,
            price=price,
            load_date=_parse_load_date(order.load_date),
            comment=(raw_preview or "")[:500] or None,
            external_url=external_url,
            source_platform=source_platform,
            status=CargoStatus.NEW,
            payment_status=CargoPaymentStatus.UNSECURED,
        )
        session.add(cargo)
        await session.commit()
        await session.refresh(cargo)

    logger.info(
        "internal.sync_data cargo_created id=%s route=%s->%s source=%s",
        cargo.id,
        cargo.from_city,
        cargo.to_city,
        cargo.source_platform,
    )
    return int(cargo.id)


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

    cargo_id = None
    try:
        cargo_id = await _create_cargo_from_sync_event(body)
    except Exception as exc:
        logger.warning(
            "internal.sync_data.cargo_create_failed event_id=%s error=%s",
            body.event_id,
            str(exc)[:200],
        )
    vehicle_sync: dict[str, Any] = {"updated": False}
    if body.source == "gruzpotok-api" and body.vehicle:
        vehicle_payload = body.vehicle
        vehicle_id_raw = str(vehicle_payload.id or "").strip()
        vehicle_id = _extract_user_id(vehicle_id_raw)
        if vehicle_id:
            async with async_session() as session:
                vehicle = await session.scalar(select(UserVehicle).where(UserVehicle.id == vehicle_id))
                if vehicle:
                    if vehicle_payload.location_city:
                        vehicle.location_city = str(vehicle_payload.location_city).strip() or vehicle.location_city
                    elif vehicle_payload.from_city:
                        vehicle.location_city = str(vehicle_payload.from_city).strip() or vehicle.location_city

                    if vehicle_payload.body_type:
                        vehicle.body_type = str(vehicle_payload.body_type).strip() or vehicle.body_type
                    if vehicle_payload.capacity_tons is not None:
                        vehicle.capacity_tons = float(vehicle_payload.capacity_tons)
                    elif vehicle_payload.capacity_t is not None:
                        vehicle.capacity_tons = float(vehicle_payload.capacity_t)
                    if vehicle_payload.plate_number:
                        vehicle.plate_number = str(vehicle_payload.plate_number).strip() or vehicle.plate_number

                    site_status = str((vehicle_payload.meta or {}).get("site_status") or "").strip().lower()
                    if vehicle_payload.is_available is not None:
                        vehicle.is_available = bool(vehicle_payload.is_available)
                    elif site_status in {"inactive", "archived"}:
                        vehicle.is_available = False
                    elif site_status == "active":
                        available_today = (vehicle_payload.meta or {}).get("site_available_today")
                        if isinstance(available_today, bool):
                            vehicle.is_available = available_today

                    await session.commit()
                    await session.refresh(vehicle)
                    vehicle_sync = {
                        "updated": True,
                        "vehicle_id": int(vehicle.id),
                        "is_available": bool(vehicle.is_available),
                        "location_city": vehicle.location_city,
                    }

    user_id = _event_user_id(body)
    notified = False
    message_id = None

    if cargo_id and body.order and not body.order.id:
        body.order.id = str(cargo_id)

    if user_id:
        notify_cargo_id = cargo_id
        if notify_cargo_id is None and body.order and body.order.id:
            notify_cargo_id = _extract_user_id(body.order.id)
        msg_text = _default_event_message(body)
        raw_metadata = body.metadata if isinstance(body.metadata, dict) else {}
        raw_site_action_link = raw_metadata.get("site_action_link") or raw_metadata.get("site_url")
        site_action_link = raw_site_action_link.strip() if isinstance(raw_site_action_link, str) else None
        source_value = str(body.source or (body.order.source if body.order else "") or "").strip().lower()
        allow_synthetic_site_link = source_value in {"tg-bot", "gruzpotok-api"}
        reply_markup = _build_cargo_notify_kb(
            notify_cargo_id,
            body.action_link,
            site_action_link=site_action_link,
            allow_synthetic_site_link=allow_synthetic_site_link,
        )
        try:
            sent = await bot.send_message(
                user_id,
                msg_text,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            notified = True
            message_id = sent.message_id
        except Exception as exc:
            logger.warning(
                "internal.sync_data.notify_failed event_id=%s user_id=%s error=%s",
                body.event_id,
                user_id,
                str(exc)[:200],
            )

    logger.info(
        "internal.sync_data accepted event_type=%s event_id=%s cargo_id=%s search_id=%s notified=%s",
        body.event_type,
        body.event_id,
        cargo_id,
        body.search_id,
        notified,
    )
    return {
        "ok": True,
        "event_id": body.event_id,
        "event_type": body.event_type,
        "cargo_id": cargo_id,
        "search_id": body.search_id,
        "vehicle_sync": vehicle_sync,
        "notified": notified,
        "message_id": message_id,
    }
