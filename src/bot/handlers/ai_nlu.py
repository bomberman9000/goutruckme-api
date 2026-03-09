"""
src/bot/handlers/ai_nlu.py
Zero-Command UI: любой свободный текст → parse_intent → action.
Регистрируется последним — catch-all для нераспознанных сообщений.
"""
import asyncio
import logging
import os

import aiohttp
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

log = logging.getLogger(__name__)
router = Router()

AI_ENGINE_URL = os.environ.get("AI_ENGINE_URL", "http://localhost:8010")
AI_ENGINE_TOKEN = os.environ.get("AI_ENGINE_TOKEN", "")


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _ai(path: str, payload: dict) -> dict:
    if AI_ENGINE_TOKEN:
        payload["token"] = AI_ENGINE_TOKEN
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as s:
            r = await s.post(f"{AI_ENGINE_URL}{path}", json=payload)
            if r.status != 200:
                return {"error": await r.text()}
            return await r.json()
    except Exception as e:
        return {"error": str(e)[:200]}


async def _ai_get(path: str, params: dict | None = None) -> dict:
    if AI_ENGINE_TOKEN and params is not None:
        params["token"] = AI_ENGINE_TOKEN
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as s:
            r = await s.get(f"{AI_ENGINE_URL}{path}", params=params or {})
            if r.status != 200:
                return {"error": await r.text()}
            return await r.json()
    except Exception as e:
        return {"error": str(e)[:200]}


async def _send_typing(message: Message, stop_event: asyncio.Event):
    """Периодически шлёт typing пока LLM думает."""
    while not stop_event.is_set():
        try:
            await message.bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        await asyncio.sleep(4)


# ─── Ask AI Logist (кнопка в меню) ───────────────────────────────────────────

def _ask_ai_confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🔄 Спросить ещё", callback_data="ask_ai_logist"))
    b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
    return b.as_markup()


@router.callback_query(F.data == "ask_ai_logist")
async def cb_ask_ai_logist(call: CallbackQuery, state: FSMContext):
    await state.set_state("ask_ai_logist:waiting")
    await call.message.edit_text(
        "🤖 <b>AI-логист слушает</b>\n\n"
        "Задай любой вопрос о грузоперевозках:\n"
        "• Почему дорого до Тюмени?\n"
        "• Когда лучше отправлять в Питер?\n"
        "• Что такое ставка за км?\n\n"
        "Просто напиши вопрос ✍️",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(F.state == "ask_ai_logist:waiting")
async def handle_ask_ai_question(message: Message, state: FSMContext):
    await state.clear()
    stop = asyncio.Event()
    typing_task = asyncio.create_task(_send_typing(message, stop))

    result = await _ai("/ask_logist", {"question": message.text})
    stop.set()
    typing_task.cancel()

    if result.get("error"):
        await message.answer("⚠️ AI временно недоступен. Попробуй позже.")
        return

    answer = result.get("answer", "").strip()
    cached = "⚡ (кэш)" if result.get("cached") else ""
    await message.answer(
        f"🤖 <b>AI-логист</b> {cached}\n\n{answer}",
        parse_mode="HTML",
        reply_markup=_ask_ai_confirm_kb(),
    )


# ─── NLU catch-all ───────────────────────────────────────────────────────────

def _find_transport_kb(from_city: str | None, to_city: str | None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if from_city and to_city:
        b.row(InlineKeyboardButton(
            text="💰 Узнать цену маршрута",
            callback_data=f"nlu_price:{from_city}:{to_city}",
        ))
    b.row(InlineKeyboardButton(text="🔍 Поиск машин", callback_data="find_truck"))
    b.row(InlineKeyboardButton(text="📦 Разместить груз", callback_data="add_cargo"))
    b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
    return b.as_markup()


def _get_price_kb(from_city: str, to_city: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(
        text="🔮 Прогноз на неделю",
        callback_data=f"nlu_forecast:{from_city}:{to_city}",
    ))
    b.row(InlineKeyboardButton(text="🔍 Найти машину", callback_data="find_truck"))
    b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
    return b.as_markup()


@router.callback_query(F.data.startswith("nlu_price:"))
async def cb_nlu_price(call: CallbackQuery):
    _, from_city, to_city = call.data.split(":", 2)
    stop = asyncio.Event()
    typing_task = asyncio.create_task(_send_typing(call.message, stop))

    result = await _ai("/route_price", {"from_city": from_city, "to_city": to_city})
    stop.set()
    typing_task.cancel()

    if result.get("error") or not result.get("features"):
        await call.message.edit_text("⚠️ Нет данных по маршруту.")
        await call.answer()
        return

    f = result["features"]
    expl = result.get("explanation", "")
    text = (
        f"💰 <b>{from_city} → {to_city}</b>\n\n"
        f"Медиана: <b>{f['median']:,}₽</b>  |  P10–P90: {f['p10']:,}–{f['p90']:,}₽\n"
        f"За 30 дней: {f['count_30d']} грузов\n"
    )
    if expl:
        text += f"\n🤖 {expl}"

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(
        text="🔮 Прогноз",
        callback_data=f"nlu_forecast:{from_city}:{to_city}",
    ))
    b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("nlu_forecast:"))
async def cb_nlu_forecast(call: CallbackQuery):
    _, from_city, to_city = call.data.split(":", 2)
    stop = asyncio.Event()
    typing_task = asyncio.create_task(_send_typing(call.message, stop))

    result = await _ai("/route_forecast", {"from_city": from_city, "to_city": to_city})
    stop.set()
    typing_task.cancel()

    if result.get("error") or result.get("detail"):
        await call.message.edit_text("⚠️ Недостаточно данных для прогноза.")
        await call.answer()
        return

    expl = result.get("explanation", "")
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
    await call.message.edit_text(
        f"🔮 <b>Прогноз {from_city} → {to_city}</b>\n\n{expl}",
        parse_mode="HTML",
        reply_markup=b.as_markup(),
    )
    await call.answer()


@router.message(F.text & ~F.text.startswith("/"))
async def nlu_catch_all(message: Message, state: FSMContext):
    """
    Catch-all для свободного текста.
    Пропускаем если пользователь в FSM-состоянии.
    """
    current_state = await state.get_state()
    if current_state is not None:
        return  # FSM активен — не перехватываем

    text = (message.text or "").strip()
    if len(text) < 5:
        return

    stop = asyncio.Event()
    typing_task = asyncio.create_task(_send_typing(message, stop))

    intent_data = await _ai("/parse_intent", {"text": text})
    stop.set()
    typing_task.cancel()

    if intent_data.get("error"):
        log.warning("parse_intent error: %s", intent_data["error"])
        return

    intent = intent_data.get("intent", "unknown")
    confidence = float(intent_data.get("confidence", 0))
    from_city = intent_data.get("from_city")
    to_city = intent_data.get("to_city")

    if confidence < 0.4 or intent == "unknown":
        # Не уверены — тихо пропускаем или предлагаем AI-логиста
        if confidence < 0.2:
            return
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="🤖 Спросить AI-логиста", callback_data="ask_ai_logist"))
        b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
        await message.answer(
            "Не совсем понял запрос 🤔\n"
            "Можешь спросить AI-логиста — он отвечает на любые вопросы о логистике.",
            reply_markup=b.as_markup(),
        )
        return

    # ── find_transport ────────────────────────────────────────────────────────
    if intent == "find_transport":
        weight = intent_data.get("weight_t")
        cargo = intent_data.get("cargo_type", "груз")
        body = intent_data.get("body_type")
        date_str = intent_data.get("date")

        parts = []
        if from_city and to_city:
            parts.append(f"📍 Маршрут: <b>{from_city} → {to_city}</b>")
        if weight:
            parts.append(f"⚖️ Вес: <b>{weight} т</b>")
        if cargo:
            parts.append(f"📦 Груз: <b>{cargo}</b>")
        if body:
            parts.append(f"🚛 Кузов: <b>{body}</b>")
        if date_str:
            date_label = {"today": "сегодня", "tomorrow": "завтра"}.get(date_str, date_str)
            parts.append(f"📅 Дата: <b>{date_label}</b>")

        summary = "\n".join(parts) if parts else "Маршрут уточняется"

        # Если есть маршрут — попутно получаем цену
        price_info = ""
        if from_city and to_city:
            pr = await _ai("/route_price", {"from_city": from_city, "to_city": to_city})
            if not pr.get("error") and pr.get("features"):
                f = pr["features"]
                price_info = (
                    f"\n\n💰 Рыночная цена: <b>{f['median']:,}₽</b> "
                    f"(диапазон {f['p10']:,}–{f['p90']:,}₽, {f['count_30d']} грузов за 30д)"
                )
                if pr.get("explanation"):
                    price_info += f"\n🤖 {pr['explanation']}"

        await message.answer(
            f"✅ Принял!\n\n{summary}{price_info}\n\n"
            f"Что делаем дальше?",
            parse_mode="HTML",
            reply_markup=_find_transport_kb(from_city, to_city),
        )

    # ── get_price ─────────────────────────────────────────────────────────────
    elif intent == "get_price" and from_city and to_city:
        pr = await _ai("/route_price", {"from_city": from_city, "to_city": to_city})
        if pr.get("error") or not pr.get("features"):
            await message.answer(
                f"По маршруту <b>{from_city} → {to_city}</b> пока нет данных.",
                parse_mode="HTML",
                reply_markup=_get_price_kb(from_city, to_city),
            )
            return

        f = pr["features"]
        expl = pr.get("explanation", "")
        text = (
            f"💰 <b>{from_city} → {to_city}</b>\n\n"
            f"Медиана: <b>{f['median']:,}₽</b>\n"
            f"Диапазон: {f['p10']:,} – {f['p90']:,}₽\n"
            f"За 30 дней: {f['count_30d']} грузов\n"
        )
        if f.get("avg_rate_per_km"):
            text += f"Ставка за км: {f['avg_rate_per_km']}₽\n"
        if expl:
            text += f"\n🤖 {expl}"

        await message.answer(text, parse_mode="HTML", reply_markup=_get_price_kb(from_city, to_city))

    # ── ask_question ──────────────────────────────────────────────────────────
    elif intent == "ask_question":
        stop2 = asyncio.Event()
        typing_task2 = asyncio.create_task(_send_typing(message, stop2))
        context_hint = f"{from_city}→{to_city}" if from_city and to_city else None
        result = await _ai("/ask_logist", {"question": text, "context": context_hint})
        stop2.set()
        typing_task2.cancel()

        answer = result.get("answer", "").strip()
        if not answer or result.get("error"):
            answer = "⚠️ AI временно недоступен. Попробуй позже."

        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="🔄 Ещё вопрос", callback_data="ask_ai_logist"))
        b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
        await message.answer(
            f"🤖 <b>AI-логист</b>\n\n{answer}",
            parse_mode="HTML",
            reply_markup=b.as_markup(),
        )

    # ── place_cargo ───────────────────────────────────────────────────────────
    elif intent == "place_cargo":
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="📦 Разместить груз", callback_data="add_cargo"))
        b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
        await message.answer(
            "📦 Размещаем груз! Нажми кнопку ниже и заполни форму.",
            reply_markup=b.as_markup(),
        )
