from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

from src.bot.states import LegalCheck
from src.bot.keyboards import back_menu
from src.core.services.legal_check import (
    full_legal_check, format_legal_check,
    full_legal_check_pro, format_legal_check_pro,
)
from src.core.logger import logger

router = Router()


@router.message(Command("check"))
async def cmd_check(message: Message, state: FSMContext):
    """Команда /check или /check ИНН"""
    parts = message.text.split()

    if len(parts) > 1:
        inn = parts[1].strip()
        await do_check(message, inn, user_id=message.from_user.id)
    else:
        await message.answer(
            "🔍 <b>Проверка контрагента</b>\n\n"
            "Введите ИНН компании (10 или 12 цифр):"
        )
        await state.set_state(LegalCheck.inn)


@router.callback_query(F.data == "legal_check")
async def start_legal_check(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        "🔍 <b>Проверка контрагента</b>\n\n"
        "Введите ИНН компании (10 или 12 цифр):"
    )
    await state.set_state(LegalCheck.inn)
    await cb.answer()


@router.message(LegalCheck.inn)
async def process_inn(message: Message, state: FSMContext):
    inn = message.text.strip().replace(" ", "")

    if not inn.isdigit() or len(inn) not in (10, 12):
        await message.answer("❌ ИНН должен содержать 10 или 12 цифр")
        return

    await state.clear()
    await do_check(message, inn, user_id=message.from_user.id)


async def do_check(message: Message, inn: str, user_id: int | None = None):
    """Выполняет проверку по ИНН и отправляет результат."""
    from src.services.ai_limits import is_premium_user
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    is_pro = await is_premium_user(user_id) if user_id else False

    msg = await message.answer(
        "⏳ Проверяю контрагента...\n\n"
        + ("🔍 Расширенная проверка (Pro)..." if is_pro else "Это займёт 10–30 секунд")
    )

    try:
        if is_pro:
            result = await full_legal_check_pro(inn)
            text = format_legal_check_pro(result)
        else:
            result = await full_legal_check(inn)
            text = format_legal_check(result)
            text += (
                "\n\n💎 <b>Светофор Pro</b> — учредители, РНП ФАС, реестр залогов "
                "и детали арбитража доступны в подписке."
            )

        kb = back_menu()
        if not is_pro:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Получить Pro", callback_data="show_premium")],
                *back_menu().inline_keyboard,
            ])
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.exception("Legal check error")
        await msg.edit_text(
            f"❌ Ошибка проверки: {e}\n\nПопробуйте позже.",
            reply_markup=back_menu(),
        )


@router.callback_query(F.data.startswith("check_company_"))
async def check_company_by_id(cb: CallbackQuery):
    """Проверка компании из профиля по company_id."""
    from src.core.database import async_session
    from src.core.models import CompanyDetails
    from sqlalchemy import select

    try:
        company_id = int(cb.data.split("_")[-1])
    except (ValueError, IndexError):
        await cb.answer("❌ Ошибка", show_alert=True)
        return

    async with async_session() as session:
        company = await session.scalar(
            select(CompanyDetails).where(CompanyDetails.id == company_id)
        )
        if not company or not company.inn:
            await cb.answer("❌ ИНН компании не указан", show_alert=True)
            return
        inn = company.inn

    await cb.answer("⏳ Проверяю...")
    await do_check(cb.message, inn, user_id=cb.from_user.id)
