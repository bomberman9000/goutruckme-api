"""Driver live-location tracking.

Flow:
  1. Driver taps "🟢 Выйти на линию" (from truck menu or /go_online)
  2. Bot requests phone contact (Telegram native button)
  3. Bot requests live-location sharing
  4. As driver moves, F.location updates arrive → stored in DriverTracking
  5. Driver taps "🔴 Сойти с линии" → is_active=False, location removed from map
"""
from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import select

from src.core.database import async_session
from src.core.logger import logger
from src.core.models import DriverTracking

router = Router()

_STALE_MINUTES = 30  # hide driver from map if no update for 30 min


class TrackingState(StatesGroup):
    wait_phone = State()
    wait_location = State()


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def _phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📞 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Поделиться геолокацией", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _online_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Сойти с линии", callback_data="driver_go_offline")],
        [InlineKeyboardButton(text="◀️ Меню", callback_data="menu")],
    ])


def _offline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Выйти на линию", callback_data="driver_go_online")],
        [InlineKeyboardButton(text="◀️ Меню", callback_data="menu")],
    ])


# ---------------------------------------------------------------------------
# Entry: /go_online or callback
# ---------------------------------------------------------------------------

@router.message(Command("go_online"))
async def cmd_go_online(message: Message, state: FSMContext) -> None:
    await _start_tracking(message, state)


@router.callback_query(F.data == "driver_go_online")
async def cb_go_online(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _start_tracking(cb.message, state, edit=True)


async def _start_tracking(msg: Message, state: FSMContext, *, edit: bool = False) -> None:
    text = (
        "🟢 <b>Выход на линию</b>\n\n"
        "Твоя геопозиция будет видна заказчикам на карте платформы.\n\n"
        "Шаг 1/2 — поделись номером телефона:"
    )
    await state.set_state(TrackingState.wait_phone)
    if edit:
        try:
            await msg.edit_text(text, parse_mode="HTML")
        except Exception:
            pass
        await msg.answer(text, parse_mode="HTML", reply_markup=_phone_kb())
    else:
        await msg.answer(text, parse_mode="HTML", reply_markup=_phone_kb())


# ---------------------------------------------------------------------------
# Step 1: receive phone
# ---------------------------------------------------------------------------

@router.message(TrackingState.wait_phone, F.contact)
async def got_phone(message: Message, state: FSMContext) -> None:
    phone = message.contact.phone_number
    await state.update_data(phone=phone)
    await state.set_state(TrackingState.wait_location)
    await message.answer(
        "✅ Номер получен.\n\n"
        "Шаг 2/2 — поделись своей геолокацией (можно Live Location для автообновления):",
        reply_markup=_location_kb(),
    )


@router.message(TrackingState.wait_phone)
async def wait_phone_wrong(message: Message, state: FSMContext) -> None:
    import re
    if message.text and re.search(r"(?i)\d+\s*(кг|т\b|тн\b|тонн)", message.text):
        await state.clear()
        from src.bot.handlers.cargo import nlp_cargo_detect
        await nlp_cargo_detect(message, state)
        return
    await message.answer("Нажми кнопку «📞 Поделиться номером» или /cancel для отмены")


# ---------------------------------------------------------------------------
# Step 2: receive location (first time in FSM)
# ---------------------------------------------------------------------------

@router.message(TrackingState.wait_location, F.location)
async def got_location_first(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    phone = data.get("phone") or ""
    await state.clear()

    lat = message.location.latitude
    lon = message.location.longitude
    user_id = message.from_user.id
    full_name = message.from_user.full_name

    await _upsert_driver(user_id, phone, full_name, lat, lon, is_active=True)

    maps_url = f"https://maps.google.com/?q={lat},{lon}"
    await message.answer(
        f"✅ <b>Ты на линии!</b>\n\n"
        f"📍 Локация получена. Заказчики видят тебя на карте.\n"
        f"<a href='{maps_url}'>Моя позиция</a>\n\n"
        f"Нажми «🔴 Сойти с линии» когда закончишь.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
        disable_web_page_preview=True,
    )
    await message.answer("Управление трекингом:", reply_markup=_online_kb())

    logger.info("driver_tracking.online user_id=%d lat=%.4f lon=%.4f", user_id, lat, lon)


@router.message(TrackingState.wait_location)
async def wait_location_wrong(message: Message, state: FSMContext) -> None:
    import re
    if message.text and re.search(r"(?i)\d+\s*(кг|т\b|тн\b|тонн)", message.text):
        await state.clear()
        from src.bot.handlers.cargo import nlp_cargo_detect
        await nlp_cargo_detect(message, state)
        return
    await message.answer("Нажми кнопку «📍 Поделиться геолокацией» или /cancel для отмены")


# ---------------------------------------------------------------------------
# Live location updates (outside FSM — continuous updates while sharing)
# ---------------------------------------------------------------------------

@router.message(F.location)
async def live_location_update(message: Message) -> None:
    """Handle Telegram live-location updates from active drivers."""
    lat = message.location.latitude
    lon = message.location.longitude
    user_id = message.from_user.id

    async with async_session() as session:
        driver = await session.scalar(
            select(DriverTracking).where(DriverTracking.user_id == user_id)
        )
        if driver and driver.is_active:
            driver.lat = lat
            driver.lon = lon
            driver.updated_at = datetime.utcnow()
            await session.commit()
            logger.info("driver_tracking.update user_id=%d lat=%.4f lon=%.4f", user_id, lat, lon)


# ---------------------------------------------------------------------------
# Go offline
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "driver_go_offline")
async def cb_go_offline(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id

    async with async_session() as session:
        driver = await session.scalar(
            select(DriverTracking).where(DriverTracking.user_id == user_id)
        )
        if driver:
            driver.is_active = False
            driver.updated_at = datetime.utcnow()
            await session.commit()

    try:
        await cb.message.edit_text(
            "🔴 <b>Ты сошёл с линии.</b>\n\nТвоя геопозиция скрыта с карты.",
            parse_mode="HTML",
            reply_markup=_offline_kb(),
        )
    except Exception:
        pass
    await cb.answer("Сошёл с линии")
    logger.info("driver_tracking.offline user_id=%d", user_id)


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

@router.message(Command("tracking_status"))
async def tracking_status(message: Message) -> None:
    user_id = message.from_user.id
    async with async_session() as session:
        driver = await session.scalar(
            select(DriverTracking).where(DriverTracking.user_id == user_id)
        )

    if not driver or not driver.is_active:
        await message.answer(
            "🔴 Ты не в линии.\n\nНажми кнопку, чтобы выйти на линию:",
            reply_markup=_offline_kb(),
        )
        return

    stale = (datetime.utcnow() - driver.updated_at) > timedelta(minutes=_STALE_MINUTES)
    maps_url = f"https://maps.google.com/?q={driver.lat},{driver.lon}"
    status = "⚠️ Геолокация устарела" if stale else "✅ Активен"
    await message.answer(
        f"🟢 <b>Ты на линии</b>\n\n"
        f"Статус: {status}\n"
        f"Обновлено: {driver.updated_at.strftime('%d.%m %H:%M')}\n"
        f"<a href='{maps_url}'>Моя позиция</a>",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=_online_kb(),
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _upsert_driver(
    user_id: int, phone: str, full_name: str,
    lat: float, lon: float, *, is_active: bool,
) -> None:
    async with async_session() as session:
        driver = await session.scalar(
            select(DriverTracking).where(DriverTracking.user_id == user_id)
        )
        if driver:
            driver.phone = phone or driver.phone
            driver.full_name = full_name or driver.full_name
            driver.lat = lat
            driver.lon = lon
            driver.is_active = is_active
            driver.updated_at = datetime.utcnow()
        else:
            driver = DriverTracking(
                user_id=user_id,
                phone=phone,
                full_name=full_name,
                lat=lat,
                lon=lon,
                is_active=is_active,
            )
            session.add(driver)
        await session.commit()
