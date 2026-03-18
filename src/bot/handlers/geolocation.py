import json
import os
import secrets
import time
from math import radians, cos, sin, asin, sqrt

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, desc
from src.bot.keyboards import main_menu, back_menu
from src.core.database import async_session
from src.core.models import Cargo, CargoStatus, CargoLocation, User
from src.core.logger import logger
from src.core.redis import get_redis
from src.bot.bot import bot

router = Router()


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


async def _save_live_location(token, cargo, lat, lng, heading=None, speed=None):
    redis = await get_redis()
    data = {
        "lat": lat,
        "lng": lng,
        "heading": heading,
        "speed": speed,
        "ts": time.time(),
        "route": f"{cargo.from_city} → {cargo.to_city}",
        "cargo_id": cargo.id,
    }
    await redis.setex(f"live_loc:{token}", 28800, json.dumps(data))  # 8h TTL
    # append to history (last 500 points)
    await redis.lpush(
        f"live_hist:{token}",
        json.dumps({"lat": lat, "lng": lng, "ts": time.time()}),
    )
    await redis.ltrim(f"live_hist:{token}", 0, 499)
    await redis.expire(f"live_hist:{token}", 28800)


def location_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Отправить локацию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


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
        await cb.message.edit_text(
            text, reply_markup=tracking_menu(cargo_id), disable_web_page_preview=True
        )
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
        f"📍 Отправь свою локацию для груза #{cargo_id}", reply_markup=location_kb()
    )
    await cb.answer()


@router.callback_query(F.data == "start_live_tracking")
async def start_live_tracking(cb: CallbackQuery):
    """Водитель нажал кнопку — просим отправить Live Location."""
    user_id = cb.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(Cargo)
            .where(Cargo.carrier_id == user_id)
            .where(Cargo.status == CargoStatus.IN_PROGRESS)
            .order_by(desc(Cargo.created_at))
            .limit(1)
        )
        cargo = result.scalar_one_or_none()

    if not cargo:
        await cb.answer("❌ Нет активных рейсов", show_alert=True)
        return

    # Генерируем токен
    token = secrets.token_urlsafe(12)
    redis = await get_redis()
    await redis.set(f"tracking_token:{user_id}", token, ex=28800)

    # Ссылка для клиента
    bot_domain = os.environ.get("BOT_DOMAIN", "")
    if not bot_domain:
        # Fallback: extract from WEBAPP_URL
        webapp_url = os.environ.get("WEBAPP_URL", "")
        if webapp_url:
            bot_domain = webapp_url.replace("https://", "").replace("http://", "").rstrip("/")
    track_url = f"https://{bot_domain}/track/{token}" if bot_domain else f"/track/{token}"

    # Уведомляем заказчика груза
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
        pass

    await cb.answer()

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📍 Транслировать местоположение", request_location=True
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    await cb.message.answer(
        f"📡 <b>Трекинг рейса запущен</b>\n\n"
        f"Маршрут: <b>{cargo.from_city} → {cargo.to_city}</b>\n"
        f"Груз #{cargo.id}\n\n"
        f"Нажми кнопку ниже и выбери <b>«Транслировать моё местоположение»</b> "
        f"на 8 часов. Заказчик видит тебя в реальном времени.\n\n"
        f"🔗 Ссылка для клиента:\n<code>{track_url}</code>",
        parse_mode="HTML",
        reply_markup=kb,
    )


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
        "✅ Трансляция местоположения завершена.", reply_markup=ReplyKeyboardRemove()
    )


@router.message(F.location)
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
            await message.answer(
                "❌ Нет активных грузов для отслеживания",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        loc = CargoLocation(
            cargo_id=cargo.id,
            user_id=message.from_user.id,
            latitude=lat,
            longitude=lon,
            address=f"{lat:.4f}, {lon:.4f}",
        )
        session.add(loc)
        await session.commit()

        if cargo.tracking_enabled:
            try:
                await bot.send_message(
                    cargo.owner_id,
                    f"📍 <b>Обновление локации</b>\n\n"
                    f"Груз #{cargo.id}: {cargo.from_city} → {cargo.to_city}\n"
                    f"<a href='https://maps.google.com/?q={lat},{lon}'>Открыть на карте</a>",
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

    await message.answer(
        f"✅ Локация сохранена!\n\n"
        f"Груз #{cargo.id}\n"
        f"📍 {lat:.4f}, {lon:.4f}",
        reply_markup=ReplyKeyboardRemove(),
    )
    logger.info(f"Location saved for cargo {cargo.id}: {lat}, {lon}")


@router.edited_message(F.location)
async def handle_live_location(message: Message):
    """Обработка обновлений Live Location от Telegram."""
    lat = message.location.latitude
    lng = message.location.longitude
    heading = message.location.heading  # может быть None
    speed = message.location.speed  # может быть None (м/с)

    user_id = message.from_user.id
    redis = await get_redis()

    # Получаем токен трекинга для этого пользователя
    token = await redis.get(f"tracking_token:{user_id}")
    if not token:
        return  # нет активного трекинга

    # Ищем активный груз
    async with async_session() as session:
        result = await session.execute(
            select(Cargo)
            .where(Cargo.carrier_id == user_id)
            .where(Cargo.status == CargoStatus.IN_PROGRESS)
            .order_by(desc(Cargo.created_at))
            .limit(1)
        )
        cargo = result.scalar_one_or_none()

    if not cargo:
        return

    await _save_live_location(token, cargo, lat, lng, heading, speed)

    # Сохраняем в БД (одна запись на N минут, не каждое обновление)
    last_db_save = await redis.get(f"last_db_loc:{user_id}")
    if not last_db_save or time.time() - float(last_db_save) > 300:  # каждые 5 мин в БД
        async with async_session() as session:
            loc = CargoLocation(
                cargo_id=cargo.id,
                user_id=user_id,
                latitude=lat,
                longitude=lng,
                address=f"{lat:.5f},{lng:.5f}",
            )
            session.add(loc)
            await session.commit()
        await redis.set(f"last_db_loc:{user_id}", str(time.time()), ex=3600)

    logger.debug("Live location update: user=%s lat=%.5f lng=%.5f", user_id, lat, lng)


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
        await cb.message.edit_text(
            text, reply_markup=tracking_menu(cargo_id), disable_web_page_preview=True
        )
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
    except Exception:
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
