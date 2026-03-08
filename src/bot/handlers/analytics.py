from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, func, desc
from datetime import datetime, timedelta
from src.bot.keyboards import analytics_menu
from src.core.database import async_session
from src.core.models import Cargo, CargoStatus

router = Router()

@router.callback_query(F.data == "analytics")
async def show_analytics(cb: CallbackQuery):
    try:
        await cb.message.edit_text("📊 <b>Аналитика</b>\n\nВыбери раздел:", reply_markup=analytics_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data == "my_earnings")
async def my_earnings(cb: CallbackQuery):
    async with async_session() as session:
        carrier_result = await session.execute(
            select(Cargo)
            .where(Cargo.carrier_id == cb.from_user.id)
            .where(Cargo.status == CargoStatus.COMPLETED)
        )
        carrier_cargos = carrier_result.scalars().all()

        owner_result = await session.execute(
            select(Cargo)
            .where(Cargo.owner_id == cb.from_user.id)
            .where(Cargo.status == CargoStatus.COMPLETED)
        )
        owner_cargos = owner_result.scalars().all()

        month_ago = datetime.utcnow() - timedelta(days=30)
        month_carrier = await session.execute(
            select(Cargo)
            .where(Cargo.carrier_id == cb.from_user.id)
            .where(Cargo.status == CargoStatus.COMPLETED)
            .where(Cargo.created_at >= month_ago)
        )
        month_cargos = month_carrier.scalars().all()

    total_earned = sum(c.actual_price or c.price for c in carrier_cargos)
    total_spent = sum(c.actual_price or c.price for c in owner_cargos)
    month_earned = sum(c.actual_price or c.price for c in month_cargos)

    text = "💰 <b>Мой заработок</b>\n\n"
    text += "<b>Как перевозчик:</b>\n"
    text += f"   Перевозок: {len(carrier_cargos)}\n"
    text += f"   Заработано: {total_earned:,} ₽\n"
    text += f"   За месяц: {month_earned:,} ₽\n\n"
    text += "<b>Как заказчик:</b>\n"
    text += f"   Заказов: {len(owner_cargos)}\n"
    text += f"   Потрачено: {total_spent:,} ₽"

    try:
        await cb.message.edit_text(text, reply_markup=analytics_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data == "my_routes")
async def my_routes(cb: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(
            select(
                Cargo.from_city,
                Cargo.to_city,
                func.count().label('count'),
                func.avg(Cargo.price).label('avg_price')
            )
            .where(Cargo.carrier_id == cb.from_user.id)
            .where(Cargo.status == CargoStatus.COMPLETED)
            .group_by(Cargo.from_city, Cargo.to_city)
            .order_by(desc('count'))
            .limit(10)
        )
        routes = result.all()

    if not routes:
        try:
            await cb.message.edit_text("📊 Нет завершённых перевозок", reply_markup=analytics_menu())
        except TelegramBadRequest:
            pass
        await cb.answer()
        return

    text = "📊 <b>Мои маршруты:</b>\n\n"
    for r in routes:
        text += f"🛣 {r.from_city} → {r.to_city}\n"
        text += f"   Рейсов: {r.count} | Средняя: {int(r.avg_price):,} ₽\n\n"

    try:
        await cb.message.edit_text(text, reply_markup=analytics_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data == "popular_routes")
async def popular_routes(cb: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(
            select(
                Cargo.from_city,
                Cargo.to_city,
                func.count().label('count'),
                func.avg(Cargo.price).label('avg_price')
            )
            .group_by(Cargo.from_city, Cargo.to_city)
            .order_by(desc('count'))
            .limit(10)
        )
        routes = result.all()

    if not routes:
        try:
            await cb.message.edit_text("📊 Нет данных", reply_markup=analytics_menu())
        except TelegramBadRequest:
            pass
        await cb.answer()
        return

    text = "🔥 <b>Популярные маршруты:</b>\n\n"
    for i, r in enumerate(routes, 1):
        text += f"{i}. {r.from_city} → {r.to_city}\n"
        text += f"   Грузов: {r.count} | Средняя: {int(r.avg_price):,} ₽\n\n"

    try:
        await cb.message.edit_text(text, reply_markup=analytics_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data == "avg_prices")
async def avg_prices(cb: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(
            select(func.avg(Cargo.price / Cargo.weight).label('price_per_ton'))
            .where(Cargo.weight > 0)
        )
        avg_per_ton = result.scalar() or 0

        expensive = await session.execute(
            select(
                Cargo.from_city,
                Cargo.to_city,
                func.avg(Cargo.price).label('avg_price')
            )
            .group_by(Cargo.from_city, Cargo.to_city)
            .having(func.count() >= 1)
            .order_by(desc('avg_price'))
            .limit(5)
        )
        top_routes = expensive.all()

    text = "📈 <b>Средние цены:</b>\n\n"
    text += f"💰 Цена за тонну: {int(avg_per_ton):,} ₽\n\n"
    text += "<b>Топ дорогих маршрутов:</b>\n"
    for r in top_routes:
        text += f"• {r.from_city} → {r.to_city}: {int(r.avg_price):,} ₽\n"

    try:
        await cb.message.edit_text(text, reply_markup=analytics_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()
