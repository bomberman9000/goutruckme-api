from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from src.bot.states import FeedbackForm
from src.bot.keyboards import confirm_kb, main_menu
from src.core.redis import get_redis
from src.core.logger import logger

router = Router()

@router.callback_query(F.data == "feedback")
async def start_feedback(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.message.edit_text("🆘 Поддержка\n\nОпиши проблему или оставь отзыв:")
    except TelegramBadRequest:
        pass
    await state.set_state(FeedbackForm.message)
    await cb.answer()

@router.message(FeedbackForm.message)
async def get_message(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await message.answer(f"Отправить сообщение?\n\n{message.text}", reply_markup=confirm_kb())
    await state.set_state(FeedbackForm.confirm)

@router.callback_query(FeedbackForm.confirm, F.data == "yes")
async def confirm_yes(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    redis = await get_redis()
    await redis.lpush("feedback", f"{cb.from_user.full_name}: {data['text']}")
    logger.info(f"Feedback from {cb.from_user.id}: {data['text']}")
    try:
        await cb.message.edit_text("✅ Спасибо! Отправлено.", reply_markup=main_menu())
    except TelegramBadRequest:
        pass
    await state.clear()
    await cb.answer()

@router.callback_query(FeedbackForm.confirm, F.data == "no")
async def confirm_no(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.message.edit_text("❌ Отменено", reply_markup=main_menu())
    except TelegramBadRequest:
        pass
    await state.clear()
    await cb.answer()

@router.callback_query(F.data == "about")
async def about(cb: CallbackQuery):
    try:
        await cb.message.edit_text("🤖 TG Bot Template\n\nFastAPI + Aiogram + Redis", reply_markup=main_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()
