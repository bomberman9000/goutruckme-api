from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select, func
from src.core.ai_diag import explain_health
from src.core.config import settings
from src.core.database import async_session
from src.core.models import User, Cargo, Feedback
from src.core.redis import get_redis
from src.core.services.watchdog import watchdog
from src.bot.bot import bot

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id == settings.admin_id


@router.message(Command("health"))
async def cmd_health(message: Message):
    """Проверка здоровья системы (только для админа)"""
    if not is_admin(message.from_user.id):
        return

    msg = await message.answer("⏳ Проверяю...")

    health = await watchdog.check_health()
    text = watchdog.format_status(health)

    await msg.edit_text(text, parse_mode="HTML")


@router.message(Command("health_ai"))
async def cmd_health_ai(message: Message):
    """Проверка здоровья системы с расшифровкой причин (только для админа)"""
    if not is_admin(message.from_user.id):
        return

    msg = await message.answer("⏳ Собираю метрики и диагноз...")

    health = await watchdog.check_health()
    text = watchdog.format_status(health) + "\n\n" + explain_health(health)

    await msg.edit_text(text, parse_mode="HTML")


@router.message(Command("errors"))
async def cmd_errors(message: Message):
    """Последние ошибки (только для админа)"""
    if not is_admin(message.from_user.id):
        return

    if not watchdog.checks:
        await message.answer("✅ Ошибок нет")
        return

    text = "🔴 <b>Последние ошибки:</b>\n\n"
    for check in watchdog.checks[-10:]:
        if check["type"] == "error":
            text += f"• {check['time'][:19]}\n  {check['message'][:100]}\n\n"

    await message.answer(text, parse_mode="HTML")

@router.message(Command("stats"))
async def admin_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    redis = await get_redis()
    async with async_session() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        cargos = await session.scalar(select(func.count()).select_from(Cargo))
        feedback = await session.scalar(select(func.count()).select_from(Feedback))
    
    messages_count = await redis.get("stats:messages") or 0
    callbacks_count = await redis.get("stats:callbacks") or 0
    
    text = f"📊 <b>Статистика</b>\n\n"
    text += f"👥 Пользователей: {users}\n"
    text += f"📦 Грузов: {cargos}\n"
    text += f"💬 Сообщений: {messages_count}\n"
    text += f"🔘 Callbacks: {callbacks_count}\n"
    text += f"📝 Отзывов: {feedback}"
    
    await message.answer(text)

@router.message(Command("users"))
async def admin_users(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    async with async_session() as session:
        result = await session.execute(select(User).limit(20))
        users = result.scalars().all()
    
    text = "👥 <b>Пользователи:</b>\n\n"
    for u in users:
        status = "🚫" if u.is_banned else "✅" if u.is_verified else "👤"
        text += f"{status} {u.id} | {u.full_name[:20]}\n"
    
    await message.answer(text)

@router.message(Command("ban"))
async def ban_user(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /ban USER_ID")
        return
    
    try:
        user_id = int(args[1])
    except:
        await message.answer("❌ Неверный ID")
        return
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            user.is_banned = True
            await session.commit()
            await message.answer(f"🚫 Пользователь {user_id} забанен")
        else:
            await message.answer("❌ Пользователь не найден")

@router.message(Command("unban"))
async def unban_user(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /unban USER_ID")
        return
    
    try:
        user_id = int(args[1])
    except:
        await message.answer("❌ Неверный ID")
        return
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            user.is_banned = False
            await session.commit()
            await message.answer(f"✅ Пользователь {user_id} разбанен")
        else:
            await message.answer("❌ Пользователь не найден")

@router.message(Command("broadcast"))
async def broadcast(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    if not message.reply_to_message:
        await message.answer("Ответь на сообщение которое нужно разослать")
        return
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.is_banned == False))
        users = result.scalars().all()
    
    sent = 0
    for user in users:
        try:
            await message.reply_to_message.copy_to(user.id)
            sent += 1
        except:
            pass
    
    await message.answer(f"✅ Отправлено {sent}/{len(users)} пользователям")

@router.message(Command("feedback_list"))
async def feedback_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    async with async_session() as session:
        result = await session.execute(
            select(Feedback).order_by(Feedback.created_at.desc()).limit(10)
        )
        feedbacks = result.scalars().all()
    
    if not feedbacks:
        await message.answer("📭 Нет отзывов")
        return
    
    text = "📝 <b>Последние отзывы:</b>\n\n"
    for fb in feedbacks:
        text += f"👤 {fb.user_id}\n{fb.message[:100]}\n\n"
    
    await message.answer(text)
