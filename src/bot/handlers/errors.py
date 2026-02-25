from aiogram import Router
from aiogram.types import ErrorEvent
from src.core.config import settings
from src.core.logger import logger
from src.bot.bot import bot

router = Router()


@router.error()
async def error_handler(event: ErrorEvent):
    logger.error("Error: %s", event.exception, exc_info=event.exception)

    # Снимаем «зависание» у callback — иначе клиент крутит загрузку
    cb = getattr(event.update, "callback_query", None)
    if cb is not None:
        try:
            await cb.answer(
                "Произошла ошибка. Попробуй ещё раз.", show_alert=True
            )
        except Exception:
            pass

    if settings.admin_id:
        try:
            text = str(event.exception)[:500]
            await bot.send_message(
                settings.admin_id,
                f"🚨 <b>Ошибка:</b>\n\n<code>{text}</code>"
            )
        except Exception:
            pass
