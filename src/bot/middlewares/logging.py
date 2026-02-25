import asyncio
from typing import Any, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select
from src.core.logger import logger
from src.core.redis import get_redis
from src.core.database import async_session
from src.core.models import User


async def _upsert_user_and_log(event: Message | CallbackQuery) -> None:
    """Фон: сохранить юзера в БД и счётчики (не блокирует callback)."""
    try:
        user = event.from_user
        redis = await get_redis()
        async with async_session() as session:
            result = await session.execute(select(User).where(User.id == user.id))
            db_user = result.scalar_one_or_none()
            if not db_user:
                db_user = User(
                    id=user.id,
                    username=user.username,
                    full_name=user.full_name,
                )
                session.add(db_user)
                await session.commit()
                logger.info("New user: %s %s", user.id, user.full_name)
        if isinstance(event, Message) and event.text:
            logger.info("[%s] %s: %s", user.id, user.full_name, event.text)
            await redis.hincrby("stats:users", str(user.id), 1)
            await redis.incr("stats:messages")
        elif isinstance(event, CallbackQuery):
            logger.info("[%s] callback: %s", user.id, event.data)
            await redis.incr("stats:callbacks")
    except Exception as e:
        logger.warning("LoggingMiddleware background: %s", e)


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable,
        event: Message | CallbackQuery,
        data: Dict,
    ) -> Any:
        if isinstance(event, CallbackQuery):
            # Сначала handler (cb.answer() быстро), логируем в фоне
            result = await handler(event, data)
            asyncio.create_task(_upsert_user_and_log(event))
            return result
        # Сообщения: логируем до handler
        await _upsert_user_and_log(event)
        return await handler(event, data)
