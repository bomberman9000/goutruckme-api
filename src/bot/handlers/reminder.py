from datetime import datetime, timedelta
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from src.core.database import async_session
from src.core.models import Reminder
from src.core.logger import logger

router = Router()

@router.message(Command("remind"))
async def cmd_remind(message: Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("Использование: /remind 30m Текст напоминания\n\nВремя: 10m, 1h, 1d")
        return

    time_str = args[1]
    text = args[2]

    try:
        if time_str.endswith("m"):
            delta = timedelta(minutes=int(time_str[:-1]))
        elif time_str.endswith("h"):
            delta = timedelta(hours=int(time_str[:-1]))
        elif time_str.endswith("d"):
            delta = timedelta(days=int(time_str[:-1]))
        else:
            await message.answer("❌ Неверный формат времени")
            return
    except ValueError:
        await message.answer("❌ Неверный формат времени")
        return

    remind_at = datetime.utcnow() + delta

    async with async_session() as session:
        reminder = Reminder(user_id=message.from_user.id, text=text, remind_at=remind_at)
        session.add(reminder)
        await session.commit()

    await message.answer(f"✅ Напомню через {time_str}:\n{text}")
    logger.info(f"Reminder set for {message.from_user.id}: {text}")

@router.message(Command("reminders"))
async def cmd_reminders(message: Message):
    async with async_session() as session:
        result = await session.execute(
            select(Reminder)
            .where(Reminder.user_id == message.from_user.id)
            .where(Reminder.is_sent == False)
            .order_by(Reminder.remind_at)
            .limit(10)
        )
        reminders = result.scalars().all()

    if not reminders:
        await message.answer("📭 Нет активных напоминаний")
        return

    text = "⏰ Твои напоминания:\n\n"
    for r in reminders:
        text += f"• {r.remind_at.strftime('%d.%m %H:%M')} — {r.text[:30]}\n"

    await message.answer(text)
