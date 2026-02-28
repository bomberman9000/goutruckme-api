"""Bot commands for interacting with the parser feed directly in Telegram.

/feed [маршрут] — quick search the live feed
/subscribe_feed [маршрут] [тип кузова] — subscribe with extended filters
/myfeed — show saved cargos (favorites)
/market [маршрут] — route price analytics
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func, or_

from src.core.database import async_session
from src.core.geo import city_coords, haversine_km, resolve_region
from src.core.models import Favorite, ParserIngestEvent

router = Router()


def _freshness_short(created_at) -> str:
    from datetime import datetime
    now = datetime.utcnow()
    ca = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
    minutes = int((now - ca).total_seconds() / 60)
    if minutes < 60:
        return f"{minutes}м"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}ч"
    return f"{hours // 24}д"


def _rate_per_km(from_city: str | None, to_city: str | None, rate: int | None) -> str:
    if not from_city or not to_city or not rate:
        return ""
    fc = city_coords(from_city)
    tc = city_coords(to_city)
    if not fc or not tc:
        return ""
    dist = haversine_km(fc[0], fc[1], tc[0], tc[1])
    if dist < 10:
        return ""
    return f" ({int(rate / dist)} ₽/км)"


def _cargo_card(ev: ParserIngestEvent, idx: int) -> str:
    hot = "🔥 " if ev.is_hot_deal else ""
    rpk = _rate_per_km(ev.from_city, ev.to_city, ev.rate_rub)
    fresh = _freshness_short(ev.created_at)

    text = f"{idx}. {hot}<b>{ev.from_city} → {ev.to_city}</b>\n"
    text += f"   {ev.body_type or '?'} • {ev.weight_t or 0}т • {ev.rate_rub or 0:,}₽{rpk}\n"
    if ev.load_date:
        text += f"   📅 {ev.load_date}"
        if ev.load_time:
            text += f" в {ev.load_time}"
        text += "\n"
    if ev.cargo_description:
        text += f"   📦 {ev.cargo_description}\n"
    if ev.payment_terms:
        text += f"   💳 {ev.payment_terms}\n"

    badges = []
    if ev.trust_verdict:
        badge = {"green": "✅", "yellow": "⚠️", "red": "🔴"}.get(ev.trust_verdict, "")
        if badge:
            badges.append(f"{badge}{ev.trust_score or '?'}")
    if ev.phone_blacklisted:
        badges.append("⛔ЧС")
    if ev.is_direct_customer:
        badges.append("🏭")

    if badges:
        text += f"   {' '.join(badges)}\n"
    text += f"   ⏱ {fresh} назад"
    if ev.inn:
        text += f" • <a href='https://ati.su/firms?inn={ev.inn}'>АТИ</a>"
    text += f" • /cargo_{ev.id}\n"
    return text


@router.message(Command("feed"))
async def feed_search(message: Message):
    """Quick search the parser feed: /feed Москва Казань тент"""
    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""

    if not query:
        await message.answer(
            "📡 <b>Лента грузов (парсер)</b>\n\n"
            "Примеры:\n"
            "• <code>/feed Москва Казань</code>\n"
            "• <code>/feed Самара тент</code>\n"
            "• <code>/feed Сибирь</code> (по региону)\n"
            "• <code>/feed</code> — последние 5 грузов\n\n"
            "Полная лента: откройте Mini App 📱",
            parse_mode="HTML",
        )
        async with async_session() as session:
            rows = (
                await session.execute(
                    select(ParserIngestEvent)
                    .where(ParserIngestEvent.is_spam.is_(False), ParserIngestEvent.status == "synced")
                    .order_by(ParserIngestEvent.id.desc())
                    .limit(5)
                )
            ).scalars().all()

        if rows:
            text = "📦 <b>Последние грузы:</b>\n\n"
            for i, ev in enumerate(rows, 1):
                text += _cargo_card(ev, i)
                text += "\n"
            await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        return

    tokens = query.lower().split()

    from src.parser_bot.extractor import BODY_TYPES
    body_filter = None
    city_tokens = []
    for t in tokens:
        if t in BODY_TYPES:
            body_filter = BODY_TYPES[t]
        else:
            city_tokens.append(t)

    async with async_session() as session:
        stmt = (
            select(ParserIngestEvent)
            .where(ParserIngestEvent.is_spam.is_(False), ParserIngestEvent.status == "synced")
        )

        if body_filter:
            stmt = stmt.where(ParserIngestEvent.body_type.ilike(f"%{body_filter}%"))

        for city_token in city_tokens:
            region = resolve_region(city_token)
            if region:
                stmt = stmt.where(
                    or_(
                        *[ParserIngestEvent.from_city.ilike(f"%{c}%") for c in region[:5]],
                        *[ParserIngestEvent.to_city.ilike(f"%{c}%") for c in region[:5]],
                    )
                )
            else:
                capitalized = city_token.title()
                stmt = stmt.where(
                    or_(
                        ParserIngestEvent.from_city.ilike(f"%{capitalized}%"),
                        ParserIngestEvent.to_city.ilike(f"%{capitalized}%"),
                    )
                )

        rows = (
            await session.execute(stmt.order_by(ParserIngestEvent.id.desc()).limit(10))
        ).scalars().all()

    if not rows:
        await message.answer(
            f"📭 Ничего не найдено по запросу: <b>{query}</b>\n\n"
            "Попробуйте шире: <code>/feed Москва</code>",
            parse_mode="HTML",
        )
        return

    text = f"🔍 <b>Найдено {len(rows)} грузов</b> по «{query}»\n\n"
    for i, ev in enumerate(rows, 1):
        text += _cargo_card(ev, i)
        text += "\n"

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="📱 Открыть полную ленту",
        web_app={"url": "https://placeholder.gruzpotok.ru"} if False else None,
        callback_data="open_twa",
    )) if False else None

    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("loads"))
async def legacy_loads(message: Message):
    """Legacy alias for old bot users: /loads -> /feed."""
    return await feed_search(message)


@router.message(Command("cargos"))
async def legacy_cargos(message: Message):
    """Legacy alias for old bot users: /cargos -> /feed."""
    return await feed_search(message)


@router.message(Command("myfeed"))
async def my_feed(message: Message):
    """Show user's saved cargos (favorites)."""
    user_id = message.from_user.id

    async with async_session() as session:
        favs = (
            await session.execute(
                select(Favorite)
                .where(Favorite.user_id == user_id)
                .order_by(Favorite.id.desc())
                .limit(10)
            )
        ).scalars().all()

        if not favs:
            await message.answer(
                "📋 <b>Мои рейсы</b>\n\n"
                "Пусто. Сохраняйте грузы через Mini App (кнопка ⭐) "
                "или <code>/save_123</code> (где 123 — ID груза).",
                parse_mode="HTML",
            )
            return

        feed_ids = [f.feed_id for f in favs]
        events = (
            await session.execute(
                select(ParserIngestEvent).where(ParserIngestEvent.id.in_(feed_ids))
            )
        ).scalars().all()
        events_map = {e.id: e for e in events}

    status_icons = {"saved": "📌", "in_progress": "🚛", "completed": "✅", "cancelled": "❌"}
    text = f"📋 <b>Мои рейсы ({len(favs)})</b>\n\n"
    for fav in favs:
        ev = events_map.get(fav.feed_id)
        icon = status_icons.get(fav.status, "📌")
        if ev:
            text += f"{icon} <b>{ev.from_city} → {ev.to_city}</b>\n"
            text += f"   {ev.body_type or '?'} • {ev.rate_rub or 0:,}₽"
            if ev.phone:
                text += f" • 📞 {ev.phone}"
            text += "\n"
        else:
            text += f"{icon} Груз #{fav.feed_id}\n"
        if fav.note:
            text += f"   💬 {fav.note}\n"
        text += f"   /cargo_{fav.feed_id}\n\n"

    await message.answer(text, parse_mode="HTML")


@router.message(F.text.regexp(r"^/save_(\d+)"))
async def save_cargo(message: Message):
    """Save a cargo to favorites: /save_123"""
    import re
    match = re.match(r"^/save_(\d+)", message.text)
    if not match:
        return

    feed_id = int(match.group(1))
    user_id = message.from_user.id

    async with async_session() as session:
        event = await session.get(ParserIngestEvent, feed_id)
        if not event:
            await message.answer("❌ Груз не найден")
            return

        existing = await session.scalar(
            select(Favorite).where(Favorite.user_id == user_id, Favorite.feed_id == feed_id)
        )
        if existing:
            await message.answer("⭐ Уже в избранном")
            return

        fav = Favorite(user_id=user_id, feed_id=feed_id)
        session.add(fav)
        await session.commit()

    await message.answer(
        f"⭐ Сохранено: <b>{event.from_city} → {event.to_city}</b>\n"
        f"Список: /myfeed",
        parse_mode="HTML",
    )


@router.message(F.text.regexp(r"^/cargo_(\d+)"))
async def cargo_detail(message: Message):
    """Show detailed cargo card: /cargo_123"""
    import re
    match = re.match(r"^/cargo_(\d+)", message.text)
    if not match:
        return

    feed_id = int(match.group(1))

    async with async_session() as session:
        ev = await session.get(ParserIngestEvent, feed_id)
        if not ev:
            await message.answer("❌ Груз не найден")
            return

        similar = (
            await session.execute(
                select(ParserIngestEvent)
                .where(
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.id != feed_id,
                    or_(
                        ParserIngestEvent.from_city == ev.from_city,
                        ParserIngestEvent.to_city == ev.to_city,
                    ),
                )
                .order_by(ParserIngestEvent.id.desc())
                .limit(3)
            )
        ).scalars().all()

    hot = "🔥 " if ev.is_hot_deal else ""
    rpk = _rate_per_km(ev.from_city, ev.to_city, ev.rate_rub)
    fresh = _freshness_short(ev.created_at)

    text = f"{hot}📦 <b>{ev.from_city} → {ev.to_city}</b>\n\n"
    text += f"🚛 Кузов: {ev.body_type or '—'}\n"
    text += f"⚖️ Вес: {ev.weight_t or '—'} т\n"
    text += f"💰 Ставка: {ev.rate_rub or 0:,} ₽{rpk}\n"
    if ev.load_date:
        text += f"📅 Дата: {ev.load_date}"
        if ev.load_time:
            text += f" в {ev.load_time}"
        text += "\n"
    if ev.cargo_description:
        text += f"📦 Груз: {ev.cargo_description}\n"
    if ev.payment_terms:
        text += f"💳 Оплата: {ev.payment_terms}\n"
    if ev.dimensions:
        text += f"📐 Габариты: {ev.dimensions}\n"
    if ev.is_direct_customer is not None:
        text += f"{'🏭 Прямой заказчик' if ev.is_direct_customer else '👤 Посредник'}\n"

    text += f"\n⏱ {fresh} назад"
    if ev.trust_verdict:
        badge = {"green": "✅", "yellow": "⚠️", "red": "🔴"}.get(ev.trust_verdict, "❓")
        text += f" • {badge} Надёжность: {ev.trust_score}/100"
    if ev.phone_blacklisted:
        text += "\n⛔ <b>Телефон в чёрном списке!</b>"
    text += "\n"

    if ev.phone:
        text += f"\n📞 <b>{ev.phone}</b>\n"
    if ev.inn:
        text += f"🏢 ИНН: {ev.inn} (<a href='https://ati.su/firms?inn={ev.inn}'>АТИ</a>)\n"

    if ev.suggested_response:
        text += f"\n✉️ <i>Готовый отклик:</i>\n<code>{ev.suggested_response}</code>\n"

    text += f"\n⭐ Сохранить: /save_{ev.id}"

    if similar:
        text += "\n\n📦 <b>Похожие грузы:</b>\n"
        for s in similar:
            text += f"  • {s.from_city} → {s.to_city} | {s.rate_rub or 0:,}₽ → /cargo_{s.id}\n"

    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("market"))
async def market_command(message: Message):
    """Route price analytics: /market Москва Казань"""
    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""

    if not query:
        await message.answer(
            "📊 <b>Аналитика маршрута</b>\n\n"
            "Пример: <code>/market Москва Казань</code>",
            parse_mode="HTML",
        )
        return

    tokens = query.split()
    if len(tokens) < 2:
        await message.answer("Укажите два города: <code>/market Москва Казань</code>", parse_mode="HTML")
        return

    from_city = tokens[0].strip().title()
    to_city = tokens[1].strip().title()

    from datetime import timedelta, datetime
    cutoff = datetime.utcnow() - timedelta(days=30)

    async with async_session() as session:
        stats = (
            await session.execute(
                select(
                    func.count().label("cnt"),
                    func.avg(ParserIngestEvent.rate_rub).label("avg"),
                    func.min(ParserIngestEvent.rate_rub).label("mn"),
                    func.max(ParserIngestEvent.rate_rub).label("mx"),
                )
                .where(
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.rate_rub.isnot(None),
                    ParserIngestEvent.from_city.ilike(f"%{from_city}%"),
                    ParserIngestEvent.to_city.ilike(f"%{to_city}%"),
                    ParserIngestEvent.created_at >= cutoff,
                )
            )
        ).one()

    cnt = stats.cnt or 0
    if not cnt:
        await message.answer(f"📭 Нет данных по маршруту {from_city} → {to_city} за 30 дней")
        return

    avg = int(stats.avg or 0)
    dist = None
    fc = city_coords(from_city)
    tc = city_coords(to_city)
    if fc and tc:
        dist = int(haversine_km(fc[0], fc[1], tc[0], tc[1]))

    text = f"📊 <b>Аналитика: {from_city} → {to_city}</b>\n"
    text += "📅 За последние 30 дней\n\n"
    text += f"📦 Грузов: <b>{cnt}</b>\n"
    text += f"💰 Ставка: {int(stats.mn):,} — {int(stats.mx):,} ₽\n"
    text += f"📈 Средняя: <b>{avg:,} ₽</b>\n"
    if dist:
        text += f"📏 Расстояние: ~{dist} км\n"
        text += f"💵 Средняя ₽/км: <b>{avg // dist}</b>\n"

    await message.answer(text, parse_mode="HTML")


@router.message(Command("check"))
async def check_contractor(message: Message):
    """Check INN or phone via gruzpotok-api: /check 7707083893"""
    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""

    if not query:
        await message.answer(
            "🔍 <b>Проверка контрагента</b>\n\n"
            "• <code>/check 7707083893</code> — ИНН\n"
            "• <code>/check +79991112233</code> — телефон",
            parse_mode="HTML",
        )
        return

    from src.core.services.gruzpotok_bridge import verify_inn, verify_phone

    digits_only = "".join(ch for ch in query if ch.isdigit())

    if query.startswith("+") or (len(digits_only) == 11 and digits_only.startswith(("7", "8"))):
        data = await verify_phone(query)
        if not data:
            await message.answer("❌ Сервис проверки недоступен")
            return
        text = f"📞 <b>Телефон: {data.get('formatted', query)}</b>\n"
        text += f"Валиден: {'✅' if data.get('valid') else '❌'}\n"
        if data.get("operator") and data["operator"] != "Unknown":
            text += f"Оператор: {data['operator']}\n"
        await message.answer(text, parse_mode="HTML")
        return

    if len(digits_only) in (10, 12):
        data = await verify_inn(digits_only)
        if not data:
            await message.answer("❌ Сервис проверки недоступен")
            return
        icon = "✅" if data.get("valid") else "❌"
        text = f"🏢 <b>ИНН: {digits_only}</b>\n"
        text += f"{icon} {data.get('message', '—')}\n"
        text += f"\n🔗 <a href='https://ati.su/firms?inn={digits_only}'>АТИ профиль</a>"
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        return

    await message.answer("❓ Укажите ИНН (10-12 цифр) или телефон (+7...)")


@router.message(Command("route"))
async def route_command(message: Message):
    """Exact route distance: /route Москва Казань"""
    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""

    if not query or len(query.split()) < 2:
        await message.answer("📏 Пример: <code>/route Москва Казань</code>", parse_mode="HTML")
        return

    tokens = query.split(maxsplit=1)
    from_city = tokens[0].strip().title()
    to_city = tokens[1].strip().title()

    from src.core.services.gruzpotok_bridge import calc_route
    data = await calc_route(from_city, to_city)

    if not data:
        fc = city_coords(from_city)
        tc = city_coords(to_city)
        if fc and tc:
            d = int(haversine_km(fc[0], fc[1], tc[0], tc[1]))
            await message.answer(f"📏 {from_city} → {to_city}: ~{d} км (по прямой)", parse_mode="HTML")
        else:
            await message.answer("❌ Не удалось рассчитать")
        return

    d = data.get("distance_km", 0)
    fn = data.get("from", {}).get("city_name", from_city)
    tn = data.get("to", {}).get("city_name", to_city)
    text = f"📏 <b>{fn} → {tn}: {d} км</b>\n"
    text += f"💰 ~{d * 40:,} ₽ (avg 40 ₽/км)\n"
    text += f"📊 Диапазон: {d * 30:,} — {d * 55:,} ₽"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("passport"))
async def passport_command(message: Message):
    """Company trust passport: /passport 7707083893"""
    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""

    digits = "".join(ch for ch in query if ch.isdigit())
    if len(digits) not in (10, 12):
        await message.answer(
            "🪪 <b>Паспорт компании</b>\n\n"
            "Пример: <code>/passport 7707083893</code>",
            parse_mode="HTML",
        )
        return

    from src.core.services.company_profile import build_company_passport
    passport = await build_company_passport(digits)

    verdict_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(passport["verdict"], "⚪")
    score = passport["trust_score"]
    stars = "★" * (score // 20) + "☆" * (5 - score // 20)

    text = "🪪 <b>Паспорт компании</b>\n\n"
    text += f"🏢 {passport.get('name') or 'Не определено'}\n"
    text += f"ИНН: <code>{digits}</code>\n"
    text += f"📅 {passport.get('age_label', '?')}\n\n"

    text += f"{verdict_icon} <b>Trust Score: {score}/100</b> {stars}\n\n"

    comps = passport.get("components", {})
    age_c = comps.get("age", {})
    act_c = comps.get("activity", {})
    fin_c = comps.get("finance", {})
    flt_c = comps.get("fleet", {})

    text += "<b>Компоненты:</b>\n"
    text += f"  📅 Возраст: {age_c.get('score', 0)}/{age_c.get('max', 30)} ({age_c.get('label', '?')})\n"
    text += f"  📱 TG-активность: {act_c.get('score', 0)}/{act_c.get('max', 20)} ({act_c.get('telegram_posts', 0)} постов)\n"
    text += f"  💰 Финансы: {fin_c.get('score', 0)}/{fin_c.get('max', 30)}\n"
    text += f"  🚛 Парк: {flt_c.get('score', 0)}/{flt_c.get('max', 20)}\n"

    flags = passport.get("flags", [])
    if flags:
        flag_icons = {
            "liquidating": "⚠️ Ликвидация",
            "active_lawsuits": "⚠️ Судебные иски",
            "very_new": "🆕 Очень молодая",
            "low_capital": "💸 Низкий капитал",
            "active_in_chats": "✅ Активна в ТГ",
            "no_telegram_activity": "📵 Нет в ТГ-чатах",
        }
        text += "\n<b>Флаги:</b>\n"
        for f in flags:
            text += f"  {flag_icons.get(f, f)}\n"

    text += f"\n🔗 <a href='{passport.get('ati_link', '')}'>Профиль на АТИ</a>"
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
