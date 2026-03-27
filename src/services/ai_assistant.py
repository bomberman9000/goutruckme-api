"""Telegram bot handler for /ai command — interactive AI assistant."""
from __future__ import annotations

import hashlib
import json

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.bot.states import AIAssistant
from src.core.logger import logger

router = Router()

_PDF_CACHE_TTL = 3600  # 1 hour


async def _cache_doc_text(text: str, doc_type: str) -> str:
    """Store doc text in Redis, return cache key hash."""
    key_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    redis_key = f"ai_doc_text:{key_hash}"
    try:
        from src.core.redis import get_redis
        redis = await get_redis()
        await redis.set(
            redis_key,
            json.dumps({"text": text, "type": doc_type}, ensure_ascii=False),
            ex=_PDF_CACHE_TTL,
        )
    except Exception as e:
        logger.warning("ai_assistant.cache_doc error=%s", e)
    return key_hash


async def _get_cached_doc(key_hash: str) -> tuple[str, str] | None:
    """Retrieve cached doc text. Returns (text, doc_type) or None."""
    try:
        from src.core.redis import get_redis
        redis = await get_redis()
        raw = await redis.get(f"ai_doc_text:{key_hash}")
        if raw:
            data = json.loads(raw)
            return data["text"], data.get("type", "документ")
    except Exception as e:
        logger.warning("ai_assistant.get_doc error=%s", e)
    return None

_MODES = {
    "logist":    ("📦 Логист",   "Опиши заявку на перевозку — разберу маршрут, груз, риски"),
    "antifraud": ("🛡 Антифрод", "Вставь текст заявки — оценю риски и дам рекомендацию"),
    "docs":      ("📄 Документ", (
        "📋 <b>Заполни данные для документа:</b>\n\n"
        "<b>Тип документа:</b> договор / заявка / акт\n"
        "<b>Маршрут:</b> откуда → куда\n"
        "<b>Груз:</b> наименование, вес (т), объём (м³)\n"
        "<b>Дата погрузки:</b>\n"
        "<b>Ставка:</b> сумма, НДС\n"
        "<b>Оплата:</b> условия\n"
        "\n"
        "<b>Грузоотправитель:</b> название, ИНН (или имя)\n"
        "<b>Грузополучатель:</b>\n"
        "<b>Перевозчик:</b>\n"
        "<b>Водитель:</b> ФИО, паспорт, телефон\n"
        "<b>ТС:</b> марка, гос. номер, прицеп\n"
        "\n"
        "Напиши всё что знаешь — пропущенные поля AI заполнит шаблонно."
    )),
    "price":     ("💰 Цена",     "Укажи маршрут и вес — рассчитаю справедливую ставку\n\nФормат: <b>Москва — Екатеринбург, 20 тонн, тент</b>"),
}


def _mode_kb() -> InlineKeyboardMarkup:
    items = [
        InlineKeyboardButton(text=label, callback_data=f"ai_mode:{key}")
        for key, (label, _) in _MODES.items()
    ]
    # 2 buttons per row
    rows = [items[i:i + 2] for i in range(0, len(items), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="ai_cancel"),
    ]])


# ---------------------------------------------------------------------------
# /ai — entry point
# ---------------------------------------------------------------------------

@router.message(Command("ai"))
async def cmd_ai(message: Message, state: FSMContext) -> None:
    await state.set_state(AIAssistant.choose_mode)
    await message.answer(
        "🤖 <b>AI-ассистент ГрузПоток</b>\n\nВыбери режим:",
        parse_mode="HTML",
        reply_markup=_mode_kb(),
    )


# ---------------------------------------------------------------------------
# Mode selection
# ---------------------------------------------------------------------------

@router.callback_query(AIAssistant.choose_mode, F.data.startswith("ai_mode:"))
async def choose_mode(cb: CallbackQuery, state: FSMContext) -> None:
    mode = cb.data.split(":")[1]
    if mode not in _MODES:
        await cb.answer("Неизвестный режим")
        return

    label, hint = _MODES[mode]
    hint_text = hint if isinstance(hint, str) else hint
    await state.update_data(mode=mode)
    await state.set_state(AIAssistant.wait_text)
    await cb.message.edit_text(
        f"Режим: <b>{label}</b>\n\n{hint_text}",
        parse_mode="HTML",
        reply_markup=_cancel_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "ai_cancel")
async def ai_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("❌ Отменено")
    await cb.answer()


# ---------------------------------------------------------------------------
# Text input → Kimi call → formatted response
# ---------------------------------------------------------------------------

@router.message(AIAssistant.wait_text)
async def process_ai_text(message: Message, state: FSMContext) -> None:
    from src.services.ai_kimi import kimi_service
    from src.services.ai_limits import check_and_increment, is_premium_user, log_ai_request

    data = await state.get_data()
    mode = data.get("mode", "logist")
    user_id = message.from_user.id
    text = (message.text or "").strip()

    if not text:
        await message.answer("Отправь текст для анализа")
        return

    # Check daily limit
    premium = await is_premium_user(user_id)
    allowed, remaining = await check_and_increment(user_id, is_premium=premium)
    if not allowed:
        await state.clear()
        await message.answer(
            "⚠️ <b>Дневной лимит исчерпан</b> (5 запросов/день).\n\n"
            "Оформи Premium для безлимитного доступа к AI.",
            parse_mode="HTML",
        )
        return

    thinking = await message.answer("⏳ Анализирую...")

    try:
        if mode == "logist":
            result = await kimi_service.logist_mode(text)
            reply = _fmt_logist(result)
            pdf_kb = None
        elif mode == "antifraud":
            result = await kimi_service.antifraud_mode(text)
            reply = _fmt_antifraud(result)
            pdf_kb = None
        elif mode == "price":
            result = await _price_from_text(text, user_id)
            reply = _fmt_price(result)
            pdf_kb = None
        else:
            result = await kimi_service.docs_mode(text)
            reply = _fmt_docs(result)
            doc_text = result.get("text") or ""
            doc_type = result.get("type", "документ")
            if doc_text:
                key_hash = await _cache_doc_text(doc_text, doc_type)
                pdf_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📥 Скачать PDF", callback_data=f"ai_pdf:{key_hash}"),
                ]])
            else:
                pdf_kb = None

        await log_ai_request(user_id, mode, text, result)

        await thinking.delete()
        await message.answer(reply, parse_mode="HTML", reply_markup=pdf_kb)

        if not premium and remaining > 0:
            await message.answer(f"💡 Осталось AI-запросов сегодня: <b>{remaining}</b>", parse_mode="HTML")

    except Exception as e:
        logger.error("ai_assistant.error user=%d mode=%s error=%s", user_id, mode, e)
        await thinking.delete()
        await message.answer("❌ Ошибка AI. Попробуй позже.")

    await state.clear()


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _fmt_logist(r: dict) -> str:
    lines = ["📦 <b>AI-анализ заявки</b>"]
    if r.get("route"):   lines.append(f"📍 Маршрут: {r['route']}")
    if r.get("cargo"):   lines.append(f"📦 Груз: {r['cargo']}")
    if r.get("weight"):  lines.append(f"⚖️ Вес: {r['weight']}")
    if r.get("volume"):  lines.append(f"📐 Объём: {r['volume']}")
    if r.get("vehicle"): lines.append(f"🚛 Транспорт: {r['vehicle']}")
    if r.get("date"):    lines.append(f"📅 Дата: {r['date']}")

    risks = r.get("risks") or []
    if risks:
        lines.append("\n⚠️ <b>Риски:</b>")
        lines.extend(f"  • {x}" for x in risks[:3])

    questions = r.get("questions") or []
    if questions:
        lines.append("\n❓ <b>Уточнить:</b>")
        lines.extend(f"  • {q}" for q in questions[:3])

    return "\n".join(lines)


def _fmt_antifraud(r: dict) -> str:
    score = int(r.get("risk_score") or 0)
    rec = r.get("recommendation", "accept")

    if score >= 75:
        emoji = "🔴"
    elif score >= 40:
        emoji = "🟡"
    else:
        emoji = "🟢"

    rec_labels = {
        "accept":  "✅ Принять",
        "caution": "⚠️ Проверить",
        "reject":  "🚫 Отклонить",
    }

    lines = [
        f"{emoji} <b>Антифрод-анализ</b>",
        f"Риск: <b>{score}/100</b>",
        f"Решение: {rec_labels.get(rec, rec)}",
    ]

    flags = r.get("flags") or []
    if flags:
        lines.append(f"Флаги: <i>{', '.join(str(f) for f in flags[:5])}</i>")

    explanation = r.get("explanation") or ""
    if explanation:
        lines.append(f"\n{explanation[:500]}")

    return "\n".join(lines)


async def _price_from_text(text: str, user_id: int) -> dict:
    """Parse 'Москва — Екатеринбург, 20 тонн, тент' and call price_mode."""
    import re
    # Try to extract: city1 — city2, weight, vehicle
    m = re.search(
        r"([а-яА-ЯёЁ\s\-]+?)\s*[—\-–]+\s*([а-яА-ЯёЁ\s]+?)"
        r"[,\s]+(\d+(?:[.,]\d+)?)\s*(?:т\b|тонн|тн)\b"
        r"(?:[,\s]+([а-яА-ЯёЁ\s]+))?",
        text,
        re.IGNORECASE,
    )
    if m:
        from_city = m.group(1).strip()
        to_city = m.group(2).strip()
        weight = float(m.group(3).replace(",", "."))
        vehicle = (m.group(4) or "тент").strip()
    else:
        # Fallback: send full text to logist first, extract fields
        parsed = await kimi_service.logist_mode(text)
        from_city = parsed.get("route", "").split("—")[0].strip() or "?"
        to_city = parsed.get("route", "").split("—")[-1].strip() or "?"
        weight_raw = parsed.get("weight", "1").replace("т", "").replace("тонн", "").strip()
        try:
            weight = float(weight_raw.split()[0]) if weight_raw else 1.0
        except ValueError:
            weight = 1.0
        vehicle = parsed.get("vehicle", "тент")

    market_data = None
    distance_km = None
    try:
        from src.core.services.price_predict import predict_route_price
        market_data = await predict_route_price(from_city, to_city)
    except Exception:
        pass
    try:
        from src.core.services.geo_service import get_geo_service
        geo = await get_geo_service().resolve_route(from_city, to_city)
        if geo:
            distance_km = geo.distance_km
    except Exception:
        pass

    return await kimi_service.price_mode(
        from_city=from_city,
        to_city=to_city,
        weight=weight,
        vehicle=vehicle,
        distance_km=distance_km,
        market_data=market_data,
    )


def _fmt_price(r: dict) -> str:
    from_city = r.get("from_city", "?")
    to_city = r.get("to_city", "?")
    rec = r.get("recommended_price")
    lo = r.get("min_price")
    hi = r.get("max_price")
    per_km = r.get("price_per_km")
    conf = r.get("confidence", "medium")
    dist = r.get("distance_km")

    conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "🟡")

    lines = [f"💰 <b>AI-оценка ставки</b>", f"📍 {from_city} → {to_city}"]
    if dist:
        lines.append(f"📏 {dist} км")
    lines.append(f"⚖️ {r.get('weight', '?')} т · {r.get('vehicle', '')}")
    lines.append("")

    if rec:
        lines.append(f"💵 Рекомендуемая: <b>{rec:,} ₽</b>".replace(",", " "))
    if lo and hi:
        lines.append(f"📊 Диапазон: {lo:,} – {hi:,} ₽".replace(",", " "))
    if per_km:
        lines.append(f"🔢 За км: {per_km:,} ₽/км".replace(",", " "))

    lines.append(f"\n{conf_emoji} Уверенность: {conf}")

    explanation = r.get("explanation") or ""
    if explanation:
        lines.append(f"\n{explanation}")

    market = r.get("market_comment") or ""
    if market:
        lines.append(f"\n📈 {market}")

    return "\n".join(lines)


def _fmt_docs(r: dict) -> str:
    doc_type = r.get("type", "документ")
    text = (r.get("text") or "").strip()
    if not text:
        return "❌ Не удалось сгенерировать документ"
    preview = text[:3500]
    return f"📄 <b>Документ: {doc_type}</b>\n\n<pre>{preview}</pre>"


# ---------------------------------------------------------------------------
# PDF download handler
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("ai_pdf:"))
async def send_pdf(cb: CallbackQuery) -> None:
    key_hash = cb.data.split(":")[1]
    cached = await _get_cached_doc(key_hash)

    if not cached:
        await cb.answer("Документ устарел. Сгенерируй заново через /ai", show_alert=True)
        return

    doc_text, doc_type = cached
    await cb.answer("Генерирую PDF...")

    try:
        from src.core.documents import generate_application_pdf
        pdf_bytes = generate_application_pdf(doc_text)
        safe_type = doc_type.replace(" ", "_").replace("/", "-")
        filename = f"gotruckme_{safe_type}.pdf"
        await cb.message.answer_document(
            BufferedInputFile(pdf_bytes, filename=filename),
            caption=f"📄 {doc_type}",
        )
    except Exception as e:
        logger.error("ai_assistant.pdf error=%s", e)
        await cb.message.answer("❌ Ошибка генерации PDF. Попробуй позже.")
