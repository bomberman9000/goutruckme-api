from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import func, select

from src.core.database import async_session
from src.core.models import ReferralInvite, ReferralReward
from src.core.services.referral import build_referral_deeplink
from src.core.config import settings


router = Router()


@router.message(Command("referral"))
@router.message(Command("invite"))
async def referral_info(message: Message):
    link = build_referral_deeplink(settings.bot_username, message.from_user.id)
    if not link:
        await message.answer("⚠️ BOT_USERNAME не настроен. Невозможно создать реферальную ссылку.")
        return

    async with async_session() as session:
        invited_count = await session.scalar(
            select(func.count()).select_from(ReferralInvite).where(
                ReferralInvite.inviter_user_id == message.from_user.id
            )
        )
        rewards_count = await session.scalar(
            select(func.count()).select_from(ReferralReward).where(
                ReferralReward.inviter_user_id == message.from_user.id
            )
        )

    await message.answer(
        "🎁 <b>Реферальная программа</b>\n\n"
        f"Приглашай коллег: за первую оплату приглашенного тебе начисляется +{settings.referral_reward_days} дней Premium.\n\n"
        f"🔗 Твоя ссылка:\n{link}\n\n"
        f"👥 Приглашено: {invited_count or 0}\n"
        f"🏆 Начислено бонусов: {rewards_count or 0}"
    )
