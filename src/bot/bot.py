from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InaccessibleMessage
from aiogram.exceptions import TelegramBadRequest
from src.core.config import settings

bot = Bot(
    token=settings.bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)


async def _inaccessible_edit_text(self, text: str, **kwargs):
    chat = getattr(self, "chat", None)
    if not chat:
        return None
    try:
        return await bot.send_message(
            chat.id,
            text,
            reply_markup=kwargs.get("reply_markup"),
            disable_web_page_preview=kwargs.get("disable_web_page_preview", True),
        )
    except TelegramBadRequest:
        return None

async def _inaccessible_answer(self, text: str, **kwargs):
    chat = getattr(self, "chat", None)
    if not chat:
        return None
    try:
        return await bot.send_message(chat.id, text, **kwargs)
    except TelegramBadRequest:
        return None

def _patch_inaccessible_message():
    candidates = []
    try:
        candidates.append(InaccessibleMessage)
    except Exception:
        pass
    try:
        from aiogram.types.inaccessible_message import InaccessibleMessage as Inacc2
        candidates.append(Inacc2)
    except Exception:
        pass
    try:
        from aiogram.types.message import InaccessibleMessage as Inacc3
        candidates.append(Inacc3)
    except Exception:
        pass

    for cls in candidates:
        try:
            cls.edit_text = _inaccessible_edit_text
            cls.answer = _inaccessible_answer
        except Exception:
            pass

_patch_inaccessible_message()
