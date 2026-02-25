from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func, or_, and_
from src.bot.states import RateForm
from src.bot.keyboards import main_menu, skip_kb
from src.core.database import async_session
from src.core.models import Rating, Cargo, CargoStatus, User, UserProfile, VerificationStatus
from src.core.logger import logger

router = Router()

def _verification_label(profile: UserProfile | None) -> str:
    if not profile:
        return "обычный"
    if profile.verification_status == VerificationStatus.VERIFIED:
        return "верифицирован"
    if profile.verification_status == VerificationStatus.CONFIRMED:
        return "подтверждён"
    return "обычный"

@router.message(F.text.startswith("/rate_"))
async def start_rate(message: Message, state: FSMContext):
    try:
        cargo_id = int(message.text.split("_")[1])
    except:
        return
    
    async with async_session() as session:
        result = await session.execute(select(Cargo).where(Cargo.id == cargo_id))
        cargo = result.scalar_one_or_none()
        
        if not cargo:
            await message.answer("❌ Груз не найден")
            return
        
        if cargo.owner_id == message.from_user.id:
            to_user_id = cargo.carrier_id
        elif cargo.carrier_id == message.from_user.id:
            to_user_id = cargo.owner_id
        else:
            await message.answer("❌ Ты не участник этого груза")
            return
        
        if not to_user_id:
            await message.answer("❌ Некого оценивать")
            return
        
        existing = await session.execute(
            select(Rating)
            .where(Rating.cargo_id == cargo_id)
            .where(Rating.from_user_id == message.from_user.id)
        )
        if existing.scalar_one_or_none():
            await message.answer("⚠️ Ты уже оценил этот груз")
            return
    
    await state.update_data(cargo_id=cargo_id, to_user_id=to_user_id)
    await message.answer("⭐ Оцени от 1 до 5:")
    await state.set_state(RateForm.score)

@router.message(RateForm.score)
async def rate_score(message: Message, state: FSMContext):
    try:
        score = int(message.text)
        if score < 1 or score > 5:
            raise ValueError
    except:
        await message.answer("❌ Введи число от 1 до 5")
        return
    
    await state.update_data(score=score)
    await message.answer("💬 Комментарий (или пропусти):", reply_markup=skip_kb())
    await state.set_state(RateForm.comment)

@router.message(RateForm.comment)
async def rate_comment(message: Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await save_rating(message, state)

@router.callback_query(RateForm.comment, F.data == "skip")
async def rate_skip_comment(cb: CallbackQuery, state: FSMContext):
    await state.update_data(comment=None)
    await save_rating(cb.message, state)
    await cb.answer()

async def save_rating(message: Message, state: FSMContext):
    data = await state.get_data()
    
    async with async_session() as session:
        rating = Rating(
            cargo_id=data['cargo_id'],
            from_user_id=message.chat.id,
            to_user_id=data['to_user_id'],
            score=data['score'],
            comment=data.get('comment')
        )
        session.add(rating)
        await session.commit()
    
    stars = "⭐" * data['score']
    await state.clear()
    await message.answer(f"✅ Оценка сохранена: {stars}", reply_markup=main_menu())
    logger.info(f"Rating {data['score']} from {message.chat.id} to {data['to_user_id']}")

@router.message(F.text.startswith("/user_"))
async def view_user(message: Message):
    try:
        user_id = int(message.text.split("_")[1])
    except:
        return
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            await message.answer("❌ Пользователь не найден")
            return
        
        avg_rating = await session.scalar(
            select(func.avg(Rating.score)).where(Rating.to_user_id == user_id)
        )
        rating_count = await session.scalar(
            select(func.count()).select_from(Rating).where(Rating.to_user_id == user_id)
        )

        profile = await session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))

        has_deal = await session.scalar(
            select(func.count())
            .select_from(Cargo)
            .where(
                Cargo.status.in_([CargoStatus.IN_PROGRESS, CargoStatus.COMPLETED]),
                or_(
                    and_(Cargo.owner_id == message.from_user.id, Cargo.carrier_id == user_id),
                    and_(Cargo.owner_id == user_id, Cargo.carrier_id == message.from_user.id),
                ),
            )
        )
    
    stars = "⭐" * round(avg_rating) if avg_rating else "нет оценок"
    
    text = f"👤 <b>{user.full_name}</b>\n\n"
    text += f"🆔 <code>{user.id}</code>\n"
    if user.username:
        text += f"📱 @{user.username}\n"
    text += f"🛡 Верификация: {_verification_label(profile)}\n"
    can_show_phone = user_id == message.from_user.id or bool(has_deal)
    if can_show_phone and user.phone:
        text += f"📞 {user.phone}\n"
    else:
        text += "📞 Контакты скрыты до сделки\n"
    text += f"\n⭐ Рейтинг: {stars}\n"
    text += f"📊 Оценок: {rating_count}"
    
    await message.answer(text)
