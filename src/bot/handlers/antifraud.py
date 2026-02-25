from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, func
from random import randint
from src.bot.states import VerifyForm, ReportForm
from src.bot.keyboards import main_menu, back_menu
from src.core.database import async_session
from src.core.models import User, Report, ReportType, Rating, Cargo, CargoStatus, UserProfile, VerificationStatus
from src.core.config import settings
from src.core.logger import logger
from src.bot.bot import bot

router = Router()

def antifraud_menu():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✅ Верификация", callback_data="verify"))
    b.row(InlineKeyboardButton(text="🚨 Пожаловаться", callback_data="report"))
    b.row(InlineKeyboardButton(text="🛡 Мой рейтинг доверия", callback_data="trust_score"))
    b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
    return b.as_markup()

def report_type_kb():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🚫 Мошенничество", callback_data="rtype_fraud"))
    b.row(InlineKeyboardButton(text="📢 Спам", callback_data="rtype_spam"))
    b.row(InlineKeyboardButton(text="❌ Фейковый груз", callback_data="rtype_fake_cargo"))
    b.row(InlineKeyboardButton(text="💸 Неоплата", callback_data="rtype_no_payment"))
    b.row(InlineKeyboardButton(text="❓ Другое", callback_data="rtype_other"))
    b.row(InlineKeyboardButton(text="◀️ Отмена", callback_data="menu"))
    return b.as_markup()

@router.callback_query(F.data == "antifraud")
async def antifraud_handler(cb: CallbackQuery):
    text = "🛡 <b>Безопасность</b>\n\n"
    text += "• Верификация подтверждает твой телефон\n"
    text += "• Жалобы помогают бороться с мошенниками\n"
    text += "• Рейтинг доверия влияет на видимость"
    try:
        await cb.message.edit_text(text, reply_markup=antifraud_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data == "verify")
async def start_verify(cb: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == cb.from_user.id))
        user = result.scalar_one_or_none()
        
        if user and user.is_verified:
            await cb.answer("✅ Ты уже верифицирован!", show_alert=True)
            return
    
    await cb.message.edit_text("📱 Введи номер телефона:\n\nФормат: +79001234567")
    await state.set_state(VerifyForm.phone)
    await cb.answer()

@router.message(VerifyForm.phone)
async def verify_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    
    if not phone.startswith("+") or len(phone) < 10:
        await message.answer("❌ Неверный формат. Пример: +79001234567")
        return
    
    code = str(randint(1000, 9999))
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == message.from_user.id))
        user = result.scalar_one_or_none()
        if user:
            user.phone = phone
            user.verification_code = code
            await session.commit()
    
    await state.update_data(phone=phone, code=code)
    
    await message.answer(
        f"📨 Код верификации: <code>{code}</code>\n\n"
        f"(В реальном боте код отправляется по SMS)\n\n"
        f"Введи код:"
    )
    await state.set_state(VerifyForm.code)
    logger.info(f"Verification code {code} for {message.from_user.id}")

@router.message(VerifyForm.code)
async def verify_code(message: Message, state: FSMContext):
    data = await state.get_data()
    
    if message.text.strip() != data['code']:
        await message.answer("❌ Неверный код. Попробуй ещё:")
        return
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == message.from_user.id))
        user = result.scalar_one_or_none()
        if user:
            user.is_verified = True
            user.verification_code = None
            user.trust_score = min(100, user.trust_score + 20)
            await session.commit()
    
    await state.clear()
    await message.answer("✅ Телефон подтверждён!\n\n+20 к рейтингу доверия", reply_markup=main_menu())
    logger.info(f"User {message.from_user.id} verified")

@router.callback_query(F.data == "trust_score")
async def show_trust_score(cb: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == cb.from_user.id))
        user = result.scalar_one_or_none()
        
        if not user:
            await cb.answer("❌ Профиль не найден", show_alert=True)
            return
        
        avg_rating = await session.scalar(
            select(func.avg(Rating.score)).where(Rating.to_user_id == cb.from_user.id)
        )

        profile = await session.scalar(select(UserProfile).where(UserProfile.user_id == cb.from_user.id))
        
        completed = await session.scalar(
            select(func.count()).select_from(Cargo)
            .where(
                (Cargo.owner_id == cb.from_user.id) | (Cargo.carrier_id == cb.from_user.id)
            )
            .where(Cargo.status == CargoStatus.COMPLETED)
        )
        
        reports = await session.scalar(
            select(func.count()).select_from(Report)
            .where(Report.to_user_id == cb.from_user.id)
        )
    
    score = user.trust_score
    if score >= 80:
        level = "🟢 Высокий"
    elif score >= 50:
        level = "🟡 Средний"
    else:
        level = "🔴 Низкий"
    
    text = f"🛡 <b>Рейтинг доверия</b>\n\n"
    text += f"Уровень: {level}\n"
    text += f"Баллы: {score}/100\n\n"
    text += f"<b>Факторы:</b>\n"
    text += f"{'✅' if user.is_verified else '❌'} Верификация телефона (+20)\n"
    if profile and profile.verification_status != VerificationStatus.BASIC:
        text += f"✅ Верификация компании (+10)\n"
    else:
        text += f"❌ Верификация компании (+10)\n"
    text += f"⭐ Средний рейтинг: {round(avg_rating, 1) if avg_rating else 'нет'}\n"
    text += f"📦 Завершённых сделок: {completed}\n"
    text += f"⚠️ Жалоб на вас: {reports}\n"
    text += f"🚫 Предупреждений: {user.warnings_count}"
    
    try:
        await cb.message.edit_text(text, reply_markup=antifraud_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data == "report")
async def start_report(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        "🚨 <b>Жалоба</b>\n\n"
        "Введи ID пользователя на которого жалуешься:\n"
        "(найди в профиле пользователя)"
    )
    await state.set_state(ReportForm.user_id)
    await cb.answer()

@router.message(ReportForm.user_id)
async def report_user_id(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
    except:
        await message.answer("❌ Введи числовой ID")
        return
    
    if user_id == message.from_user.id:
        await message.answer("❌ Нельзя жаловаться на себя")
        return
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("❌ Пользователь не найден")
            return
    
    await state.update_data(to_user_id=user_id)
    await message.answer("Выбери тип жалобы:", reply_markup=report_type_kb())
    await state.set_state(ReportForm.report_type)

@router.callback_query(ReportForm.report_type, F.data.startswith("rtype_"))
async def report_type_selected(cb: CallbackQuery, state: FSMContext):
    rtype = cb.data.replace("rtype_", "")
    await state.update_data(report_type=rtype)
    await cb.message.edit_text("📝 Опиши ситуацию подробно:")
    await state.set_state(ReportForm.description)
    await cb.answer()

@router.message(ReportForm.description)
async def report_description(message: Message, state: FSMContext):
    data = await state.get_data()
    
    report_type_map = {
        "fraud": ReportType.FRAUD,
        "spam": ReportType.SPAM,
        "fake_cargo": ReportType.FAKE_CARGO,
        "no_payment": ReportType.NO_PAYMENT,
        "other": ReportType.OTHER
    }
    
    async with async_session() as session:
        report = Report(
            from_user_id=message.from_user.id,
            to_user_id=data['to_user_id'],
            report_type=report_type_map[data['report_type']],
            description=message.text
        )
        session.add(report)
        
        result = await session.execute(select(User).where(User.id == data['to_user_id']))
        user = result.scalar_one_or_none()
        if user:
            user.trust_score = max(0, user.trust_score - 5)
        
        await session.commit()
    
    if settings.admin_id:
        try:
            await bot.send_message(
                settings.admin_id,
                f"🚨 <b>Новая жалоба</b>\n\n"
                f"От: {message.from_user.id}\n"
                f"На: {data['to_user_id']}\n"
                f"Тип: {data['report_type']}\n"
                f"Описание: {message.text[:200]}"
            )
        except:
            pass
    
    await state.clear()
    await message.answer("✅ Жалоба отправлена на рассмотрение", reply_markup=main_menu())
    logger.info(f"Report from {message.from_user.id} to {data['to_user_id']}")

@router.message(F.text.startswith("/report_"))
async def quick_report(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.split("_")[1])
    except:
        return
    
    await state.update_data(to_user_id=user_id)
    await message.answer("Выбери тип жалобы:", reply_markup=report_type_kb())
    await state.set_state(ReportForm.report_type)
