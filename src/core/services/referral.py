from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.models import ReferralInvite, ReferralReward, User


def build_referral_deeplink(bot_username: str | None, inviter_user_id: int) -> str | None:
    username = (bot_username or "").strip().lstrip("@")
    if not username or inviter_user_id <= 0:
        return None
    return f"https://t.me/{username}?start=ref_{inviter_user_id}"


def extend_premium_until(current_until: datetime | None, days: int) -> datetime:
    now = datetime.now()
    base = current_until if current_until and current_until > now else now
    safe_days = max(1, int(days))
    return base + timedelta(days=safe_days)


async def attach_referral_invite(
    session: AsyncSession,
    *,
    inviter_user_id: int,
    invited_user_id: int,
    source_payload: str | None = None,
) -> tuple[bool, str]:
    if inviter_user_id <= 0 or invited_user_id <= 0:
        return False, "invalid_user_id"
    if inviter_user_id == invited_user_id:
        return False, "self_invite"

    inviter = await session.get(User, inviter_user_id)
    if not inviter:
        return False, "inviter_not_found"

    existing = await session.scalar(
        select(ReferralInvite).where(ReferralInvite.invited_user_id == invited_user_id)
    )
    if existing:
        return False, "already_has_inviter"

    session.add(
        ReferralInvite(
            inviter_user_id=inviter_user_id,
            invited_user_id=invited_user_id,
            source_payload=(source_payload or "")[:255] or None,
        )
    )
    return True, "created"


async def grant_referral_reward_if_applicable(
    session: AsyncSession,
    *,
    invited_user_id: int,
    payment_id: int,
) -> ReferralReward | None:
    invite = await session.scalar(
        select(ReferralInvite).where(ReferralInvite.invited_user_id == invited_user_id)
    )
    if not invite:
        return None
    if invite.rewarded_at is not None:
        return None
    if invite.inviter_user_id == invited_user_id:
        return None

    existing_reward = await session.scalar(
        select(ReferralReward).where(ReferralReward.payment_id == payment_id)
    )
    if existing_reward:
        return None

    inviter = await session.get(User, invite.inviter_user_id)
    invited = await session.get(User, invited_user_id)
    if not inviter or not invited:
        return None

    reward_days = max(1, int(settings.referral_reward_days))
    invited_reward_days = max(0, int(settings.referral_invited_reward_days))
    inviter.is_premium = True
    inviter.premium_until = extend_premium_until(inviter.premium_until, reward_days)
    if invited_reward_days:
        invited.is_premium = True
        invited.premium_until = extend_premium_until(invited.premium_until, invited_reward_days)

    now = datetime.now()
    invite.rewarded_at = now
    invite.reward_days = reward_days
    invite.trigger_payment_id = payment_id

    reward = ReferralReward(
        inviter_user_id=invite.inviter_user_id,
        invited_user_id=invite.invited_user_id,
        payment_id=payment_id,
        reward_days=reward_days,
    )
    session.add(reward)
    return reward
