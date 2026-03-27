from __future__ import annotations

import json
import secrets
import time

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, desc
from src.core.config import settings
from src.core.database import async_session
from src.core.models import Cargo, CargoStatus, CargoLocation
from src.core.logger import logger
from src.core.redis import get_redis
from src.bot.bot import bot

router = Router()


def location_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Отправить локацию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def _track_url(token: str) -> str:
    base = (settings.webapp_url or "").rstrip("/")
    if base:
        return f"{base}/track/{token}"
    return f"/track/{token}"


async def _save_live_location(
    redis,
    token: str,
    cargo: Cargo,
    lat: float,
    lng: float,
    *,
    heading: int | None = None,
    speed: float | None = None,
    ts: float | None = None,
) -> None:
    now = ts if ts is not None else time.time()
    payload = {
        "lat": lat,
        "lng": lng,
        "heading": heading,
        "speed": speed,
        "ts": now,
        "route": f"{cargo.from_city} → {cargo.to_city}",
        "cargo_id": cargo.id,
    }
    await redis.setex(f"live_loc:{token}", 28800, json.dumps(payload))
    await redis.lpush(f"live_hist:{token}", json.dumps({"lat": lat, "lng": lng, "ts": now}))
    await redis.ltrim(f"live_hist:{token}", 0, 499)
    await redis.expire(f"live_hist:{token}", 28800)


def tracking_menu(cargo_id: int):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📍 Обновить локацию", callback_data=f"update_loc_{cargo_id}"))
    b.row(InlineKeyboardButton(text="🗺 История маршрута", callback_data=f"route_history_{cargo_id}"))
    b.row(InlineKeyboardButton(text="🔔 Вкл/выкл уведомления", callback_data=f"toggle_tracking_{cargo_id}"))
    b.row(InlineKeyboardButton(text="📡 Live-трекинг", callback_data="start_live_tracking"))
    b.row(InlineKeyboardButton(text="⏹ Остановить трекинг", callback_data="stop_live_tracking"))
    b.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"cargo_{cargo_id}"))
    return b.as_markup()

@router.callback_query(F.data.startswith("tracking_"))
async def show_tracking(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[1])
    
    async with async_session() as session:
        result = await session.execute(select(Cargo).where(Cargo.id == cargo_id))
        cargo = result.scalar_one_or_none()
        
        if not cargo:
            await cb.answer("❌ Груз не найден", show_alert=True)
            return
        
        loc_result = await session.execute(
            select(CargoLocation)
            .where(CargoLocation.cargo_id == cargo_id)
            .order_by(desc(CargoLocation.created_at))
            .limit(1)
        )
        last_loc = loc_result.scalar_one_or_none()
    
    text = f"🗺 <b>Отслеживание груза #{cargo_id}</b>\n\n"
    text += f"Маршрут: {cargo.from_city} → {cargo.to_city}\n"
    text += f"Статус: {cargo.status.value}\n\n"
    
    if last_loc:
        text += "📍 <b>Последняя локация:</b>\n"
        text += f"   {last_loc.address or 'Без адреса'}\n"
        text += f"   {last_loc.created_at.strftime('%d.%m %H:%M')}\n"
        text += f"   <a href='https://maps.google.com/?q={last_loc.latitude},{last_loc.longitude}'>Открыть карту</a>"
    else:
        text += "📍 Локация ещё не отправлена"
    
    try:
        await cb.message.edit_text(text, reply_markup=tracking_menu(cargo_id), disable_web_page_preview=True)
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data.startswith("update_loc_"))
async def request_location(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[2])
    
    async with async_session() as session:
        result = await session.execute(select(Cargo).where(Cargo.id == cargo_id))
        cargo = result.scalar_one_or_none()
        
        if not cargo:
            await cb.answer("❌ Груз не найден", show_alert=True)
            return
        
        if cargo.carrier_id != cb.from_user.id:
            await cb.answer("❌ Только перевозчик может обновлять локацию", show_alert=True)
            return
    
    await cb.message.answer(
        f"📍 Отправь свою локацию для груза #{cargo_id}",
        reply_markup=location_kb()
    )
    await cb.answer()


@router.callback_query(F.data == "start_live_tracking")
async def start_live_tracking(cb: CallbackQuery):
    user_id = cb.from_user.id

    async with async_session() as session:
        cargo = (
            await session.execute(
                select(Cargo)
                .where(Cargo.carrier_id == user_id)
                .where(Cargo.status == CargoStatus.IN_PROGRESS)
                .order_by(desc(Cargo.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()

    if not cargo:
        await cb.answer("❌ Нет активных рейсов", show_alert=True)
        return

    token = secrets.token_urlsafe(12)
    redis = await get_redis()
    await redis.setex(f"tracking_token:{user_id}", 28800, token)
    track_url = _track_url(token)

    try:
        await cb.bot.send_message(
            cargo.owner_id,
            f"🚚 <b>Водитель начал трансляцию местоположения</b>\n\n"
            f"Маршрут: {cargo.from_city} → {cargo.to_city}\n"
            f"Груз #{cargo.id}\n\n"
            f"📍 <a href='{track_url}'>Отслеживать в реальном времени</a>",
            parse_mode="HTML",
            disable_web_page_preview=False,
        )
    except Exception:
        logger.exception("Failed to notify owner about live tracking start for cargo %s", cargo.id)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Транслировать местоположение", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    await cb.message.answer(
        f"📡 <b>Трекинг рейса запущен</b>\n\n"
        f"Маршрут: <b>{cargo.from_city} → {cargo.to_city}</b>\n"
        f"Груз #{cargo.id}\n\n"
        f"Нажми кнопку ниже и выбери <b>«Транслировать моё местоположение»</b>.\n\n"
        f"🔗 Ссылка для клиента:\n<code>{track_url}</code>",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data == "stop_live_tracking")
async def stop_live_tracking(cb: CallbackQuery):
    user_id = cb.from_user.id
    redis = await get_redis()
    token = await redis.get(f"tracking_token:{user_id}")
    if token:
        await redis.delete(f"live_loc:{token}")
        await redis.delete(f"tracking_token:{user_id}")
    await cb.answer("Трекинг остановлен")
    await cb.message.answer(
        "✅ Трансляция местоположения завершена.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(StateFilter(None), F.location)
async def handle_location(message: Message):
    lat = message.location.latitude
    lon = message.location.longitude
    
    async with async_session() as session:
        result = await session.execute(
            select(Cargo)
            .where(Cargo.carrier_id == message.from_user.id)
            .where(Cargo.status == CargoStatus.IN_PROGRESS)
            .order_by(desc(Cargo.created_at))
            .limit(1)
        )
        cargo = result.scalar_one_or_none()
        
        if not cargo:
            await message.answer("❌ Нет активных грузов для отслеживания", reply_markup=ReplyKeyboardRemove())
            return
        
        loc = CargoLocation(
            cargo_id=cargo.id,
            user_id=message.from_user.id,
            latitude=lat,
            longitude=lon,
            address=f"{lat:.4f}, {lon:.4f}"
        )
        session.add(loc)
        await session.commit()

        redis = await get_redis()
        token = await redis.get(f"tracking_token:{message.from_user.id}")
        if token:
            await _save_live_location(redis, token, cargo, lat, lon)

        if cargo.tracking_enabled:
            try:
                await bot.send_message(
                    cargo.owner_id,
                    f"📍 <b>Обновление локации</b>\n\n"
                    f"Груз #{cargo.id}: {cargo.from_city} → {cargo.to_city}\n"
                    f"<a href='https://maps.google.com/?q={lat},{lon}'>Открыть на карте</a>",
                    disable_web_page_preview=True
                )
            except Exception:
                logger.exception("Failed to notify owner about location update for cargo %s", cargo.id)
    
    await message.answer(
        f"✅ Локация сохранена!\n\n"
        f"Груз #{cargo.id}\n"
        f"📍 {lat:.4f}, {lon:.4f}",
        reply_markup=ReplyKeyboardRemove()
    )
    logger.info(f"Location saved for cargo {cargo.id}: {lat}, {lon}")


@router.edited_message(F.location)
async def handle_live_location(message: Message):
    lat = message.location.latitude
    lng = message.location.longitude
    heading = message.location.heading
    speed = message.location.speed

    redis = await get_redis()
    token = await redis.get(f"tracking_token:{message.from_user.id}")
    if not token:
        return

    async with async_session() as session:
        cargo = (
            await session.execute(
                select(Cargo)
                .where(Cargo.carrier_id == message.from_user.id)
                .where(Cargo.status == CargoStatus.IN_PROGRESS)
                .order_by(desc(Cargo.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()

        if not cargo:
            return

        await _save_live_location(
            redis,
            token,
            cargo,
            lat,
            lng,
            heading=heading,
            speed=speed,
        )

        last_db_save = await redis.get(f"last_db_loc:{message.from_user.id}")
        now = time.time()
        should_persist = not last_db_save or now - float(last_db_save) > 300
        if should_persist:
            session.add(
                CargoLocation(
                    cargo_id=cargo.id,
                    user_id=message.from_user.id,
                    latitude=lat,
                    longitude=lng,
                    address=f"{lat:.5f},{lng:.5f}",
                )
            )
            await session.commit()
            await redis.setex(f"last_db_loc:{message.from_user.id}", 3600, str(now))

    logger.debug("Live location update: user=%s lat=%.5f lng=%.5f", message.from_user.id, lat, lng)


@router.callback_query(F.data.startswith("route_history_"))
async def route_history(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[2])
    
    async with async_session() as session:
        result = await session.execute(
            select(CargoLocation)
            .where(CargoLocation.cargo_id == cargo_id)
            .order_by(desc(CargoLocation.created_at))
            .limit(10)
        )
        locations = result.scalars().all()
    
    if not locations:
        await cb.answer("📍 Нет данных о маршруте", show_alert=True)
        return
    
    text = f"🗺 <b>История маршрута #{cargo_id}</b>\n\n"
    for i, loc in enumerate(locations, 1):
        text += f"{i}. {loc.created_at.strftime('%d.%m %H:%M')}\n"
        text += f"   📍 <a href='https://maps.google.com/?q={loc.latitude},{loc.longitude}'>{loc.latitude:.4f}, {loc.longitude:.4f}</a>\n\n"
    
    try:
        await cb.message.edit_text(text, reply_markup=tracking_menu(cargo_id), disable_web_page_preview=True)
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data.startswith("toggle_tracking_"))
async def toggle_tracking(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[2])
    
    async with async_session() as session:
        result = await session.execute(select(Cargo).where(Cargo.id == cargo_id))
        cargo = result.scalar_one_or_none()
        
        if not cargo:
            await cb.answer("❌ Груз не найден", show_alert=True)
            return
        
        if cargo.owner_id != cb.from_user.id:
            await cb.answer("❌ Только заказчик может управлять уведомлениями", show_alert=True)
            return
        
        cargo.tracking_enabled = not cargo.tracking_enabled
        await session.commit()
        
        status = "включены ✅" if cargo.tracking_enabled else "выключены ❌"
        await cb.answer(f"Уведомления {status}", show_alert=True)

@router.message(F.text.startswith("/track_"))
async def track_cargo(message: Message):
    try:
        cargo_id = int(message.text.split("_")[1])
    except (IndexError, TypeError, ValueError):
        return
    
    async with async_session() as session:
        result = await session.execute(select(Cargo).where(Cargo.id == cargo_id))
        cargo = result.scalar_one_or_none()
        
        if not cargo:
            await message.answer("❌ Груз не найден")
            return
        
        if cargo.owner_id != message.from_user.id and cargo.carrier_id != message.from_user.id:
            await message.answer("❌ Нет доступа")
            return
        
        loc_result = await session.execute(
            select(CargoLocation)
            .where(CargoLocation.cargo_id == cargo_id)
            .order_by(desc(CargoLocation.created_at))
            .limit(1)
        )
        last_loc = loc_result.scalar_one_or_none()
    
    text = f"🗺 <b>Отслеживание груза #{cargo_id}</b>\n\n"
    text += f"Маршрут: {cargo.from_city} → {cargo.to_city}\n"
    text += f"Статус: {cargo.status.value}\n\n"
    
    if last_loc:
        text += "📍 <b>Последняя локация:</b>\n"
        text += f"   {last_loc.created_at.strftime('%d.%m %H:%M')}\n"
        text += f"   <a href='https://maps.google.com/?q={last_loc.latitude},{last_loc.longitude}'>Открыть карту</a>"
    else:
        text += "📍 Локация ещё не отправлена"
    
    await message.answer(text, reply_markup=tracking_menu(cargo_id), disable_web_page_preview=True)
