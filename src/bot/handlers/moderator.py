"""
src/bot/handlers/moderator.py
Авто-модератор групповых чатов: анализирует каждое сообщение через AI,
удаляет мошеннические посты, предупреждает подозрительных.
"""
import logging
import os

import aiohttp
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

log = logging.getLogger(__name__)
router = Router()

AI_ENGINE_URL = os.environ.get("AI_ENGINE_URL", "http://localhost:8010")
AI_ENGINE_TOKEN = os.environ.get("AI_ENGINE_TOKEN", "")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))

# ID групп, которые модерируем. Пустой список = все группы.
# Формат: "-1001234567890,-1009876543210"
_raw = os.environ.get("MODERATED_GROUP_IDS", "")
MODERATED_GROUP_IDS: set[int] = {int(x.strip()) for x in _raw.split(",") if x.strip()}

# Пороги
SCORE_WARN = float(os.environ.get("MOD_SCORE_WARN", "0.5"))   # предупреждение
SCORE_BAN  = float(os.environ.get("MOD_SCORE_BAN",  "0.75"))  # удаление + уведомление


async def _moderate(text: str, user_id: int | None, username: str | None,
                    chat_id: int | None, message_id: int | None) -> dict:
    payload = {
        "text": text,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "message_id": message_id,
    }
    if AI_ENGINE_TOKEN:
        payload["token"] = AI_ENGINE_TOKEN
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=50)) as s:
            r = await s.post(f"{AI_ENGINE_URL}/moderate", json=payload)
            if r.status == 200:
                return await r.json()
    except Exception as e:
        log.warning("moderate request failed: %s", e)
    return {"score": 0.0, "verdict": "clean", "reason": "", "flags": []}


def _is_moderated(chat_id: int) -> bool:
    if not MODERATED_GROUP_IDS:
        return True  # мониторим все группы
    return chat_id in MODERATED_GROUP_IDS


# ─── Основной хендлер — каждое сообщение в группе ────────────────────────────

@router.message(F.chat.type.in_({"group", "supergroup"}), F.text)
async def group_message_handler(message: Message, bot: Bot):
    chat_id = message.chat.id
    if not _is_moderated(chat_id):
        return

    text = message.text or ""
    if len(text) < 20:  # слишком короткие пропускаем
        return

    user = message.from_user
    user_id  = user.id if user else None
    username = user.username if user else None

    result = await _moderate(text, user_id, username, chat_id, message.message_id)
    score   = result.get("score", 0.0)
    verdict = result.get("verdict", "clean")
    reason  = result.get("reason", "")
    flags   = result.get("flags", [])

    if score < SCORE_WARN:
        return

    log.info("mod[%s] user=%s score=%.2f verdict=%s", chat_id, username, score, verdict)

    if score >= SCORE_BAN:
        # Удаляем сообщение
        try:
            await message.delete()
        except Exception as e:
            log.warning("cannot delete msg: %s", e)

        # Уведомляем пользователя
        try:
            await bot.send_message(
                chat_id,
                f"⛔️ <b>Сообщение удалено</b>\n"
                f"Причина: {reason or 'нарушение правил платформы'}",
                parse_mode="HTML",
            )
        except Exception:
            pass

        # Уведомляем админа
        if ADMIN_CHAT_ID:
            flags_str = "\n".join(f"  • {f}" for f in flags) if flags else "  —"
            try:
                await bot.send_message(
                    ADMIN_CHAT_ID,
                    f"🚨 <b>Авто-модератор удалил сообщение</b>\n\n"
                    f"👤 @{username or user_id} | чат: {message.chat.title}\n"
                    f"📊 Score: <b>{score:.2f}</b> ({verdict})\n"
                    f"❗️ {reason}\n\n"
                    f"Признаки:\n{flags_str}\n\n"
                    f"💬 <i>{text[:400]}</i>",
                    parse_mode="HTML",
                )
            except Exception as e:
                log.warning("cannot notify admin: %s", e)

    elif score >= SCORE_WARN:
        # Предупреждение без удаления
        try:
            await message.reply(
                f"⚠️ <b>Внимание!</b> Сообщение содержит признаки нарушений.\n"
                f"Пожалуйста, убедитесь, что ваше предложение соответствует правилам платформы.",
                parse_mode="HTML",
            )
        except Exception:
            pass

        if ADMIN_CHAT_ID:
            try:
                await bot.send_message(
                    ADMIN_CHAT_ID,
                    f"⚠️ <b>Подозрительное сообщение</b>\n\n"
                    f"👤 @{username or user_id} | чат: {message.chat.title}\n"
                    f"📊 Score: <b>{score:.2f}</b>\n"
                    f"❗️ {reason}\n\n"
                    f"💬 <i>{text[:400]}</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass


# ─── /modstats — статистика для админа ───────────────────────────────────────

@router.message(Command("modstats"), F.chat.type == "private")
async def cmd_modstats(message: Message):
    """Показать последние 5 пойманных нарушений."""
    if not message.from_user or str(message.from_user.id) != os.environ.get("ADMIN_ID", ""):
        return

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            params = {"token": AI_ENGINE_TOKEN} if AI_ENGINE_TOKEN else {}
            r = await s.get(f"{AI_ENGINE_URL}/mod/log", params=params)
            if r.status != 200:
                await message.answer("Нет данных.")
                return
            data = await r.json()
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        return

    items = data.get("items", [])
    if not items:
        await message.answer("📋 Нарушений не зафиксировано.")
        return

    lines = ["📋 <b>Последние нарушения:</b>\n"]
    for it in items[:5]:
        lines.append(
            f"• score={it.get('score', '?')} | @{it.get('username', '?')}\n"
            f"  {it.get('reason', '')}\n"
            f"  <i>{str(it.get('text', ''))[:100]}</i>\n"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")
