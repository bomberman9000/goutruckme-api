from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from src.core.services.watchdog import watchdog


class WatchdogMiddleware(BaseMiddleware):
    """Middleware для отслеживания активности бота"""

    async def __call__(
        self,
        handler: Callable[
            [Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]
        ],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        watchdog.heartbeat()

        try:
            return await handler(event, data)
        except Exception as e:
            watchdog.record_error(str(e))
            raise
