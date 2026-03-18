"""Push-notification service: cargo notifications for matching carriers."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select

from src.core.database import async_session
from src.core.logger import logger
from src.core.models import (
    Cargo,
    CompanyDetails,
    RouteSubscription,
    User,
    UserVehicle,
)


async def collect_matching_route_subscriber_ids(session, cargo: Cargo) -> list[int]:
    result = await session.execute(
        select(RouteSubscription)
        .where(RouteSubscription.is_active.is_(True))
        .where(
            or_(
                RouteSubscription.from_city.is_(None),
                RouteSubscription.from_city.ilike(f"%{cargo.from_city}%"),
            )
        )
        .where(
            or_(
                RouteSubscription.to_city.is_(None),
                RouteSubscription.to_city.ilike(f"%{cargo.to_city}%"),
            )
        )
    )
    subscribers = result.scalars().all()
    return [int(sub.user_id) for sub in subscribers if int(sub.user_id) != int(cargo.owner_id)]


async def collect_matching_available_vehicle_user_ids(session, cargo: Cargo) -> list[int]:
    rows = (
        await session.execute(
            select(UserVehicle).where(
                UserVehicle.is_available.is_(True),
                UserVehicle.location_city.is_not(None),
                UserVehicle.location_city.ilike(f"%{cargo.from_city}%"),
            )
        )
    ).scalars().all()

    matches: list[int] = []
    cargo_body = (cargo.cargo_type or "").strip().lower()
    for vehicle in rows:
        if int(vehicle.user_id) == int(cargo.owner_id):
            continue
        if vehicle.capacity_tons and cargo.weight and float(cargo.weight) > float(vehicle.capacity_tons):
            continue
        vehicle_body = (vehicle.body_type or "").strip().lower()
        if cargo_body and vehicle_body:
            if cargo_body not in vehicle_body and vehicle_body not in cargo_body:
                continue
        matches.append(int(vehicle.user_id))
    return matches


def _mask_phone(phone: str) -> str:
    """Скрывает последние 4 цифры: +7 900 *** **67 → +7 900 *** **XX"""
    digits = [c for c in phone if c.isdigit()]
    if len(digits) < 5:
        return "📞 Скрыт"
    masked = phone[:-4] + "XXXX"
    return masked


def _build_cargo_notification_text(
    cargo: Cargo,
    owner_company: CompanyDetails | None,
    owner: User | None,
    *,
    show_phone: bool = False,
) -> str:
    text = "🔔 <b>Новый груз по вашему маршруту!</b>\n\n"
    text += f"📍 {cargo.from_city} → {cargo.to_city}\n"
    text += f"📦 {cargo.cargo_type} | {cargo.weight} т\n"
    text += f"💰 {cargo.price:,} ₽\n"
    text += f"📅 {cargo.load_date.strftime('%d.%m.%Y')}"
    if cargo.load_time:
        text += f" в {cargo.load_time}"
    text += "\n"

    if owner_company:
        rating = owner_company.total_rating
        stars = "⭐" * rating + "☆" * (10 - rating)
        name = owner_company.company_name or "Компания"
        text += f"\n🏢 {name} | {stars} ({rating}/10)\n"
    elif owner:
        text += f"\n👤 {owner.full_name}\n"

    phone = cargo.phone or (owner.phone if owner else None)
    if phone:
        if show_phone:
            text += f"\n📞 {phone}"
        else:
            text += f"\n📞 {_mask_phone(phone)}"
            text += "  🔒 <i>Откройте подпиской</i>"
    return text


async def dispatch_cargo_notification(cargo: Cargo, user_ids: list[int]) -> int:
    if not user_ids:
        return 0

    from src.bot.bot import bot
    from src.bot.keyboards import notification_kb

    async with async_session() as session:
        owner_company = await session.scalar(
            select(CompanyDetails).where(CompanyDetails.user_id == cargo.owner_id)
        )
        owner = await session.scalar(select(User).where(User.id == cargo.owner_id))
        users = (
            await session.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all()
    users_map = {u.id: u for u in users}

    has_phone = bool(cargo.phone or (owner and owner.phone))

    sent = 0
    for user_id in user_ids:
        try:
            recipient = users_map.get(user_id)
            is_premium = bool(
                recipient
                and recipient.is_premium
                and (
                    recipient.premium_until is None
                    or recipient.premium_until >= datetime.utcnow()
                )
            )
            text = _build_cargo_notification_text(
                cargo, owner_company, owner, show_phone=is_premium or not has_phone
            )
            kb = notification_kb(cargo.id, is_premium=is_premium, has_phone=has_phone)
            await bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML")
            sent += 1
        except Exception:
            pass
    return sent


async def notify_subscribers(cargo: Cargo) -> int:
    """Send cargo notifications to route subscribers only."""
    async with async_session() as session:
        target_ids = await collect_matching_route_subscriber_ids(session, cargo)
    sent = await dispatch_cargo_notification(cargo, target_ids)
    if sent:
        async with async_session() as session:
            current = await session.get(Cargo, cargo.id)
            if current:
                current.notified_at = datetime.utcnow()
                await session.commit()

    logger.info(
        "Notified %d route subscribers for cargo #%d",
        sent,
        cargo.id,
    )
    return sent
