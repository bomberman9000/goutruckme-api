from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from src.bot.keyboards import main_menu, role_kb, contact_request_kb, legal_type_kb, webapp_entry_kb
from src.bot.handlers.cargo import send_cargo_details
from src.core.database import async_session
from src.core.models import User, Reminder, UserProfile, UserRole
from src.core.services.cross_sync import (
    confirm_gruzpotok_telegram_link,
    create_gruzpotok_login_link,
    verify_gruzpotok_login_token,
)
from src.core.services.referral import attach_referral_invite
from src.bot.states import Onboarding
from src.core.config import settings

router = Router()

ROLE_MAP = {
    "customer": UserRole.CUSTOMER,
    "carrier": UserRole.CARRIER,
    "forwarder": UserRole.FORWARDER,
}

CANCEL_HINT = "\n\n❌ Отмена: /cancel"


def _site_link_url() -> str | None:
    base = (settings.gruzpotok_public_url or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/telegram-link"


def _guest_entry_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🚀 Начать в Telegram", callback_data="begin_onboarding")]
    ]

    site_url = _site_link_url()
    if site_url:
        rows.append([InlineKeyboardButton(text="🔗 Привязать через сайт", url=site_url)])

    webapp_base = (settings.webapp_url or "").rstrip("/")
    if webapp_base:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📱 Открыть Mini App",
                    web_app=WebAppInfo(url=f"{webapp_base}/webapp"),
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)

async def upsert_text(obj, text: str, reply_markup=None, disable_web_page_preview=True):
    """
    Унифицирует вывод: если это CallbackQuery — редактируем его message.
    Если Message — пытаемся отредактировать последнее (сам Message) если возможно,
    иначе отправляем новое.
    """
    # Inline keyboard можно использовать с edit_text,
    # reply- и remove-клавиатуры — только с answer().
    is_inline = (
        reply_markup is None or isinstance(reply_markup, InlineKeyboardMarkup)
    )

    try:
        if isinstance(obj, CallbackQuery):
            if is_inline:
                return await obj.message.edit_text(
                    text,
                    reply_markup=reply_markup,
                    disable_web_page_preview=disable_web_page_preview,
                )
            return await obj.message.answer(
                text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )

        # obj is Message
        if is_inline:
            return await obj.edit_text(
                text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        return await obj.answer(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )

    except (TelegramBadRequest, AttributeError):
        # Fallback: если редактирование не удалось, отправляем новое сообщение.
        if isinstance(obj, CallbackQuery):
            return await obj.message.answer(
                text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        return await obj.answer(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )

async def get_profile(session, user_id: int) -> UserProfile | None:
    result = await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    return result.scalar_one_or_none()

async def ensure_profile(session, user_id: int) -> UserProfile:
    profile = await get_profile(session, user_id)
    if profile:
        return profile
    profile = UserProfile(user_id=user_id)
    session.add(profile)
    await session.commit()
    return profile

async def needs_onboarding(user_id: int) -> bool:
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        profile = await get_profile(session, user_id)

    if not user:
        return True
    if not user.phone:
        return True
    if not profile:
        return True
    if not profile.role:
        return True
    if profile.role in (UserRole.CUSTOMER, UserRole.FORWARDER):
        if not profile.inn or not user.company:
            return True
    if profile.role == UserRole.CARRIER:
        if profile.inn and not user.company:
            return True
    return False

async def start_onboarding(obj: Message | CallbackQuery, state: FSMContext):
    await state.clear()
    await upsert_text(
        obj,
        "👋 Добро пожаловать! Чтобы начать, выберите роль:" + CANCEL_HINT,
        reply_markup=role_kb(),
    )
    await state.set_state(Onboarding.role)


async def _try_attach_referral(start_payload: str | None, invited_user_id: int) -> str | None:
    payload = (start_payload or "").strip().lower()
    if not payload.startswith("ref_"):
        return None

    try:
        inviter_user_id = int(payload.split("_", maxsplit=1)[1])
    except (ValueError, IndexError):
        return "invalid"

    async with async_session() as session:
        created, reason = await attach_referral_invite(
            session,
            inviter_user_id=inviter_user_id,
            invited_user_id=invited_user_id,
            source_payload=start_payload,
        )
        if created:
            await session.commit()
        else:
            await session.rollback()
    return reason

@router.callback_query(F.data == "cancel")
async def cancel_flow_cb(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await upsert_text(cb, "Ок, отменил", reply_markup=main_menu())
    await cb.answer()

@router.message(F.text.in_({"отмена", "cancel", "/cancel"}))
async def cancel_flow_msg(message: Message, state: FSMContext):
    await state.clear()
    await upsert_text(message, "Ок, отменил", reply_markup=main_menu())

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    start_payload: str | None = None
    if message.text:
        parts = message.text.split(maxsplit=1)
        start_payload = parts[1] if len(parts) == 2 else None
        if start_payload == "buy_premium":
            from src.bot.handlers.payments import _build_buy_kb

            await message.answer(
                "💳 Выберите тариф Premium.\n\n"
                "Premium открывает полный контакт в ленте, приоритетный доступ к новым заявкам "
                "и быстрый доступ к откликам.",
                reply_markup=_build_buy_kb(),
            )
            return
        if len(parts) == 2 and parts[1].startswith("link_"):
            code = parts[1][len("link_"):].strip()
            if not code:
                await message.answer("❌ Невалидная ссылка привязки.")
                return

            confirm_result = await confirm_gruzpotok_telegram_link(
                code=code,
                telegram_user_id=message.from_user.id,
                telegram_username=message.from_user.username,
            )
            if not bool(confirm_result.get("ok")):
                await message.answer(
                    "❌ Не удалось подтвердить привязку по старой ссылке. "
                    "Запросите новую ссылку на сайте или откройте Mini App из бота."
                )
                return

            async with async_session() as session:
                user = await session.get(User, message.from_user.id)
                if not user:
                    user = User(
                        id=message.from_user.id,
                        username=message.from_user.username,
                        full_name=message.from_user.full_name,
                    )
                    session.add(user)
                else:
                    user.username = message.from_user.username
                    user.full_name = message.from_user.full_name
                user.is_verified = True
                await session.commit()

            await message.answer(
                "✅ Аккаунт успешно привязан.\n\n"
                "Откройте Mini App или используйте меню бота.",
                reply_markup=webapp_entry_kb(),
            )
            return

        if len(parts) == 2 and parts[1].startswith("login_token_"):
            token = parts[1][len("login_token_"):].strip()
            if not token:
                await message.answer("❌ Невалидный токен входа.")
                return

            verify_result = await verify_gruzpotok_login_token(
                token=token,
                telegram_user_id=message.from_user.id,
            )
            if not bool(verify_result.get("ok")):
                await message.answer(
                    "❌ Не удалось связать аккаунт по ссылке. "
                    "Запросите новую ссылку на сайте и попробуйте снова."
                )
                return

            async with async_session() as session:
                user = await session.get(User, message.from_user.id)
                if not user:
                    user = User(
                        id=message.from_user.id,
                        username=message.from_user.username,
                        full_name=message.from_user.full_name,
                    )
                    session.add(user)
                else:
                    user.username = message.from_user.username
                    user.full_name = message.from_user.full_name
                await session.commit()

            web_url = (
                verify_result.get("web_url")
                or verify_result.get("redirect_url")
                or verify_result.get("url")
            )
            if isinstance(web_url, str) and web_url.strip():
                await message.answer(
                    "✅ Аккаунт связан. Можно сразу открыть сайт без повторного входа.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="🌐 Открыть площадку",
                                    url=web_url.strip(),
                                )
                            ]
                        ]
                    ),
                )
            else:
                await message.answer(
                    "✅ Аккаунт связан. Выберите действие в меню или откройте Mini App.",
                    reply_markup=main_menu(),
                )
            return

        if len(parts) == 2 and parts[1].startswith("cargo_"):
            try:
                cargo_id = int(parts[1].split("_")[1])
            except (ValueError, IndexError):
                cargo_id = None
            if cargo_id:
                await send_cargo_details(message, cargo_id)
                return

    referral_result = await _try_attach_referral(start_payload, message.from_user.id)
    if referral_result == "created":
        await message.answer(
            "🎉 Приглашение принято. После первой оплаты ты и пригласивший получите бонус."
        )

    async with async_session() as session:
        user = await session.get(User, message.from_user.id)

    if user and user.is_verified:
        await state.clear()
        await message.answer(
            "👋 <b>ГрузПоток готов к работе</b>\n\n"
            "Используйте меню ниже для поиска грузов, размещения заявок и работы с Mini App.",
            reply_markup=main_menu(),
        )
        return

    if await needs_onboarding(message.from_user.id):
        await state.clear()
        await message.answer(
            "👋 <b>Добро пожаловать в ГрузПоток</b>\n\n"
            "Чтобы сайт, бот и Mini App работали как единое целое, сначала свяжите аккаунт.\n\n"
            "Выберите удобный сценарий:\n"
            "• начать регистрацию прямо в Telegram\n"
            "• открыть сайт для привязки\n"
            "• зайти в Mini App",
            reply_markup=_guest_entry_kb(),
        )
        return

    await message.answer(
        f"👋 Привет, <b>{message.from_user.full_name}</b>!\n\n"
        "Выбери действие в меню ниже.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "begin_onboarding")
async def begin_onboarding(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await start_onboarding(cb, state)

@router.callback_query(F.data == "menu")
async def show_menu(cb: CallbackQuery):
    try:
        await cb.message.edit_text("🏠 <b>Главное меню</b>", reply_markup=main_menu())
    except TelegramBadRequest:
        await cb.message.answer("🏠 <b>Главное меню</b>", reply_markup=main_menu())
    await cb.answer()


@router.message(Command("webapp"))
async def open_webapp(message: Message):
    if not (settings.webapp_url or "").strip():
        await message.answer(
            "⚠️ WEBAPP_URL пока не настроен.\n"
            "После деплоя фронта укажи WEBAPP_URL в .env.",
            reply_markup=main_menu(),
        )
        return

    await message.answer(
        "📱 Mini App открывается внутри Telegram.\n\n"
        "Там доступны кабинет, кошелёк, грузы, флот и подбор рейсов.",
        reply_markup=webapp_entry_kb(),
    )


@router.message(Command("link"))
async def legacy_link(message: Message):
    login_url = await create_gruzpotok_login_link(
        telegram_user_id=message.from_user.id,
        redirect_path="/webapp",
    )

    if login_url:
        rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text="🌐 Открыть площадку", url=login_url)]
        ]
        webapp_base = (settings.webapp_url or "").rstrip("/")
        if webapp_base:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="📱 Открыть Mini App",
                        web_app=WebAppInfo(url=f"{webapp_base}/webapp"),
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text="◀️ Меню", callback_data="menu")])

        await message.answer(
            "🔗 Вход по старому сценарию доступен.\n\n"
            "Откройте площадку по кнопке ниже — вход выполнится автоматически.\n"
            "Кабинет в Telegram также доступен через Mini App.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return

    await message.answer(
        "🔗 Авто-вход через сайт сейчас недоступен.\n\n"
        "Откройте Mini App из этого бота — Telegram-сессия подтянется автоматически.\n"
        "Если доступ устарел, обновите его в кабинете.",
        reply_markup=webapp_entry_kb(),
    )

@router.callback_query(Onboarding.role, F.data.startswith("role_"))
async def onboarding_role(cb: CallbackQuery, state: FSMContext):
    role_key = cb.data.replace("role_", "")
    role = ROLE_MAP.get(role_key)

    if not role:
        await cb.answer("❌ Неизвестная роль", show_alert=True)
        return

    # Отвечаем сразу — иначе клиент на новом устройстве показывает загрузку
    await cb.answer()

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == cb.from_user.id))).scalar_one_or_none()
        profile = await ensure_profile(session, cb.from_user.id)
        profile.role = role
        if not user:
            user = User(id=cb.from_user.id, username=cb.from_user.username, full_name=cb.from_user.full_name)
            session.add(user)
        user.is_carrier = role == UserRole.CARRIER
        await session.commit()

    await state.update_data(role=role.value)
    await upsert_text(
        cb,
        "📲 Поделись номером телефона через кнопку ниже." + CANCEL_HINT,
        reply_markup=contact_request_kb(),
    )
    await state.set_state(Onboarding.contact)

@router.message(Onboarding.contact, F.contact)
async def onboarding_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == message.from_user.id))).scalar_one_or_none()
        profile = await ensure_profile(session, message.from_user.id)
        if not user:
            user = User(id=message.from_user.id, username=message.from_user.username, full_name=message.from_user.full_name)
            session.add(user)
        user.phone = phone
        await session.commit()

    data = await state.get_data()
    role_value = data.get("role")
    role = UserRole(role_value) if role_value else profile.role

    if not role:
        await upsert_text(
            message,
            "Выберите роль, чтобы продолжить:",
            reply_markup=role_kb(),
        )
        await state.set_state(Onboarding.role)
        return

    if role == UserRole.CARRIER:
        await upsert_text(
            message,
            "✅ Номер сохранён.\n\n🏢 Укажите тип организации:" + CANCEL_HINT,
            reply_markup=legal_type_kb(),
        )
        await state.set_state(Onboarding.legal_type)
        return

    await upsert_text(
        message,
        "✅ Номер сохранён.\n\n🧾 Укажи ИНН (10 или 12 цифр):" + CANCEL_HINT,
    )
    await state.set_state(Onboarding.inn)

@router.message(Onboarding.legal_type)
async def onboarding_legal_type(message: Message, state: FSMContext):
    legal_type = message.text.strip()
    if legal_type not in ("ИП", "ООО", "Физлицо"):
        await upsert_text(
            message,
            "❌ Выбери тип из кнопок ниже.",
            reply_markup=legal_type_kb(),
        )
        return

    if legal_type == "Физлицо":
        async with async_session() as session:
            user = (await session.execute(select(User).where(User.id == message.from_user.id))).scalar_one_or_none()
            profile = await ensure_profile(session, message.from_user.id)
            profile.inn = None
            if user:
                user.company = None
            await session.commit()

        await state.clear()
        await upsert_text(
            message,
            "✅ Регистрация завершена",
            reply_markup=main_menu(),
        )
        return

    await upsert_text(
        message,
        "🧾 Укажи ИНН (10 или 12 цифр):" + CANCEL_HINT,
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Onboarding.inn)

@router.message(Onboarding.inn)
async def onboarding_inn(message: Message, state: FSMContext):
    inn = message.text.strip()
    if not inn.isdigit() or len(inn) not in (10, 12):
        await upsert_text(message, "❌ ИНН должен содержать 10 или 12 цифр")
        return

    async with async_session() as session:
        profile = await ensure_profile(session, message.from_user.id)
        profile.inn = inn
        await session.commit()

    await upsert_text(message, "🏢 Введи название компании (ООО/ИП):" + CANCEL_HINT)
    await state.set_state(Onboarding.company)

@router.message(Onboarding.company)
async def onboarding_company(message: Message, state: FSMContext):
    company = message.text.strip()
    if not company:
        await upsert_text(message, "❌ Введи название компании")
        return

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == message.from_user.id))).scalar_one_or_none()
        await ensure_profile(session, message.from_user.id)
        if not user:
            user = User(id=message.from_user.id, username=message.from_user.username, full_name=message.from_user.full_name)
            session.add(user)
        user.company = company
        await session.commit()

    await state.clear()
    await upsert_text(
        message,
        "✅ Регистрация завершена",
        reply_markup=main_menu(),
    )

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📚 <b>Как пользоваться ГрузПотоком</b>\n\n"
        "<b>Что где находится:</b>\n"
        "• Бот — быстрые команды, поиск, отклики, уведомления\n"
        "• Mini App — кабинет, кошелёк, Честный рейс / Safe Deal, флот, мои грузы\n"
        "• Сайт — полная площадка и работа через браузер\n\n"
        "<b>Основные действия в боте:</b>\n"
        "🚛 Найти груз\n"
        "📦 Разместить груз\n"
        "🧾 Мои грузы\n"
        "🤝 Мои отклики\n"
        "⭐ Кабинет / Профиль\n"
        "🆘 Поддержка\n\n"
        "<b>Если вы переходите со старого бота:</b>\n"
        "/link — открыть площадку по магической ссылке\n"
        "/loads — открыть ленту грузов\n"
        "/applications — показать мои отклики\n\n"
        "<b>Основные команды:</b>\n"
        "/start — меню\n"
        "/help — помощь\n"
        "/webapp — открыть Mini App\n"
        "/buy_premium — купить premium\n"
        "/referral — пригласить коллегу\n"
        "/me — мой профиль\n"
        "/find — умный поиск грузов\n"
        "/remind 30m Текст — напоминание\n"
        "/reminders — мои напоминания\n"
        "\n🔍 <b>Умный поиск:</b>\n"
        "/find москва питер — найти грузы\n"
        "/find мск спб 20т до 100к — с фильтрами\n"
        "\nПримеры:\n"
        "• /find москва — грузы из Москвы\n"
        "• /find москва питер — Москва → СПб\n"
        "• /find мск спб — Москва → СПб (сокращения)\n"
        "• /find в краснодар — грузы в Краснодар\n"
        "• /find 20т — грузы 20 тонн\n"
        "• /find мск спб 20т — Москва → СПб, 20т\n"
        "• /find из казани до 100к — из Казани, до 100,000₽\n"
        "• /find ростов 10-15т — из Ростова, 10-15 тонн\n\n"
        "<b>Совет:</b>\n"
        "Для кабинета и Честного рейса используйте Mini App через кнопку в меню."
    )

@router.message(Command("me"))
async def cmd_me(message: Message):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == message.from_user.id))
        user = result.scalar_one_or_none()

        reminders = await session.execute(
            select(Reminder)
            .where(Reminder.user_id == message.from_user.id)
            .where(Reminder.is_sent.is_(False))
        )
        rem_count = len(reminders.scalars().all())

    if user:
        status = "🚫 Забанен" if user.is_banned else "✅ Активен"
        await message.answer(
            "👤 <b>Твой профиль:</b>\n\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"📝 Имя: {user.full_name}\n"
            f"📅 Регистрация: {user.created_at.strftime('%d.%m.%Y')}\n"
            f"⏰ Напоминаний: {rem_count}\n"
            f"Статус: {status}"
        )
