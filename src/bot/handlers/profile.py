from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, func
from src.bot.states import ProfileEdit
from src.bot.keyboards import main_menu, skip_kb, profile_menu
from src.bot.utils import cargo_deeplink
from src.core.database import async_session
from src.core.models import User, Cargo, CargoPaymentStatus, CargoStatus, Rating, UserProfile, UserRole, UserVehicle, UserWallet, VerificationStatus
from src.core.logger import logger

router = Router()

ROLE_LABELS = {
    UserRole.CUSTOMER: "Заказчик",
    UserRole.CARRIER: "Перевозчик",
    UserRole.FORWARDER: "Экспедитор",
}

VERIFICATION_LABELS = {
    VerificationStatus.BASIC: "обычный",
    VerificationStatus.CONFIRMED: "подтверждён",
    VerificationStatus.VERIFIED: "верифицирован",
}




def _profile_completion(
    user: User,
    profile: "UserProfile | None",
    vehicles: list,
    cargos_count: int,
) -> tuple[int, list[tuple[str, str]]]:
    """Returns (pct 0-100, list of (emoji+label, callback_data) for missing items)."""
    is_carrier = profile and profile.role == UserRole.CARRIER

    # Each item: (weight, filled_bool, label, action_cb)
    items = [
        (20, bool(user.phone),                        "📱 Номер телефона",        "edit_phone"),
        (10, bool(profile and profile.role),          "🎭 Роль на платформе",     "begin_onboarding"),
        (20, bool(profile and profile.inn),           "🧾 ИНН",                   "edit_company"),
        (15, bool(user.company),                      "🏢 Название компании",      "edit_company"),
    ]
    if is_carrier:
        items += [
            (25, bool(vehicles),                                          "🚛 Машина в базе",            "add_truck"),
            (10, any(v.location_city for v in vehicles),                  "📍 Город дислокации машины",  "add_truck"),
        ]
    else:
        items += [
            (25, cargos_count > 0,                                        "📦 Первый груз размещён",     "add_cargo"),
            (10, bool(profile and profile.verification_status
                      and profile.verification_status != VerificationStatus.BASIC),
                                                                          "✅ Верификация пройдена",     "start_verification"),
        ]

    total_weight = sum(w for w, *_ in items)
    earned = sum(w for w, filled, *_ in items if filled)
    pct = round(earned * 100 / total_weight)

    missing = [
        (label, cb)
        for w, filled, label, cb in items
        if not filled
    ]
    return pct, missing


def _progress_bar(pct: int) -> str:
    filled = round(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"{bar} {pct}%"

@router.callback_query(F.data == "profile")
async def show_profile(cb: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == cb.from_user.id))
        user = result.scalar_one_or_none()

        if not user:
            await cb.answer("❌ Профиль не найден", show_alert=True)
            return

        avg_rating = await session.scalar(
            select(func.avg(Rating.score)).where(Rating.to_user_id == cb.from_user.id)
        )
        rating_count = await session.scalar(
            select(func.count()).select_from(Rating).where(Rating.to_user_id == cb.from_user.id)
        )

        cargos_count = await session.scalar(
            select(func.count()).select_from(Cargo).where(Cargo.owner_id == cb.from_user.id)
        )
        completed = await session.scalar(
            select(func.count()).select_from(Cargo)
            .where(Cargo.owner_id == cb.from_user.id)
            .where(Cargo.status == CargoStatus.COMPLETED)
        )

        profile = await session.scalar(select(UserProfile).where(UserProfile.user_id == cb.from_user.id))
        wallet = await session.get(UserWallet, cb.from_user.id)
        vehicles_result = await session.execute(
            select(UserVehicle).where(UserVehicle.user_id == cb.from_user.id)
        )
        vehicles = vehicles_result.scalars().all()

    stars = "⭐" * round(avg_rating) if avg_rating else "нет оценок"
    premium_text = "нет"
    if user.is_premium:
        if user.premium_until:
            premium_text = f"до {user.premium_until.strftime('%d.%m.%Y %H:%M')}"
        else:
            premium_text = "активен"
    wallet_balance = int(wallet.balance_rub) if wallet else 0
    wallet_frozen = int(wallet.frozen_balance_rub) if wallet else 0

    pct, missing = _profile_completion(user, profile, vehicles, cargos_count)
    bar = _progress_bar(pct)
    nudge = ""
    if pct < 100 and missing:
        first_label, _ = missing[0]
        nudge = f"\n💡 Следующий шаг: <b>{first_label}</b>\n"

    text = f"👤 <b>Кабинет / профиль</b>\n\n"
    text += f"📊 Заполненность: {bar}{nudge}\n"
    text += f"🆔 <code>{user.id}</code>\n"
    text += f"📝 {user.full_name}\n"
    if user.username:
        text += f"📱 @{user.username}\n"
    text += f"📞 {user.phone or 'не указан'}\n"
    text += f"🏢 {user.company or 'не указана'}\n"

    role_label = ROLE_LABELS.get(profile.role, "—") if profile else "—"
    ver_label = VERIFICATION_LABELS.get(profile.verification_status, "обычный") if profile else "обычный"
    inn_value = profile.inn if profile and profile.inn else "не указан"

    text += f"🏷 Роль: {role_label}\n"
    text += f"🧾 ИНН: {inn_value}\n"
    text += f"🛡 Верификация: {ver_label}\n\n"
    text += f"⭐ Репутация: {stars} ({rating_count})\n"
    text += f"📦 Грузов: {cargos_count} (завершено: {completed})\n"
    text += f"💎 Premium: {premium_text}\n"
    text += f"💼 Кошелёк: {wallet_balance:,}₽ (холд: {wallet_frozen:,}₽)\n"
    text += f"📅 С нами с: {user.created_at.strftime('%d.%m.%Y')}"

    # Если профиль неполный — добавляем кнопки быстрых действий
    from aiogram.types import InlineKeyboardMarkup
    from aiogram.utils.keyboard import InlineKeyboardBuilder as _IKB
    b = _IKB()
    if pct < 100 and missing:
        for label, cb_data in missing[:3]:
            b.row(InlineKeyboardButton(text=f"➕ {label}", callback_data=cb_data))
    # Стандартные кнопки кабинета
    b.row(InlineKeyboardButton(text="✏️ Редактировать", callback_data="profile_edit_menu"))
    b.row(InlineKeyboardButton(text="🔔 Подписки", callback_data="subscriptions"))
    b.row(InlineKeyboardButton(text="💳 Premium", callback_data="buy_premium_menu"))
    b.row(InlineKeyboardButton(text="📜 История", callback_data="history"))
    b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))

    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.callback_query(F.data == "profile_edit_menu")
async def profile_edit_menu(cb: CallbackQuery):
    try:
        await cb.message.edit_text("✏️ Что изменить?", reply_markup=profile_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.callback_query(F.data == "buy_premium_menu")
async def buy_premium_menu(cb: CallbackQuery):
    from src.bot.handlers.payments import _build_buy_kb
    try:
        await cb.message.edit_text(
            "💎 <b>Premium</b>\n\nОткрывает полные контакты в ленте, "
            "приоритетный доступ к заявкам и отклики без ограничений.",
            reply_markup=_build_buy_kb(),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            "💎 Premium",
            reply_markup=_build_buy_kb(),
        )
    await cb.answer()


@router.callback_query(F.data == "edit_phone")
async def edit_phone(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text("📞 Введи номер телефона:")
    await state.set_state(ProfileEdit.phone)
    await cb.answer()

@router.message(ProfileEdit.phone)
async def save_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == message.from_user.id))
        user = result.scalar_one_or_none()
        if user:
            user.phone = phone
            await session.commit()
    
    await state.clear()
    await message.answer(f"✅ Телефон сохранён: {phone}", reply_markup=main_menu())
    logger.info(f"User {message.from_user.id} updated phone: {phone}")

@router.callback_query(F.data == "edit_company")
async def edit_company(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text("🏢 Введи название компании:")
    await state.set_state(ProfileEdit.company)
    await cb.answer()

@router.message(ProfileEdit.company)
async def save_company(message: Message, state: FSMContext):
    company = message.text.strip()
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == message.from_user.id))
        user = result.scalar_one_or_none()
        if user:
            user.company = company
            await session.commit()
    
    await state.clear()
    await message.answer(f"✅ Компания: {company}", reply_markup=main_menu())
    logger.info(f"User {message.from_user.id} updated company: {company}")

@router.callback_query(F.data == "history")
async def show_history(cb: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(
            select(Cargo)
            .where(
                (Cargo.owner_id == cb.from_user.id) | (Cargo.carrier_id == cb.from_user.id)
            )
            .where(Cargo.status.in_([CargoStatus.COMPLETED, CargoStatus.CANCELLED, CargoStatus.ARCHIVED]))
            .order_by(Cargo.created_at.desc())
            .limit(10)
        )
        cargos = result.scalars().all()
    
    if not cargos:
        try:
            await cb.message.edit_text("📜 История пуста", reply_markup=profile_menu())
        except TelegramBadRequest:
            pass
        await cb.answer()
        return
    
    header = "📜 <b>История рейсов:</b>\n\n"
    try:
        await cb.message.edit_text(header, reply_markup=profile_menu())
    except TelegramBadRequest:
        pass

    for c in cargos:
        role = "📦" if c.owner_id == cb.from_user.id else "🚛"
        link = cargo_deeplink(c.id)
        status = {
            CargoStatus.COMPLETED: "✅ Завершён",
            CargoStatus.CANCELLED: "❌ Отменён",
            CargoStatus.ARCHIVED: "🗄️ Архив",
        }.get(c.status, c.status.value)

        text = f"{role} {c.from_city} → {c.to_city}\n"
        text += f"   {c.weight}т, {c.price}₽ — {status}\n"
        if getattr(c, "payment_status", None) in {
            CargoPaymentStatus.FUNDED,
            CargoPaymentStatus.DELIVERY_MARKED,
            CargoPaymentStatus.RELEASED,
        }:
            text += "   🛡️ Честный рейс\n"
        text += f"   {link}\n"

        reply_markup = None
        if c.status == CargoStatus.ARCHIVED and c.owner_id == cb.from_user.id:
            b = InlineKeyboardBuilder()
            b.row(InlineKeyboardButton(text="♻️ Восстановить", callback_data=f"restore_cargo_{c.id}"))
            reply_markup = b.as_markup()

        await cb.message.answer(text, reply_markup=reply_markup)

    await cb.answer()
