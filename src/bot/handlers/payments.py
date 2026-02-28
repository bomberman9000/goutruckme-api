from __future__ import annotations

from datetime import datetime
from uuid import uuid4

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
from src.core.models import PremiumPayment, User
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


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    payload = (pre_checkout_query.invoice_payload or "").strip()
    if payload.startswith("premium:"):
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
