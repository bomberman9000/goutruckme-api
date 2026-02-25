from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from src.bot.states import LegalVerification
from src.bot.keyboards import skip_kb, profile_menu
from src.core.database import async_session
from src.core.models import User, UserProfile, VerificationStatus

router = Router()

async def ensure_profile(session, user_id: int) -> UserProfile:
    profile = (
        await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalar_one_or_none()
    if profile:
        return profile
    profile = UserProfile(user_id=user_id)
    session.add(profile)
    await session.commit()
    return profile

@router.callback_query(F.data == "start_verification")
async def start_verification(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text("🧾 Введите ИНН (10 или 12 цифр):")
    await state.set_state(LegalVerification.inn)
    await cb.answer()

@router.message(LegalVerification.inn)
async def verification_inn(message: Message, state: FSMContext):
    inn = message.text.strip()
    if not inn.isdigit() or len(inn) not in (10, 12):
        await message.answer("❌ ИНН должен содержать 10 или 12 цифр")
        return

    async with async_session() as session:
        profile = await ensure_profile(session, message.from_user.id)
        profile.inn = inn
        await session.commit()

    await message.answer("🧾 Введите ОГРН/ОГРНИП (13 или 15 цифр):")
    await state.set_state(LegalVerification.ogrn)

@router.message(LegalVerification.ogrn)
async def verification_ogrn(message: Message, state: FSMContext):
    ogrn = message.text.strip()
    if not ogrn.isdigit() or len(ogrn) not in (13, 15):
        await message.answer("❌ ОГРН/ОГРНИП должен содержать 13 или 15 цифр")
        return

    async with async_session() as session:
        profile = await ensure_profile(session, message.from_user.id)
        profile.ogrn = ogrn
        await session.commit()

    await message.answer("👤 Введите ФИО директора/ИП:")
    await state.set_state(LegalVerification.director)

@router.message(LegalVerification.director)
async def verification_director(message: Message, state: FSMContext):
    director_name = message.text.strip()

    async with async_session() as session:
        profile = await ensure_profile(session, message.from_user.id)
        profile.director_name = director_name
        await session.commit()

    await message.answer(
        "📎 Пришлите документ (реквизиты/выписка) или пропустите:",
        reply_markup=skip_kb(),
    )
    await state.set_state(LegalVerification.doc)

@router.callback_query(LegalVerification.doc, F.data == "skip")
async def verification_skip_doc(cb: CallbackQuery, state: FSMContext):
    await finalize_verification(cb.message, state, None)
    await cb.answer()

@router.message(LegalVerification.doc, F.document)
async def verification_doc(message: Message, state: FSMContext):
    await finalize_verification(message, state, message.document.file_id)

@router.message(LegalVerification.doc, F.photo)
async def verification_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id if message.photo else None
    await finalize_verification(message, state, file_id)

async def finalize_verification(
    message: Message,
    state: FSMContext,
    file_id: str | None,
):
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.id == message.from_user.id))
        ).scalar_one_or_none()
        profile = await ensure_profile(session, message.from_user.id)

        if file_id:
            profile.verification_doc_file_id = file_id

        if profile.verification_status == VerificationStatus.BASIC:
            profile.verification_status = VerificationStatus.CONFIRMED
            if user:
                user.trust_score = min(100, user.trust_score + 10)

        await session.commit()

    await state.clear()
    await message.answer(
        "✅ Данные отправлены. Статус: подтверждён.\n\n"
        "После ручной проверки статус станет \"верифицирован\".",
        reply_markup=profile_menu(),
    )
