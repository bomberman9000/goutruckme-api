from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from src.bot.keyboards import main_menu
from src.core.config import settings
from src.core.database import async_session
from src.core.models import AvailableTruck, CargoContactUnlock, PremiumPayment, TruckContactUnlock, User
from src.core.services.referral import extend_premium_until, grant_referral_reward_if_applicable


router = Router()


def _build_buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⭐ 7 дней — {settings.premium_stars_7d} XTR",
                    callback_data="buy_premium:7",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⭐ 30 дней — {settings.premium_stars_30d} XTR",
                    callback_data="buy_premium:30",
                )
            ],
            [InlineKeyboardButton(text="◀️ Меню", callback_data="menu")],
        ]
    )


def _plan_config(days: int) -> tuple[int, int, str] | None:
    if days == 7:
        return 7, settings.premium_stars_7d, "Премиум-доступ на 7 дней"
    if days == 30:
        return 30, settings.premium_stars_30d, "Премиум-доступ на 30 дней"
    return None


def _truck_unlock_config() -> tuple[int, str]:
    return settings.truck_contact_unlock_stars, "Разовый доступ к контакту перевозчика"


def _render_truck_unlock_text(truck: AvailableTruck) -> str:
    parts = ["🔓 <b>Контакт открыт</b>"]
    summary: list[str] = []
    if truck.truck_type:
        summary.append(truck.truck_type.title())
    if truck.capacity_tons:
        summary.append(f"{truck.capacity_tons}т")
    if summary:
        parts.append("🚛 " + " • ".join(summary))
    if truck.base_city:
        parts.append(f"📍 {truck.base_city}")
    if truck.routes:
        parts.append(f"🗺 {truck.routes[:120]}")
    if truck.phone:
        parts.append(f"📞 {truck.phone}")
    if truck.avito_url:
        parts.append("🔗 Источник доступен кнопкой ниже")
    return "\n".join(parts)


def _truck_unlock_kb(truck: AvailableTruck) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if truck.phone:
        phone_clean = "".join(c for c in truck.phone if c.isdigit() or c == "+")
        if phone_clean:
            rows.append([InlineKeyboardButton(text="📞 Позвонить", url=f"tel:{phone_clean}")])
    if truck.avito_url:
        rows.append([InlineKeyboardButton(text="📍 Открыть источник", url=truck.avito_url)])
    rows.append([InlineKeyboardButton(text="◀️ Меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_truck_unlock_entry(*, bot, chat_id: int, user_id: int, truck_id: int) -> tuple[bool, str]:
    async with async_session() as session:
        user = await session.get(User, user_id)
        truck = await session.get(AvailableTruck, truck_id)
        if not truck:
            return False, "missing_truck"

        is_premium = bool(
            user
            and user.is_premium
            and (user.premium_until is None or user.premium_until >= datetime.now())
        )
        existing_unlock = (
            await session.execute(
                select(TruckContactUnlock).where(
                    TruckContactUnlock.user_id == user_id,
                    TruckContactUnlock.truck_id == truck_id,
                    TruckContactUnlock.status == "success",
                )
            )
        ).scalar_one_or_none()

    if is_premium or existing_unlock:
        await bot.send_message(
            chat_id,
            _render_truck_unlock_text(truck),
            reply_markup=_truck_unlock_kb(truck),
            disable_web_page_preview=True,
        )
        return True, "revealed"

    stars_amount, title = _truck_unlock_config()
    invoice_payload = f"truck_contact:{user_id}:{truck_id}:{stars_amount}:{uuid4().hex[:10]}"
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description="Открывает телефон и источник одной выбранной машины.",
        payload=invoice_payload,
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=stars_amount)],
        provider_token=None,
        start_parameter=f"truck_contact_{truck_id}",
    )
    return True, "invoice_sent"


@router.message(Command("buy_premium"))
async def buy_premium(message: Message):
    await message.answer(
        "💳 Выбери тариф Premium.\n\n"
        "Премиум открывает полный телефон и мгновенный доступ к заявкам без маски.",
        reply_markup=_build_buy_kb(),
    )


@router.callback_query(F.data.startswith("buy_premium:"))
async def buy_premium_callback(cb: CallbackQuery):
    payload_raw = (cb.data or "").split(":", maxsplit=1)
    if len(payload_raw) != 2:
        await cb.answer("Неверный тариф", show_alert=True)
        return

    try:
        days = int(payload_raw[1])
    except ValueError:
        await cb.answer("Неверный тариф", show_alert=True)
        return

    plan = _plan_config(days)
    if not plan:
        await cb.answer("Тариф не поддерживается", show_alert=True)
        return

    plan_days, stars_amount, title = plan
    invoice_payload = f"premium:{cb.from_user.id}:{plan_days}:{stars_amount}:{uuid4().hex[:10]}"

    await cb.bot.send_invoice(
        chat_id=cb.from_user.id,
        title=title,
        description="Открывает полный контакт в ленте и приоритетный доступ к новым заявкам.",
        payload=invoice_payload,
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=stars_amount)],
        provider_token=None,
        start_parameter=f"premium_{plan_days}",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("unlock_truck:"))
async def unlock_truck_callback(cb: CallbackQuery):
    payload_raw = (cb.data or "").split(":", maxsplit=1)
    if len(payload_raw) != 2:
        await cb.answer("Не удалось определить машину", show_alert=True)
        return

    try:
        truck_id = int(payload_raw[1])
    except ValueError:
        await cb.answer("Не удалось определить машину", show_alert=True)
        return

    ok, reason = await send_truck_unlock_entry(
        bot=cb.bot,
        chat_id=cb.from_user.id,
        user_id=cb.from_user.id,
        truck_id=truck_id,
    )
    if not ok and reason == "missing_truck":
        await cb.answer("Машина уже недоступна", show_alert=True)
        return
    await cb.answer()


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    payload = (pre_checkout_query.invoice_payload or "").strip()
    if payload.startswith("premium:") or payload.startswith("truck_contact:"):
        await pre_checkout_query.answer(ok=True)
        return
    await pre_checkout_query.answer(ok=False, error_message="Неизвестный счёт")


def _extend_premium(current_until: datetime | None, days: int) -> datetime:
    return extend_premium_until(current_until, days)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    payment = message.successful_payment
    if payment is None:
        return

    payload = (payment.invoice_payload or "").strip()
    if payload.startswith("cargo_contact:"):
        await _handle_cargo_contact_payment(message, payload, payment)
        return

    if payload.startswith("truck_contact:"):
        parts = payload.split(":")
        if len(parts) < 4:
            await message.answer("⚠️ Платёж получен, но не удалось определить машину.")
            return

        try:
            truck_id = int(parts[2])
        except ValueError:
            await message.answer("⚠️ Платёж получен, но машина не распознана.")
            return

        async with async_session() as session:
            truck = await session.get(AvailableTruck, truck_id)
            if not truck:
                await message.answer("⚠️ Машина уже недоступна. Напишите в поддержку.")
                return

            existing = (
                await session.execute(
                    select(TruckContactUnlock).where(
                        TruckContactUnlock.user_id == message.from_user.id,
                        TruckContactUnlock.truck_id == truck_id,
                    )
                )
            ).scalar_one_or_none()

            if existing is None:
                session.add(
                    TruckContactUnlock(
                        user_id=message.from_user.id,
                        truck_id=truck_id,
                        amount_stars=payment.total_amount,
                        currency=payment.currency,
                        status="success",
                        invoice_payload=payload,
                        telegram_payment_charge_id=payment.telegram_payment_charge_id,
                        provider_payment_charge_id=payment.provider_payment_charge_id,
                    )
                )
                await session.commit()

        await message.answer(
            _render_truck_unlock_text(truck),
            reply_markup=_truck_unlock_kb(truck),
            disable_web_page_preview=True,
        )
        return

    if not payload.startswith("premium:"):
        return

    parts = payload.split(":")
    if len(parts) < 4:
        await message.answer("⚠️ Платёж получен, но не удалось определить тариф.")
        return

    try:
        plan_days = int(parts[2])
    except ValueError:
        await message.answer("⚠️ Платёж получен, но тариф не распознан.")
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

        user.is_premium = True
        user.premium_until = _extend_premium(user.premium_until, plan_days)

        payment_row = PremiumPayment(
            user_id=message.from_user.id,
            plan_days=plan_days,
            amount_stars=payment.total_amount,
            currency=payment.currency,
            status="success",
            invoice_payload=payload,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            provider_payment_charge_id=payment.provider_payment_charge_id,
        )
        session.add(payment_row)
        await session.flush()

        referral_reward = await grant_referral_reward_if_applicable(
            session,
            invited_user_id=message.from_user.id,
            payment_id=payment_row.id,
        )
        await session.commit()

    until = user.premium_until.strftime("%d.%m.%Y %H:%M")
    await message.answer(
        f"✅ Премиум активирован до <b>{until}</b>.\n"
        "Теперь в ленте доступны полные контакты.",
        reply_markup=main_menu(),
    )

    if referral_reward:
        invited_reward_days = max(0, int(settings.referral_invited_reward_days))
        if invited_reward_days:
            await message.answer(
                "🎁 Реферальный бонус активирован.\n"
                f"Тебе начислено +{invited_reward_days} дней Premium за первую оплату по приглашению."
            )
        try:
            await message.bot.send_message(
                referral_reward.inviter_user_id,
                "🎁 По реферальной программе начислен бонус Premium: "
                f"+{referral_reward.reward_days} дней.\n"
                "Спасибо за приглашение!",
            )
        except Exception:
            pass


# ── Cargo contact unlock ──────────────────────────────────────────────────────

async def _get_cargo_contact(cargo) -> dict:
    """Возвращает {phone, username, tg_id} владельца груза."""
    phone    = cargo.phone
    username = None
    tg_id    = None
    async with async_session() as session:
        owner = await session.get(User, int(cargo.owner_id))
        if owner:
            if not phone and owner.phone:
                phone = owner.phone
            username = owner.username
            tg_id    = owner.id   # Telegram user id
    return {"phone": phone, "username": username, "tg_id": tg_id}


def _render_cargo_contact_text(cargo, contact: dict) -> str:
    lines = [
        "🔓 <b>Контакт открыт</b>",
        f"📦 Груз #{cargo.id}: {cargo.from_city} → {cargo.to_city}",
    ]
    if contact.get("phone"):
        lines.append(f"📞 {contact['phone']}")
    if contact.get("username"):
        lines.append(f"✈️ Telegram: @{contact['username']}")
    elif contact.get("tg_id"):
        lines.append(f'✈️ <a href="tg://user?id={contact['tg_id']}">Написать в Telegram</a>')
    return "\n".join(lines)


def _cargo_contact_kb(contact: dict, cargo_id: int) -> InlineKeyboardMarkup:
    rows = []
    phone = contact.get("phone") or ""
    clean = "".join(c for c in phone if c.isdigit() or c == "+")
    if clean:
        rows.append([InlineKeyboardButton(text="📞 Позвонить", url=f"tel:{clean}")])
    username = contact.get("username")
    tg_id    = contact.get("tg_id")
    if username:
        rows.append([InlineKeyboardButton(text="✈️ Написать в Telegram", url=f"https://t.me/{username}")])
    elif tg_id:
        rows.append([InlineKeyboardButton(text="✈️ Написать в Telegram", url=f"tg://user?id={tg_id}")])
    rows.append([InlineKeyboardButton(text="📦 К грузу", callback_data=f"cargo_view:{cargo_id}")])
    rows.append([InlineKeyboardButton(text="◀️ Меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# back-compat aliases (used in older call sites)
async def _get_cargo_phone(cargo) -> str | None:
    contact = await _get_cargo_contact(cargo)
    return contact.get("phone")



async def _handle_cargo_contact_payment(message, payload: str, payment) -> None:
    """Обработка успешной оплаты cargo_contact:..."""
    parts = payload.split(":")
    if len(parts) < 3:
        await message.answer("⚠️ Платёж получен, но не удалось определить груз.")
        return
    try:
        cargo_id = int(parts[2])
    except ValueError:
        await message.answer("⚠️ Платёж получен, но груз не распознан.")
        return

    from src.core.models import Cargo, CargoContactUnlock
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo:
            await message.answer("⚠️ Груз уже недоступен. Напишите в поддержку.")
            return

        existing = (
            await session.execute(
                select(CargoContactUnlock).where(
                    CargoContactUnlock.user_id == message.from_user.id,
                    CargoContactUnlock.cargo_id == cargo_id,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(CargoContactUnlock(
                user_id=message.from_user.id,
                cargo_id=cargo_id,
                amount_stars=payment.total_amount,
                currency=payment.currency,
                status="success",
                invoice_payload=payload,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
                provider_payment_charge_id=payment.provider_payment_charge_id,
            ))
            await session.commit()

    contact = await _get_cargo_contact(cargo)
    if contact.get("phone") or contact.get("username") or contact.get("tg_id"):
        await message.answer(
            _render_cargo_contact_text(cargo, contact),
            reply_markup=_cargo_contact_kb(contact, cargo_id),
            parse_mode="HTML",
        )
    else:
        await message.answer("✅ Оплата принята. Контактные данные не указаны для этого груза.")

@router.callback_query(F.data == "reveal_cargo_phone" or F.data.startswith("reveal_cargo_phone:"))
async def reveal_cargo_phone_cb(cb: CallbackQuery):
    """Премиум-юзер нажал Позвонить — сразу показываем телефон."""
    cargo_id_str = (cb.data or "").split(":")[-1]
    try:
        cargo_id = int(cargo_id_str)
    except ValueError:
        await cb.answer("Ошибка", show_alert=True)
        return

    from src.core.models import Cargo
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo:
            await cb.answer("Груз не найден", show_alert=True)
            return
        user = await session.get(User, cb.from_user.id)

    is_premium = bool(
        user
        and user.is_premium
        and (user.premium_until is None or user.premium_until >= datetime.now())
    )

    contact = await _get_cargo_contact(cargo)
    if not contact.get("phone") and not contact.get("username") and not contact.get("tg_id"):
        await cb.answer("Контакт не указан для этого груза", show_alert=True)
        return

    if is_premium:
        await cb.message.answer(
            _render_cargo_contact_text(cargo, contact),
            reply_markup=_cargo_contact_kb(contact, cargo_id),
            parse_mode="HTML",
        )
        await cb.answer()
    else:
        # Юзер как-то нажал кнопку без подписки — переключаем на оплату
        await cb.answer()
        await _send_cargo_unlock_invoice(cb.bot, cb.from_user.id, cargo_id, cargo)


async def _send_cargo_unlock_invoice(bot, user_id: int, cargo_id: int, cargo) -> None:
    stars = settings.cargo_contact_unlock_stars
    payload = f"cargo_contact:{user_id}:{cargo_id}:{stars}"
    title   = "Контакт грузовладельца"
    desc    = f"Открывает телефон по грузу {cargo.from_city} → {cargo.to_city}"
    await bot.send_message(
        user_id,
        f"💎 <b>Открой телефон за {stars} XTR</b>\n\n"
        f"Груз #{cargo_id}: {cargo.from_city} → {cargo.to_city}\n"
        f"Или оформи <b>подписку</b> и получай все контакты бесплатно:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"🔓 Открыть за {stars} ⭐",
                callback_data=f"pay_cargo_contact:{cargo_id}",
            )],
            [InlineKeyboardButton(text="💎 Подписка Premium", callback_data="buy_premium_menu")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu")],
        ]),
    )


@router.callback_query(F.data.startswith("unlock_cargo_contact:"))
async def unlock_cargo_contact_cb(cb: CallbackQuery):
    """Нажата кнопка 🔒 Показать телефон — проверяем подписку."""
    cargo_id_str = cb.data.split(":")[-1]
    try:
        cargo_id = int(cargo_id_str)
    except ValueError:
        await cb.answer("Ошибка", show_alert=True)
        return

    from src.core.models import Cargo, CargoContactUnlock
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo:
            await cb.answer("Груз не найден", show_alert=True)
            return
        user = await session.get(User, cb.from_user.id)

        is_premium = bool(
            user
            and user.is_premium
            and (user.premium_until is None or user.premium_until >= datetime.now())
        )
        existing_unlock = (
            await session.execute(
                select(CargoContactUnlock).where(
                    CargoContactUnlock.user_id == cb.from_user.id,
                    CargoContactUnlock.cargo_id == cargo_id,
                    CargoContactUnlock.status == "success",
                )
            )
        ).scalar_one_or_none()

    contact = await _get_cargo_contact(cargo)
    if not contact.get("phone") and not contact.get("username") and not contact.get("tg_id"):
        await cb.answer("Контакт не указан для этого груза", show_alert=True)
        return

    if is_premium or existing_unlock:
        await cb.message.answer(
            _render_cargo_contact_text(cargo, contact),
            reply_markup=_cargo_contact_kb(contact, cargo_id),
            parse_mode="HTML",
        )
        await cb.answer()
        return

    await cb.answer()
    await _send_cargo_unlock_invoice(cb.bot, cb.from_user.id, cargo_id, cargo)


@router.callback_query(F.data.startswith("pay_cargo_contact:"))
async def pay_cargo_contact_cb(cb: CallbackQuery):
    """Юзер нажал 'Открыть за N ⭐' — шлём invoice."""
    cargo_id_str = cb.data.split(":")[-1]
    try:
        cargo_id = int(cargo_id_str)
    except ValueError:
        await cb.answer("Ошибка", show_alert=True)
        return

    from src.core.models import Cargo
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
    if not cargo:
        await cb.answer("Груз не найден", show_alert=True)
        return

    stars   = settings.cargo_contact_unlock_stars
    payload = f"cargo_contact:{cb.from_user.id}:{cargo_id}:{stars}"
    title   = "Контакт грузовладельца"
    await cb.bot.send_invoice(
        chat_id=cb.from_user.id,
        title=title,
        description=f"Открывает телефон по грузу {cargo.from_city} → {cargo.to_city}.",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=stars)],
        provider_token=None,
        start_parameter=f"cargo_contact_{cargo_id}",
    )
    await cb.answer()
