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


# ── Верификация водителя ───────────────────────────────────────────────────────

@router.callback_query(F.data == "driver_verification")
async def start_driver_verification(cb: CallbackQuery, state: FSMContext):
    from src.bot.states import DriverVerification
    await cb.message.edit_text(
        "🪪 <b>Верификация водителя</b>\n\n"
        "Шаг 1/2 — Пришли фото <b>водительского удостоверения</b> (обе стороны в одном фото):",
        parse_mode="HTML",
    )
    await state.set_state(DriverVerification.license_photo)
    await cb.answer()


@router.message(F.photo)
async def driver_license_photo(message: Message, state: FSMContext):
    from src.bot.states import DriverVerification
    if await state.get_state() != DriverVerification.license_photo.state:
        return
    file_id = message.photo[-1].file_id
    await state.update_data(license_file_id=file_id)
    await message.answer(
        "✅ Права получены!\n\n"
        "Шаг 2/2 — Теперь пришли фото <b>СТС</b> (свидетельство о регистрации ТС):",
        parse_mode="HTML",
    )
    await state.set_state(DriverVerification.sts_photo)


@router.message(F.photo)
async def driver_sts_photo(message: Message, state: FSMContext):
    from src.bot.states import DriverVerification
    if await state.get_state() != DriverVerification.sts_photo.state:
        return
    data = await state.get_data()
    sts_file_id = message.photo[-1].file_id
    license_file_id = data.get("license_file_id")

    async with async_session() as session:
        profile = await ensure_profile(session, message.from_user.id)
        profile.driver_license_file_id = license_file_id
        profile.sts_file_id = sts_file_id
        from datetime import datetime
        profile.driver_verified_at = datetime.utcnow()
        if profile.verification_status == VerificationStatus.BASIC:
            profile.verification_status = VerificationStatus.CONFIRMED
        await session.commit()

    await state.clear()

    # Уведомляем модератора
    from src.core.config import settings
    from src.bot.bot import bot
    if settings.admin_chat_id:
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            mod_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Верифицировать", callback_data=f"verify_driver_{message.from_user.id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_driver_{message.from_user.id}"),
            ]])
            await bot.send_message(
                settings.admin_chat_id,
                f"🪪 Новая верификация водителя\nUser: {message.from_user.id} @{message.from_user.username or '—'}",
                reply_markup=mod_kb,
            )
            await bot.send_photo(settings.admin_chat_id, license_file_id, caption="Права")
            await bot.send_photo(settings.admin_chat_id, sts_file_id, caption="СТС")
        except Exception:
            pass

    await message.answer(
        "✅ Документы отправлены на проверку!\n\n"
        "Обычно проверка занимает несколько часов. "
        "После одобрения ты получишь значок ✅ в профиле.",
        reply_markup=profile_menu(),
    )


@router.callback_query(F.data.startswith("verify_driver_"))
async def admin_verify_driver(cb: CallbackQuery):
    user_id = int(cb.data.split("_")[2])
    async with async_session() as session:
        profile = await ensure_profile(session, user_id)
        profile.verification_status = VerificationStatus.VERIFIED
        user_result = await session.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if user:
            user.is_verified = True
            user.trust_score = min(100, user.trust_score + 20)
        await session.commit()

    from src.bot.bot import bot
    try:
        await bot.send_message(user_id, "✅ Ваши документы проверены! Вы верифицированы как водитель.")
    except Exception:
        pass
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✅ Водитель верифицирован")


@router.callback_query(F.data.startswith("reject_driver_"))
async def admin_reject_driver(cb: CallbackQuery):
    user_id = int(cb.data.split("_")[2])
    from src.bot.bot import bot
    try:
        await bot.send_message(
            user_id,
            "❌ Документы не прошли проверку. Пожалуйста, пришли чёткие фото и попробуй снова.",
        )
    except Exception:
        pass
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("❌ Отклонено")
