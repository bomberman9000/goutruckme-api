from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, or_, func
from datetime import datetime, timedelta
import json
import re
from src.bot.states import CargoForm, EditCargo, CargoNLPConfirm, CargoTracking
from src.bot.keyboards import main_menu, confirm_kb, cargo_actions, cargos_menu, cargo_open_list_kb, skip_kb, response_actions, deal_actions, city_kb, delete_confirm_kb, my_cargos_kb, cargo_edit_kb, price_suggest_kb
from src.bot.utils import cargo_deeplink
from src.bot.utils.cities import city_suggest
from src.core.ai import parse_city, parse_load_datetime, parse_cargo_nlp
from src.core.database import async_session
from src.core.models import (
    Cargo,
    CargoStatus,
    CargoResponse,
    User,
    RouteSubscription,
    Rating,
    UserProfile,
    VerificationStatus,
    CompanyDetails,
    Claim,
    ClaimStatus,
    ParserIngestEvent,
)
from src.core.schemas.sync import SharedOrderSchema, SharedSyncEvent
from src.core.cache import clear_cached
from src.core.services.cross_sync import make_search_id, publish_sync_event
from src.core.services.geo_service import get_geo_service
from src.core.logger import logger
from src.bot.bot import bot
import asyncio

router = Router()


async def _gemini_logist_analyze(cargo_text: str) -> dict | None:
    """Анализ груза через Gemini: риски + вопросы только о реально отсутствующих данных."""
    import httpx, json as _json
    from src.core.config import settings
    api_key = settings.gemini_api_key
    if not api_key:
        return None
    prompt = (
        "Ты AI-логист. Проанализируй заявку на перевозку.\n"
        "ПРАВИЛА:\n"
        "1. Вес в тоннах — достаточно, объём в м³ НЕ спрашивай.\n"
        "2. В 'questions' включай вопрос ТОЛЬКО если реально нет: маршрута (откуда/куда), веса/тоннажа, типа ТС.\n"
        "3. Если дата не указана — это риск, не вопрос.\n"
        "4. Не дублируй в вопросах то, что уже есть в заявке.\n\n"
        f"Заявка: {cargo_text}\n\n"
        "Верни ТОЛЬКО JSON:\n"
        "{\n"
        '  "risks": ["риск1", "риск2"],\n'
        '  "questions": ["вопрос если критично не хватает данных"]\n'
        "}"
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={api_key}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "response_mime_type": "application/json"},
            })
            if r.status_code != 200:
                return None
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            return _json.loads(text)
    except Exception as e:
        logger.warning("gemini_logist_analyze error: %s", e)
        return None


async def _run_ai_after_cargo(
    cargo_id: int,
    owner_id: int,
    cargo_text: str,
) -> None:
    """Background: antifraud check + AI logist analysis sent to user."""
    from src.services.cargo_antifraud import run_antifraud_check, RISK_HIGH_THRESHOLD

    # 1. Antifraud
    try:
        fraud = await run_antifraud_check(cargo_id, cargo_text, owner_id)
        risk = int(fraud.get("risk_score") or 0)
        if risk >= RISK_HIGH_THRESHOLD:
            await bot.send_message(
                owner_id,
                f"⚠️ AI выявил высокий риск в вашей заявке #{cargo_id} (риск {risk}/100).\n"
                "Менеджер проверит заявку в ближайшее время.",
            )
    except Exception as e:
        logger.warning("bg.antifraud cargo_id=%d error=%s", cargo_id, e)

    # 2. Gemini logist analysis → send to user
    try:
        result = await _gemini_logist_analyze(cargo_text)
        if not result:
            return
        lines = [f"🤖 <b>AI-анализ груза #{cargo_id}</b>"]

        risks = result.get("risks") or []
        if risks:
            lines.append(f"\n⚠️ Риски: {', '.join(str(r) for r in risks[:3])}")

        questions = result.get("questions") or []
        if questions:
            lines.append(f"\n❓ {questions[0]}")

        if len(lines) > 1:
            await bot.send_message(owner_id, "\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.warning("bg.ai_logist cargo_id=%d error=%s", cargo_id, e)

CANCEL_HINT = "\n\n❌ Отмена: /cancel"
STOP_WORDS = {"да", "ок", "okay", "привет", "hello", "hi", "угу", "ага"}

def _looks_like_city(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t or t in STOP_WORDS:
        return False
    if len(t) < 3:
        return False
    return bool(re.search(r"[а-яА-Я]", t))


def _verification_label(profile: UserProfile | None) -> str:
    if not profile:
        return "обычный"
    if profile.verification_status == VerificationStatus.VERIFIED:
        return "верифицирован"
    if profile.verification_status == VerificationStatus.CONFIRMED:
        return "подтверждён"
    return "обычный"


def _is_premium_active(user: User | None) -> bool:
    if not user or not user.is_premium:
        return False
    if user.premium_until is None:
        return True
    return user.premium_until >= datetime.now()


def _manual_feed_verdict(score: int) -> str:
    if score >= 75:
        return "green"
    if score >= 45:
        return "yellow"
    return "red"


def _manual_feed_matches(details_json: str | None, cargo_id: int) -> bool:
    return bool(details_json and f"\"cargo_id\": {cargo_id}" in details_json)


def _manual_feed_raw_text(cargo: Cargo) -> str:
    bits = [
        f"{cargo.from_city} -> {cargo.to_city}",
        f"{cargo.cargo_type}",
        f"{cargo.weight:g} т",
        f"{int(cargo.price)} ₽",
    ]
    if cargo.comment:
        bits.append(cargo.comment)
    return "\n".join(bits)


async def _latest_manual_feed_event(session, owner_id: int, cargo_id: int) -> ParserIngestEvent | None:
    rows = (
        await session.execute(
            select(ParserIngestEvent)
            .where(
                ParserIngestEvent.source == "manual_client",
                ParserIngestEvent.chat_id == f"user:{owner_id}",
            )
            .order_by(ParserIngestEvent.id.desc())
            .limit(100)
        )
    ).scalars().all()
    for row in rows:
        if _manual_feed_matches(getattr(row, "details_json", None), cargo_id):
            return row
    return None


async def _sync_manual_feed_event(
    cargo: Cargo,
    *,
    source_note: str,
    republish: bool = False,
) -> int | None:
    async with async_session() as session:
        db_cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo.id))
        if not db_cargo:
            return None

        owner = await session.scalar(select(User).where(User.id == db_cargo.owner_id))
        latest = await _latest_manual_feed_event(session, int(db_cargo.owner_id), int(db_cargo.id))
        route_geo = await get_geo_service().resolve_route(db_cargo.from_city, db_cargo.to_city)
        trust_score = int(getattr(owner, "trust_score", 50) or 50)
        details = {
            "created_via": source_note,
            "cargo_id": int(db_cargo.id),
            "owner_id": int(db_cargo.owner_id),
            "distance_km": route_geo.distance_km if route_geo else None,
            "volume_m3": db_cargo.volume,
            "source_platform": db_cargo.source_platform,
        }

        if latest and not republish:
            latest.from_city = db_cargo.from_city
            latest.to_city = db_cargo.to_city
            latest.body_type = db_cargo.cargo_type
            latest.phone = owner.phone if owner else None
            latest.rate_rub = int(db_cargo.price)
            latest.weight_t = float(db_cargo.weight)
            latest.load_date = db_cargo.load_date.date().isoformat() if db_cargo.load_date else None
            latest.load_time = db_cargo.load_time
            latest.cargo_description = db_cargo.comment or db_cargo.cargo_type
            latest.is_direct_customer = True
            latest.dimensions = f"{db_cargo.volume:g} м³" if db_cargo.volume is not None else None
            latest.from_lat = route_geo.origin.lat if route_geo else None
            latest.from_lon = route_geo.origin.lon if route_geo else None
            latest.to_lat = route_geo.destination.lat if route_geo else None
            latest.to_lon = route_geo.destination.lon if route_geo else None
            latest.trust_score = trust_score
            latest.trust_verdict = _manual_feed_verdict(trust_score)
            latest.trust_comment = "Ручное размещение через Telegram-бот"
            latest.provider = "manual"
            latest.is_spam = False
            latest.status = "synced"
            latest.raw_text = _manual_feed_raw_text(db_cargo)
            latest.details_json = json.dumps(details, ensure_ascii=False)
            await session.commit()
            await clear_cached("feed")
            return int(latest.id)

        if latest and republish:
            latest.status = "replaced"

        event = ParserIngestEvent(
            stream_entry_id=f"manual-bot-{make_search_id()}",
            chat_id=f"user:{db_cargo.owner_id}",
            message_id=0,
            source="manual_client",
            from_city=db_cargo.from_city,
            to_city=db_cargo.to_city,
            body_type=db_cargo.cargo_type,
            phone=owner.phone if owner else None,
            inn=None,
            rate_rub=int(db_cargo.price),
            weight_t=float(db_cargo.weight),
            load_date=db_cargo.load_date.date().isoformat() if db_cargo.load_date else None,
            load_time=db_cargo.load_time,
            cargo_description=db_cargo.comment or db_cargo.cargo_type,
            payment_terms=None,
            is_direct_customer=True,
            dimensions=f"{db_cargo.volume:g} м³" if db_cargo.volume is not None else None,
            is_hot_deal=False,
            suggested_response=None,
            phone_blacklisted=False,
            from_lat=route_geo.origin.lat if route_geo else None,
            from_lon=route_geo.origin.lon if route_geo else None,
            to_lat=route_geo.destination.lat if route_geo else None,
            to_lon=route_geo.destination.lon if route_geo else None,
            trust_score=trust_score,
            trust_verdict=_manual_feed_verdict(trust_score),
            trust_comment="Ручное размещение через Telegram-бот",
            provider="manual",
            is_spam=False,
            status="synced",
            raw_text=_manual_feed_raw_text(db_cargo),
            details_json=json.dumps(details, ensure_ascii=False),
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)

    await clear_cached("feed")
    return int(event.id)


def _format_bump_wait(remaining: timedelta) -> str:
    total_seconds = max(0, int(remaining.total_seconds()))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{seconds} сек")
    return " ".join(parts)


async def _publish_cargo_sync_event(cargo: Cargo, *, event_type: str) -> None:
    load_date = cargo.load_date.strftime("%Y-%m-%d") if cargo.load_date else None
    order = SharedOrderSchema(
        id=str(cargo.id),
        search_id=f"cargo_{cargo.id}",
        user_id=int(cargo.owner_id),
        from_city=cargo.from_city,
        to_city=cargo.to_city,
        cargo_type=cargo.cargo_type,
        weight_t=float(cargo.weight),
        price_rub=int(cargo.price),
        load_date=load_date,
        status=cargo.status.value if cargo.status else None,
        source="tg-bot",
    )
    event = SharedSyncEvent(
        event_id=make_search_id(),
        event_type=event_type,
        source="tg-bot",
        search_id=order.search_id,
        user_id=order.user_id,
        order=order,
        metadata={"origin": "bot"},
    )
    await publish_sync_event(event)

async def render_cargo_card(session, cargo: Cargo, viewer_id: int) -> tuple[str, bool, int | None, bool]:
    owner = await session.scalar(select(User).where(User.id == cargo.owner_id))
    owner_profile = await session.scalar(select(UserProfile).where(UserProfile.user_id == cargo.owner_id))
    owner_company = await session.scalar(
        select(CompanyDetails).where(CompanyDetails.user_id == cargo.owner_id)
    )

    avg_rating = await session.scalar(
        select(func.avg(Rating.score)).where(Rating.to_user_id == cargo.owner_id)
    )
    rating_count = await session.scalar(
        select(func.count()).select_from(Rating).where(Rating.to_user_id == cargo.owner_id)
    )

    status_map = {
        "new": "🆕 Новый",
        "in_progress": "🚛 В работе",
        "completed": "✅ Завершён",
        "cancelled": "❌ Отменён",
        "archived": "🗄️ Архив",
        "active": "🆕 Новый",
    }

    text = f"📦 <b>Груз #{cargo.id}</b>\n\n"
    text += f"📍 {cargo.from_city} → {cargo.to_city}\n"
    text += f"📦 {cargo.cargo_type}\n"
    text += f"⚖️ {cargo.weight} т\n"
    price_label = "Рекоменд. цена" if (cargo.source_platform and cargo.source_platform != "manual") else "Ставка"
    text += f"💰 {price_label}: {cargo.price:,} ₽\n"
    text += f"📅 {cargo.load_date.strftime('%d.%m.%Y')}"
    if cargo.load_time:
        text += f" в {cargo.load_time}"
    text += "\n"
    text += f"📊 {status_map.get(cargo.status.value, cargo.status.value)}\n"
    if cargo.comment:
        text += f"💬 {cargo.comment}\n"

    is_owner = cargo.owner_id == viewer_id
    is_carrier = cargo.carrier_id == viewer_id if cargo.carrier_id else False
    is_participant = is_owner or is_carrier
    is_parsed = cargo.source_platform and cargo.source_platform not in {"manual", "tg-bot"}
    can_show_contacts = is_parsed or (is_participant and cargo.status in {CargoStatus.IN_PROGRESS, CargoStatus.COMPLETED})

    if owner and not is_parsed:
        _owner_display = ("@" + owner.username) if owner.username else (owner.full_name or str(owner.id))
        text += f"\n👤 Выложил: {_owner_display}"
        if owner_company:
            rating = owner_company.total_rating
            stars = "⭐" * rating + "☆" * (10 - rating)
            text += f"\n🏢 {owner_company.company_name or 'Компания'}"
            text += f"\n📊 Рейтинг: {stars} ({rating}/10)"
        else:
            text += "\n⚠️ Компания не зарегистрирована"
        if can_show_contacts and owner.phone:
            text += f"\n📞 {owner.phone}"
        else:
            stars_old = "⭐" * round(avg_rating) if avg_rating else "нет оценок"
            text += f"\n⭐ Оценки: {stars_old} ({rating_count or 0})"
            text += f"\n🛡 Верификация: {_verification_label(owner_profile)}"
            if not is_parsed:
                text += "\n📵 Контакты скрыты до начала сделки"

    owner_company_id = owner_company.id if owner_company else None
    can_bump = is_owner and cargo.status in {CargoStatus.NEW, CargoStatus.ACTIVE} and _is_premium_active(owner)
    return text, is_owner, owner_company_id, can_bump




async def send_cargo_details(message: Message, cargo_id: int) -> bool:
    async with async_session() as session:
        cargo = (await session.execute(select(Cargo).where(Cargo.id == cargo_id))).scalar_one_or_none()

        if not cargo:
            await message.answer("❌ Груз не найден")
            return False

        owner = (await session.execute(select(User).where(User.id == cargo.owner_id))).scalar_one_or_none()
        carrier = None
        if cargo.carrier_id:
            carrier = (await session.execute(select(User).where(User.id == cargo.carrier_id))).scalar_one_or_none()

        owner_profile = await session.scalar(select(UserProfile).where(UserProfile.user_id == cargo.owner_id))
        owner_company = await session.scalar(
            select(CompanyDetails).where(CompanyDetails.user_id == cargo.owner_id)
        )

        avg_rating = await session.scalar(
            select(func.avg(Rating.score)).where(Rating.to_user_id == cargo.owner_id)
        )
        rating_count = await session.scalar(
            select(func.count()).select_from(Rating).where(Rating.to_user_id == cargo.owner_id)
        )

    status_map = {
        "new": "🆕 Новый",
        "in_progress": "🚛 В работе",
        "completed": "✅ Завершён",
        "cancelled": "❌ Отменён",
        "archived": "🗄️ Архив",
        "active": "🆕 Новый",
    }

    is_owner = cargo.owner_id == message.from_user.id
    is_carrier = cargo.carrier_id == message.from_user.id if cargo.carrier_id else False
    is_participant = is_owner or is_carrier
    is_parsed = cargo.source_platform and cargo.source_platform not in {"manual", "tg-bot"}
    can_show_contacts = is_parsed or (is_participant and cargo.status in {CargoStatus.IN_PROGRESS, CargoStatus.COMPLETED})

    text = f"📦 <b>Груз #{cargo.id}</b>\n\n"
    text += f"📍 {cargo.from_city} → {cargo.to_city}\n"
    text += f"📦 {cargo.cargo_type}\n"
    text += f"⚖️ {cargo.weight} т\n"
    price_label = "Рекоменд. цена" if (cargo.source_platform and cargo.source_platform != "manual") else "Ставка"
    text += f"💰 {price_label}: {cargo.price:,} ₽\n"
    text += f"📅 {cargo.load_date.strftime('%d.%m.%Y')}"
    if cargo.load_time:
        text += f" в {cargo.load_time}"
    text += "\n"
    text += f"📊 {status_map.get(cargo.status.value, cargo.status.value)}\n"
    if cargo.comment:
        text += f"💬 {cargo.comment}\n"

    if owner and not is_parsed:
        owner_display = ("@" + owner.username) if owner.username else (owner.full_name or str(owner.id))
    else:
        owner_display = "N/A"
    text += f"\n👤 Выложил: {owner_display}"

    if owner_company:
        rating = owner_company.total_rating
        stars = "⭐" * rating + "☆" * (10 - rating)
        text += f"\n🏢 {owner_company.company_name or 'Компания'}"
        text += f"\n📊 Рейтинг: {stars} ({rating}/10)"
    else:
        text += "\n⚠️ Компания не зарегистрирована"

    # For parsed cargos: extract phone from comment
    parsed_phone = None
    if is_parsed and cargo.comment:
        phones = re.findall(r'[+]?[\d]{10,13}', cargo.comment)
        if phones:
            parsed_phone = phones[0]
            text += "\n📞 Контакт: " + parsed_phone

    if can_show_contacts and is_participant:
        other = carrier if is_owner else owner
        if other:
            company = f" ({other.company})" if other.company else ""
            phone = other.phone or "не указан"
            text += f"\n📞 Контакты: {other.full_name}{company} — {phone}"
    else:
        stars = "⭐" * round(avg_rating) if avg_rating else "нет оценок"
        text += f"\n⭐ Оценки: {stars} ({rating_count or 0})"
        text += f"\n🛡 Верификация: {_verification_label(owner_profile)}"
        if not is_parsed:
            text += "\n📞 Контакты доступны только участникам сделки"

    if cargo.status == CargoStatus.IN_PROGRESS and is_participant:
        text += "\n\n🗺 Трекинг доступен в меню сделки"

    owner_company_id = owner_company.id if owner_company else None
    if cargo.status == CargoStatus.IN_PROGRESS and is_participant:
        reply_markup = deal_actions(cargo.id, is_owner, has_ttn=bool(cargo.ttn_photo_file_id))
    else:
        can_bump = is_owner and cargo.status in {CargoStatus.NEW, CargoStatus.ACTIVE} and _is_premium_active(owner)
        reply_markup = cargo_actions(
            cargo.id, is_owner, cargo.status, owner_company_id, can_bump=can_bump
        )

    if parsed_phone:
        from aiogram.utils.keyboard import InlineKeyboardBuilder as _IKB
        _b = _IKB.from_markup(reply_markup)
        clean_phone = parsed_phone.lstrip('+')
        _b.row(InlineKeyboardButton(
            text="✍️ Написать в Telegram",
            url=f"tg://resolve?phone={clean_phone}"
        ))
        reply_markup = _b.as_markup()

    await message.answer(text, reply_markup=reply_markup)
    return True


@router.callback_query(F.data.startswith("upload_ttn_"))
async def upload_ttn_start(cb: CallbackQuery, state: FSMContext):
    cargo_id = int(cb.data.split("_")[2])
    await state.update_data(cargo_id=cargo_id)
    await state.set_state(CargoTracking.wait_ttn_photo)
    await cb.message.answer("📸 Пожалуйста, отправьте фото ТТН (транспортной накладной).")
    await cb.answer()


@router.message(CargoTracking.wait_ttn_photo, F.photo)
async def upload_ttn_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    cargo_id = data.get("cargo_id")
    photo = message.photo[-1]
    photo_file_id = photo.file_id

    # Анализ ТТН через Gemini (прямой вызов)
    msg = await message.answer("🔍 <b>Анализирую ТТН через AI...</b>", parse_mode="HTML")
    
    file = await message.bot.get_file(photo_file_id)
    file_bytes = await message.bot.download_file(file.file_path)
    
    from src.core.ai import verify_document_ai
    res = await verify_document_ai(file_bytes.read(), "ТТН")
    
    ai_status = ""
    if res.get("is_valid"):
        ai_status = f"\n\n🤖 <b>AI-Проверка пройдена:</b>\n✅ Подписи и печати найдены.\n📋 Номер/дата: {res.get('number', 'не распознан')}"
    else:
        ai_status = f"\n\n🤖 <b>Внимание AI:</b>\n⚠️ {res.get('comment', 'Не удалось подтвердить ТТН. Требуется ручная проверка.')}"

    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if cargo:
            cargo.ttn_photo_file_id = photo_file_id
            await session.commit()
            
            # Уведомляем заказчика
            try:
                await bot.send_message(
                    cargo.owner_id,
                    f"✅ Перевозчик загрузил фото ТТН по грузу #{cargo_id}.{ai_status}",
                    parse_mode="HTML"
                )
                await bot.send_photo(cargo.owner_id, photo_file_id, caption=f"📄 Фото ТТН по грузу #{cargo_id}")
            except:
                pass

    await state.clear()
    await msg.edit_text(f"✅ Фото ТТН успешно загружено и отправлено заказчику.{ai_status}", parse_mode="HTML", reply_markup=deal_actions(cargo_id, False, True))


@router.callback_query(F.data.startswith("view_ttn_"))
async def view_ttn(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[2])
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if cargo and cargo.ttn_photo_file_id:
            await cb.message.answer_photo(cargo.ttn_photo_file_id, caption=f"📄 Фото ТТН для груза #{cargo_id}")
        else:
            await cb.answer("❌ Фото ТТН не найдено", show_alert=True)
    await cb.answer()


@router.callback_query(F.data.startswith("find_dogruz_"))
async def cb_find_dogruz(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[2])
    
    # 1. Показываем статус поиска
    msg = await cb.message.answer("🤖 <b>Анализирую маршрут и ищу догрузы...</b>", parse_mode="HTML")
    
    async with async_session() as session:
        # Основной груз (рейс)
        base_cargo = await session.get(Cargo, cargo_id)
        if not base_cargo:
            await msg.edit_text("❌ Рейс не найден")
            return

        # 2. Получаем транзитные города через AI (чтобы знать путь)
        from src.bot.handlers.ai_nlu import _ai
        route_res = await _ai("/ask_logist", {
            "question": f"Назови список из 5-7 крупных городов России, через которые проходит оптимальный маршрут на грузовике из {base_cargo.from_city} в {base_cargo.to_city}. Верни только названия через запятую."
        })
        
        raw_route = route_res.get("answer", "")
        transit_cities = [c.strip() for c in raw_route.split(",") if c.strip()]
        
        # Собираем полный маршрут: Старт -> Транзиты -> Финиш
        full_route = [base_cargo.from_city] + transit_cities + [base_cargo.to_city]
        # Убираем дубли и пустые
        full_route = list(dict.fromkeys([c for c in full_route if c]))

        # 3. Ищем кандидатов в БД (все активные грузы кроме основного)
        candidates_res = await session.execute(
            select(Cargo).where(Cargo.status == CargoStatus.NEW).where(Cargo.id != cargo_id).limit(100)
        )
        db_candidates = candidates_res.scalars().all()

        if not db_candidates:
            await msg.edit_text("📭 Сейчас нет активных грузов для подбора. Попробуй позже!")
            return

        # 4. Готовим запрос для нашего оптимизатора
        from src.core.schemas.dogruz import DogruzRequest, BaseLoad, LoadCandidate
        from src.core.services.dogruz.optimizer import find_best_dogruz

        # Для MVP считаем, что у водителя стандартная фура 20т/82м3
        # Остаток = фура - основной груз
        max_w, max_v = 20000.0, 82.0
        used_w, used_v = float(base_cargo.weight or 0), float(base_cargo.volume or 0)

        req = DogruzRequest(
            base_load=BaseLoad(
                from_city=base_cargo.from_city,
                to_city=base_cargo.to_city,
                route=full_route,
                max_weight=max_w,
                max_volume=max_v,
                used_weight=used_w,
                used_volume=used_v
            ),
            loads=[
                LoadCandidate(
                    id=c.id, from_city=c.from_city, to_city=c.to_city, 
                    weight=c.weight, volume=c.volume or 0, price=c.price
                ) for c in db_candidates
            ]
        )

        # 5. Запускаем магию оптимизации
        result = find_best_dogruz(req)

        if not result.selected_loads:
            await msg.edit_text(
                f"📍 Маршрут: {' → '.join(full_route)}\n\n"
                f"😔 Подходящих догрузов не найдено.\n"
                f"Свободно: {max_w - used_w}кг и {max_v - used_v}м3."
            )
            return

        # 6. Выводим результат
        text = f"✅ <b>Нашел идеальные догрузы!</b>\n\n"
        text += f"🛣 <b>Маршрут:</b> <i>{base_cargo.from_city} → {base_cargo.to_city}</i>\n"
        text += f"🚚 <b>Твой остаток:</b> {max_w - used_w}кг / {max_v - used_v}м3\n\n"
        
        for i, sl in enumerate(result.selected_loads, 1):
            text += f"📦 <b>{i}. Груз #{sl.id}</b>\n"
            text += f"📍 {sl.from_city} → {sl.to_city}\n"
            text += f"⚖️ {sl.weight}т | 💰 {sl.price or 0:,}₽\n"
            text += f"/cargo_{sl.id} — посмотреть\n\n"
        
        text += f"💵 <b>Доп. доход: +{result.total_price:,} ₽</b>\n"
        text += f"🤖 {result.explanation}"

        await msg.edit_text(text, parse_mode="HTML")

    await cb.answer()
    cargo_id = int(cb.data.split("_")[1])
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo or not cargo.carrier_id:
            await cb.answer("❌ Перевозчик еще не назначен", show_alert=True)
            return
        
        from src.core.models import DriverTracking
        tracking = await session.scalar(
            select(DriverTracking).where(DriverTracking.user_id == cargo.carrier_id)
        )
        
        if tracking and tracking.is_active and tracking.lat:
            maps_url = f"https://maps.google.com/?q={tracking.lat},{tracking.lon}"
            time_str = tracking.updated_at.strftime("%H:%M")
            await cb.message.answer(
                f"📍 <b>Текущее местоположение груза #{cargo_id}</b>\n\n"
                f"Обновлено: {time_str}\n"
                f"Водитель на связи.\n\n"
                f"<a href='{maps_url}'>Посмотреть на карте</a>",
                parse_mode="HTML",
                disable_web_page_preview=False
            )
        else:
            await cb.message.answer(
                "📵 Водитель сейчас не транслирует геолокацию.\n\n"
                "Вы можете попросить его 'Выйти на линию' в чате сделки."
            )
    await cb.answer()

@router.callback_query(F.data == "cargos")
async def cargos_handler(cb: CallbackQuery):
    try:
        await cb.message.edit_text("🚛 <b>Грузы</b>", reply_markup=cargos_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data == "all_cargos")
async def all_cargos(cb: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(
            select(Cargo).where(Cargo.status == CargoStatus.NEW).limit(10)
        )
        cargos = result.scalars().all()

    if not cargos:
        try:
            await cb.message.edit_text("📭 Нет активных грузов", reply_markup=cargos_menu())
        except TelegramBadRequest:
            pass
        await cb.answer()
        return

    text = "📋 <b>Активные грузы:</b>\n\n"
    for c in cargos:
        text += f"🔹 #{c.id}: {c.from_city} → {c.to_city}\n"
        text += f"   {c.cargo_type}, {c.weight}т, {c.price}₽\n\n"

    try:
        await cb.message.edit_text(
            text,
            reply_markup=cargo_open_list_kb(cargos, back_cb="cargos"),
        )
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data == "my_cargos")
async def my_cargos(cb: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(
            select(Cargo)
            .where(Cargo.owner_id == cb.from_user.id)
            .where(Cargo.status.in_([CargoStatus.NEW, CargoStatus.IN_PROGRESS, CargoStatus.ACTIVE]))
            .order_by(Cargo.created_at.desc())
            .limit(15)
        )
        cargos = result.scalars().all()

    if not cargos:
        try:
            await cb.message.edit_text("📭 У тебя нет грузов", reply_markup=cargos_menu())
        except TelegramBadRequest:
            pass
        await cb.answer()
        return

    text = "📦 <b>Мои грузы</b>\n\nВыбери груз:"
    try:
        await cb.message.edit_text(
            text,
            reply_markup=cargo_open_list_kb(cargos, back_cb="cargos"),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            text,
            reply_markup=cargo_open_list_kb(cargos, back_cb="cargos"),
        )
    await cb.answer()

@router.callback_query(F.data.startswith("cargo_open_"))
async def cargo_open(cb: CallbackQuery):
    try:
        cargo_id = int(cb.data.split("_")[2])
    except:
        await cb.answer("❌ Ошибка", show_alert=True)
        return

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo:
            await cb.answer("❌ Груз не найден", show_alert=True)
            return

        text, is_owner, owner_company_id, can_bump = await render_cargo_card(
            session, cargo, cb.from_user.id
        )

    try:
        await cb.message.edit_text(
            text,
            reply_markup=deal_actions(cargo.id, is_owner, has_ttn=bool(cargo.ttn_photo_file_id)) if cargo.status == CargoStatus.IN_PROGRESS else cargo_actions(
                cargo.id, is_owner, cargo.status, owner_company_id, can_bump=can_bump
            ),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            text,
            reply_markup=deal_actions(cargo.id, is_owner, has_ttn=bool(cargo.ttn_photo_file_id)) if cargo.status == CargoStatus.IN_PROGRESS else cargo_actions(
                cargo.id, is_owner, cargo.status, owner_company_id, can_bump=can_bump
            ),
        )
    await cb.answer()

@router.callback_query(F.data.startswith("edit_cargo_"))
async def edit_cargo_menu(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[2])

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo or cargo.owner_id != cb.from_user.id:
            await cb.answer("❌ Груз не найден или нет доступа", show_alert=True)
            return
        if cargo.status != CargoStatus.NEW:
            await cb.answer("❌ Можно редактировать только новые грузы", show_alert=True)
            return

    await cb.message.edit_text(
        f"✏️ <b>Редактирование груза #{cargo_id}</b>\n\nВыбери что изменить:",
        reply_markup=cargo_edit_kb(cargo_id),
    )
    await cb.answer()

@router.callback_query(F.data.startswith("edit_price_"))
async def edit_price_start(cb: CallbackQuery, state: FSMContext):
    cargo_id = int(cb.data.split("_")[2])
    await state.update_data(edit_cargo_id=cargo_id)
    await cb.message.edit_text("💰 Введи новую цену (₽):\n\n<i>Отмена — /cancel</i>")
    await state.set_state(EditCargo.price)
    await cb.answer()

@router.message(EditCargo.price)
async def edit_price_save(message: Message, state: FSMContext):
    if message.text.lower() in ["/cancel", "отмена"]:
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=main_menu())
        return

    try:
        price = int(message.text.replace(" ", "").replace("₽", ""))
    except:
        await message.answer("❌ Введи число. Пример: 50000")
        return

    data = await state.get_data()
    cargo_id = data.get("edit_cargo_id")

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if cargo and cargo.owner_id == message.from_user.id:
            cargo.price = price
            await session.commit()
            await message.answer(f"✅ Цена изменена на {price:,} ₽")
        else:
            await message.answer("❌ Груз не найден")

    await state.clear()

@router.callback_query(F.data.startswith("edit_date_"))
async def edit_date_start(cb: CallbackQuery, state: FSMContext):
    cargo_id = int(cb.data.split("_")[2])
    await state.update_data(edit_cargo_id=cargo_id)
    await cb.message.edit_text(
        "📅 Введи новую дату загрузки:\n\n"
        "Формат: ДД.ММ.ГГГГ или 'завтра', 'послезавтра'\n\n"
        "<i>Отмена — /cancel</i>",
    )
    await state.set_state(EditCargo.date)
    await cb.answer()

@router.message(EditCargo.date)
async def edit_date_save(message: Message, state: FSMContext):
    if message.text.lower() in ["/cancel", "отмена"]:
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=main_menu())
        return

    text = message.text.lower().strip()

    if text == "сегодня":
        load_date = datetime.now()
    elif text == "завтра":
        load_date = datetime.now() + timedelta(days=1)
    elif text == "послезавтра":
        load_date = datetime.now() + timedelta(days=2)
    else:
        try:
            load_date = datetime.strptime(message.text, "%d.%m.%Y")
        except:
            await message.answer("❌ Неверный формат. Пример: 15.02.2026 или 'завтра'")
            return

    data = await state.get_data()
    cargo_id = data.get("edit_cargo_id")

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if cargo and cargo.owner_id == message.from_user.id:
            cargo.load_date = load_date
            await session.commit()
            await message.answer(f"✅ Дата изменена на {load_date.strftime('%d.%m.%Y')}")
        else:
            await message.answer("❌ Груз не найден")

    await state.clear()

@router.callback_query(F.data.startswith("edit_time_"))
async def edit_time_start(cb: CallbackQuery, state: FSMContext):
    cargo_id = int(cb.data.split("_")[2])
    await state.update_data(edit_cargo_id=cargo_id)
    await cb.message.edit_text(
        "🕐 Введи время загрузки:\n\n"
        "Формат: ЧЧ:ММ (например 09:00 или 14:30)\n\n"
        "<i>Отмена — /cancel</i>",
    )
    await state.set_state(EditCargo.time)
    await cb.answer()

@router.message(EditCargo.time)
async def edit_time_save(message: Message, state: FSMContext):
    if message.text.lower() in ["/cancel", "отмена"]:
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=main_menu())
        return

    time_match = re.match(r"^(\d{1,2}):(\d{2})$", message.text.strip())
    if not time_match:
        await message.answer("❌ Неверный формат. Пример: 09:00 или 14:30")
        return

    hours, minutes = int(time_match.group(1)), int(time_match.group(2))
    if hours > 23 or minutes > 59:
        await message.answer("❌ Неверное время")
        return

    load_time = f"{hours:02d}:{minutes:02d}"

    data = await state.get_data()
    cargo_id = data.get("edit_cargo_id")

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if cargo and cargo.owner_id == message.from_user.id:
            cargo.load_time = load_time
            await session.commit()
            await message.answer(f"✅ Время загрузки: {load_time}")
        else:
            await message.answer("❌ Груз не найден")

    await state.clear()

@router.callback_query(F.data.startswith("edit_comment_"))
async def edit_comment_start(cb: CallbackQuery, state: FSMContext):
    cargo_id = int(cb.data.split("_")[2])
    await state.update_data(edit_cargo_id=cargo_id)
    await cb.message.edit_text("💬 Введи новый комментарий:\n\n<i>Отмена — /cancel</i>")
    await state.set_state(EditCargo.comment)
    await cb.answer()

@router.message(EditCargo.comment)
async def edit_comment_save(message: Message, state: FSMContext):
    if message.text.lower() in ["/cancel", "отмена"]:
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=main_menu())
        return

    data = await state.get_data()
    cargo_id = data.get("edit_cargo_id")

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if cargo and cargo.owner_id == message.from_user.id:
            cargo.comment = message.text[:500]
            await session.commit()
            await message.answer("✅ Комментарий обновлён")
        else:
            await message.answer("❌ Груз не найден")

    await state.clear()

@router.callback_query(F.data.startswith("restore_cargo_"))
async def restore_cargo(cb: CallbackQuery):
    try:
        cargo_id = int(cb.data.split("_")[2])
    except:
        await cb.answer("❌ Ошибка", show_alert=True)
        return

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo or cargo.owner_id != cb.from_user.id:
            await cb.answer("❌ Груз не найден или нет доступа", show_alert=True)
            return
        if cargo.status != CargoStatus.ARCHIVED:
            await cb.answer("❌ Можно восстановить только архивные грузы", show_alert=True)
            return

        restored = Cargo(
            owner_id=cargo.owner_id,
            carrier_id=None,
            from_city=cargo.from_city,
            to_city=cargo.to_city,
            cargo_type=cargo.cargo_type,
            weight=cargo.weight,
            volume=cargo.volume,
            price=cargo.price,
            actual_price=None,
            load_date=datetime.now(),
            load_time=cargo.load_time,
            comment=cargo.comment,
            external_url=cargo.external_url,
            source_platform=cargo.source_platform,
            status=CargoStatus.NEW,
            tracking_enabled=False,
        )
        session.add(restored)
        await session.commit()
        await session.refresh(restored)
        new_id = restored.id

    # Send push notifications to route subscribers
    from src.core.services.notifications import notify_subscribers
    try:
        await notify_subscribers(restored)
        async with async_session() as session:
            c = await session.scalar(select(Cargo).where(Cargo.id == new_id))
            if c:
                c.notified_at = datetime.utcnow()
                await session.commit()
    except Exception as e:
        logger.warning("Notification failed for cargo #%s: %s", new_id, e)

    try:
        await _sync_manual_feed_event(restored, source_note="telegram_bot_restore")
    except Exception as e:
        logger.warning("Manual feed sync failed for restored cargo #%s: %s", new_id, e)

    try:
        await _publish_cargo_sync_event(restored, event_type="cargo.created")
    except Exception as e:
        logger.warning("Cross-sync publish failed for cargo #%s: %s", new_id, e)

    await cb.message.answer(f"✅ Груз восстановлен как #{new_id}")
    await send_cargo_details(cb.message, new_id)
    await cb.answer()


@router.callback_query(F.data.startswith("bump_cargo_"))
async def bump_cargo(cb: CallbackQuery):
    try:
        cargo_id = int(cb.data.split("_")[2])
    except Exception:
        await cb.answer("❌ Ошибка", show_alert=True)
        return

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo or cargo.owner_id != cb.from_user.id:
            await cb.answer("❌ Груз не найден или нет доступа", show_alert=True)
            return
        if cargo.status not in {CargoStatus.NEW, CargoStatus.ACTIVE}:
            await cb.answer("❌ Поднимать можно только активный груз", show_alert=True)
            return

        owner = await session.scalar(select(User).where(User.id == cargo.owner_id))
        if not _is_premium_active(owner):
            await cb.answer("⭐ Поднятие доступно только по подписке", show_alert=True)
            return

        latest_event = await _latest_manual_feed_event(session, int(cargo.owner_id), int(cargo.id))
        if latest_event and latest_event.created_at:
            next_allowed = latest_event.created_at + timedelta(hours=1)
            if next_allowed > datetime.utcnow():
                await cb.answer(
                    f"⏳ Повторно можно через {_format_bump_wait(next_allowed - datetime.utcnow())}",
                    show_alert=True,
                )
                return

        cargo.created_at = datetime.utcnow()
        await session.commit()

    try:
        await _sync_manual_feed_event(cargo, source_note="telegram_bot_bump", republish=True)
    except Exception as e:
        logger.warning("Manual feed bump failed for cargo #%s: %s", cargo_id, e)
        await cb.answer("❌ Не удалось поднять груз", show_alert=True)
        return

    await cb.answer("⬆️ Груз поднят в ленте")
    await send_cargo_details(cb.message, cargo_id)

@router.callback_query(F.data == "my_responses")
async def my_responses(cb: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(
            select(CargoResponse).where(CargoResponse.carrier_id == cb.from_user.id).limit(10)
        )
        responses = result.scalars().all()

    if not responses:
        try:
            await cb.message.edit_text("📭 Нет откликов", reply_markup=cargos_menu())
        except TelegramBadRequest:
            pass
        await cb.answer()
        return

    text = "🚛 <b>Мои отклики:</b>\n\n"
    for r in responses:
        status = "⏳" if r.is_accepted is None else ("✅" if r.is_accepted else "❌")
        link = cargo_deeplink(r.cargo_id)
        text += f"{status} Груз #{r.cargo_id} — {r.price_offer or 'без цены'}₽ {link}\n"

    try:
        await cb.message.edit_text(text, reply_markup=cargos_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.message(Command("applications"))
async def legacy_applications(message: Message):
    """Legacy alias for old bot users: /applications -> my responses."""
    async with async_session() as session:
        result = await session.execute(
            select(CargoResponse).where(CargoResponse.carrier_id == message.from_user.id).limit(10)
        )
        responses = result.scalars().all()

    if not responses:
        await message.answer("📭 Нет откликов")
        return

    text = "🚛 <b>Мои отклики:</b>\n\n"
    for r in responses:
        status = "⏳" if r.is_accepted is None else ("✅" if r.is_accepted else "❌")
        link = cargo_deeplink(r.cargo_id)
        text += f"{status} Груз #{r.cargo_id} — {r.price_offer or 'без цены'}₽ {link}\n"

    await message.answer(text, parse_mode="HTML")

@router.callback_query(F.data == "add_cargo")
async def add_cargo_start(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        "🚛 <b>Новый груз</b>\n\n"
        "Откуда? Начни вводить город (например: «самар», «мос», «спб»)"
        + CANCEL_HINT,
        reply_markup=city_kb([], "from"),
    )
    await state.set_state(CargoForm.from_city)
    await cb.answer()

@router.message(CargoForm.from_city)
async def cargo_from(message: Message, state: FSMContext):
    suggestions = city_suggest(message.text)
    if not suggestions:
        if _looks_like_city(message.text):
            parsed_city = await parse_city(message.text)
            if parsed_city:
                suggestions = [parsed_city]
        if not suggestions:
            await message.answer(
                "Я жду город отправления. Начни ввод: «мос», «самар», «спб»."
                + CANCEL_HINT,
                reply_markup=city_kb([], "from"),
            )
            return
    await message.answer(
        "Выбери город отправления:" + CANCEL_HINT,
        reply_markup=city_kb(suggestions, "from"),
    )

@router.message(CargoForm.to_city)
async def cargo_to(message: Message, state: FSMContext):
    suggestions = city_suggest(message.text)
    if not suggestions:
        if _looks_like_city(message.text):
            parsed_city = await parse_city(message.text)
            if parsed_city:
                suggestions = [parsed_city]
        if not suggestions:
            await message.answer(
                "Я жду город назначения. Начни ввод: «мос», «самар», «спб»."
                + CANCEL_HINT,
                reply_markup=city_kb([], "to"),
            )
            return
    await message.answer(
        "Выбери город назначения:" + CANCEL_HINT,
        reply_markup=city_kb(suggestions, "to"),
    )

@router.callback_query(CargoForm.from_city, F.data.startswith("city:from:"))
async def cargo_from_select(cb: CallbackQuery, state: FSMContext):
    _, _, city = cb.data.split(":", 2)
    await state.update_data(from_city=city)
    await state.set_state(CargoForm.to_city)
    await cb.message.edit_text(
        f"✅ Выбрано: {city}\n\n"
        "Куда доставить? Начни вводить город (например: «самар», «мос», «спб»)"
        + CANCEL_HINT,
        reply_markup=city_kb([], "to"),
    )
    await cb.answer()

@router.callback_query(CargoForm.to_city, F.data.startswith("city:to:"))
async def cargo_to_select(cb: CallbackQuery, state: FSMContext):
    _, _, city = cb.data.split(":", 2)
    await state.update_data(to_city=city)
    await state.set_state(CargoForm.cargo_type)
    await cb.message.edit_text(
        f"✅ Выбрано: {city}\n\n"
        "Тип груза? (например: паллеты, сборный)" + CANCEL_HINT,
    )
    await cb.answer()

@router.message(CargoForm.cargo_type)
async def cargo_type(message: Message, state: FSMContext):
    await state.update_data(cargo_type=message.text)
    await message.answer("Вес (в тоннах)" + CANCEL_HINT)
    await state.set_state(CargoForm.weight)

@router.message(CargoForm.weight)
async def cargo_weight(message: Message, state: FSMContext):
    if message.text.lower() in ["/cancel", "отмена"]:
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=main_menu())
        return

    try:
        weight = float(message.text.replace(",", ".").replace(" ", ""))
    except:
        await message.answer("❌ Введи число. Пример: 20 или 5.5")
        return

    await state.update_data(weight=weight)
    data = await state.get_data()

    from_city = data.get("from_city")
    to_city = data.get("to_city")
    cargo_type = data.get("cargo_type", "тент")

    from src.core.ai import estimate_price_smart
    estimate = await estimate_price_smart(from_city, to_city, weight, cargo_type)

    hint = ""
    if estimate.get("price"):
        hint = f"\n\n💡 <b>Рекомендуемая цена: {estimate['price']:,} ₽</b>\n"
        hint += estimate["details"]
        await state.update_data(suggested_price=estimate["price"])

    await message.answer(
        f"💰 Укажи цену (₽){hint}\n\n"
        "Введи число или нажми кнопку:",
        reply_markup=price_suggest_kb(estimate.get("price")),
    )
    await state.set_state(CargoForm.price)

@router.message(CargoForm.price)
async def cargo_price(message: Message, state: FSMContext):
    if message.text.lower() in ["/cancel", "отмена"]:
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=main_menu())
        return

    try:
        price = int(message.text.replace(" ", "").replace("₽", ""))
    except:
        await message.answer("❌ Введи число")
        return

    await state.update_data(price=price)
    await message.answer(
        "📅 Дата загрузки?\n\n"
        "Можно: сегодня / завтра / послезавтра или ДД.ММ[.ГГГГ].\n"
        "И сразу время: завтра в 10:00"
        + CANCEL_HINT
    )
    await state.set_state(CargoForm.load_date)

@router.callback_query(F.data.startswith("use_price_"), CargoForm.price)
async def use_suggested_price(cb: CallbackQuery, state: FSMContext):
    price = int(cb.data.split("_")[2])
    await state.update_data(price=price)

    await cb.message.edit_text(
        f"✅ Цена: {price:,} ₽\n\n"
        "📅 Дата загрузки? (завтра / послезавтра / ДД.ММ или завтра в 10:00)"
    )
    await state.set_state(CargoForm.load_date)
    await cb.answer()

@router.message(CargoForm.load_date)
async def cargo_date(message: Message, state: FSMContext):
    parsed = parse_load_datetime(message.text)
    if not parsed:
        await message.answer(
            "❌ Формат: сегодня/завтра/послезавтра или ДД.ММ[.ГГГГ], "
            "можно с временем: завтра в 10:00"
        )
        return
    load_date, load_time = parsed
    # В FSM (Redis) храним строку — datetime не сериализуется в JSON
    await state.update_data(
        load_date=load_date.strftime("%Y-%m-%d"),
        load_time=load_time,
    )
    if load_time:
        await message.answer("💬 Комментарий?", reply_markup=skip_kb())
        await state.set_state(CargoForm.comment)
    else:
        await message.answer(
            "🕐 Время загрузки? (ЧЧ:ММ)\n\nПропустить — нажми кнопку",
            reply_markup=skip_kb(),
        )
        await state.set_state(CargoForm.load_time)

@router.message(CargoForm.load_time)
async def cargo_time(message: Message, state: FSMContext):
    time_match = re.match(r"^(\d{1,2}):(\d{2})$", message.text.strip())
    if time_match:
        hours, minutes = int(time_match.group(1)), int(time_match.group(2))
        if hours <= 23 and minutes <= 59:
            load_time = f"{hours:02d}:{minutes:02d}"
            await state.update_data(load_time=load_time)

    await message.answer("💬 Комментарий?", reply_markup=skip_kb())
    await state.set_state(CargoForm.comment)

@router.callback_query(F.data == "skip", CargoForm.load_time)
async def skip_time(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text("💬 Комментарий?", reply_markup=skip_kb())
    await state.set_state(CargoForm.comment)
    await cb.answer()

@router.message(CargoForm.comment)
async def cargo_comment(message: Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await show_confirm(message, state)

@router.callback_query(CargoForm.comment, F.data == "skip")
async def cargo_skip_comment(cb: CallbackQuery, state: FSMContext):
    await state.update_data(comment=None)
    await show_confirm(cb.message, state)
    await cb.answer()

def _load_date_from_state(data: dict):
    """load_date в state хранится как 'YYYY-MM-DD'."""
    raw = data.get("load_date")
    if hasattr(raw, "strftime"):
        return raw
    if isinstance(raw, str):
        return datetime.strptime(raw, "%Y-%m-%d")
    return datetime.now()


async def show_confirm(message: Message, state: FSMContext):
    data = await state.get_data()
    load_date = _load_date_from_state(data)

    ati_line = ""
    try:
        from src.services.ati_service import get_route_rate_cached
        rate = await get_route_rate_cached(data['from_city'], data['to_city'])
        if rate:
            price_rub = data.get('price') or 0
            diff = ""
            if price_rub and rate.price_rub:
                delta = price_rub - rate.price_rub
                if abs(delta) > 1000:
                    sign = "+" if delta > 0 else "−"
                    diff = f" ({sign}{abs(delta):,} ₽ от рынка)".replace(",", " ")
            ati_line = f"\n📊 Рынок ATI: <b>{rate.price_rub:,} ₽</b> ({rate.loads_count} грузов){diff}".replace(",", " ")
    except Exception as e:
        logger.debug("ATI rate fetch skipped: %s", e)

    text = "📦 <b>Подтверди публикацию:</b>\n\n"
    text += f"📍 {data['from_city']} → {data['to_city']}\n"
    text += f"📦 {data['cargo_type']}\n"
    text += f"⚖️ {data['weight']} т\n"
    text += f"💰 {data['price']} ₽{ati_line}\n"
    text += f"📅 {load_date.strftime('%d.%m.%Y')}"
    if data.get("load_time"):
        text += f" в {data['load_time']}"
    text += "\n"
    if data.get('comment'):
        text += f"💬 {data['comment']}\n"
    await message.answer(text, parse_mode="HTML", reply_markup=confirm_kb())
    await state.set_state(CargoForm.confirm)

@router.callback_query(CargoForm.confirm, F.data == "yes")
async def cargo_confirm_yes(cb: CallbackQuery, state: FSMContext):
    from src.core.services.notifications import notify_subscribers
    from datetime import datetime as _dt

    data = await state.get_data()
    load_date = _load_date_from_state(data)

    async with async_session() as session:
        cargo = Cargo(
            owner_id=cb.from_user.id,
            from_city=data['from_city'],
            to_city=data['to_city'],
            cargo_type=data['cargo_type'],
            weight=data['weight'],
            price=data['price'],
            load_date=load_date,
            load_time=data.get('load_time'),
            comment=data.get('comment'),
            source_platform="tg-bot",
        )
        session.add(cargo)
        await session.commit()
        await session.refresh(cargo)
        cargo_id = cargo.id

    await state.clear()
    await cb.message.edit_text(f"✅ Груз #{cargo_id} опубликован!", reply_markup=main_menu())

    # Send push notifications to route subscribers
    try:
        await notify_subscribers(cargo)
        async with async_session() as session:
            c = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
            if c:
                c.notified_at = _dt.utcnow()
                await session.commit()
    except Exception as e:
        logger.warning("Notification failed for cargo #%s: %s", cargo_id, e)

    try:
        await _sync_manual_feed_event(cargo, source_note="telegram_bot_create")
    except Exception as e:
        logger.warning("Manual feed sync failed for cargo #%s: %s", cargo_id, e)

    try:
        await _publish_cargo_sync_event(cargo, event_type="cargo.created")
    except Exception as e:
        logger.warning("Cross-sync publish failed for cargo #%s: %s", cargo_id, e)

    # AI analysis + antifraud in background
    cargo_text = (
        f"{data.get('from_city', '')} — {data.get('to_city', '')}, "
        f"{data.get('cargo_type', '')}, {data.get('weight', '')} т, "
        f"дата: {data.get('load_date', 'не указана')}, "
        f"{data.get('price', '')} руб"
    )
    asyncio.create_task(_run_ai_after_cargo(cargo_id, cb.from_user.id, cargo_text))

    await cb.answer()
    logger.info("Cargo %s created by %s", cargo_id, cb.from_user.id)

@router.callback_query(CargoForm.confirm, F.data == "no")
async def cargo_confirm_no(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Отменено", reply_markup=main_menu())
    await cb.answer()


def _nlp_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Создать", callback_data="nlp_cargo_confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="nlp_cargo_cancel"),
    ]])


@router.message(StateFilter(None, CargoNLPConfirm.wait_confirm), F.voice)
async def nlp_voice_detect(message: Message, state: FSMContext):
    """Голосовое сообщение → транскрипция → умный NLP парсинг (машина или груз)."""
    from src.core.ai import transcribe_voice
    
    current = await state.get_state()
    if current is not None:
        await state.clear()

    wait_msg = await message.answer("🎤 <i>Слушаю...</i>", parse_mode="HTML")
    try:
        from src.bot.bot import bot as _bot
        file = await _bot.get_file(message.voice.file_id)
        bio = await _bot.download_file(file.file_path)
        file_bytes = bio.read()
        text = await transcribe_voice(file_bytes)
    except Exception as e:
        logger.error("Voice transcription failed: %s", e)
        await wait_msg.edit_text("Не удалось распознать голос.")
        return

    if not text:
        await wait_msg.edit_text("Не распознал текст.")
        return

    await wait_msg.edit_text(f"🎤 <i>{text}</i>", parse_mode="HTML")
    
    # 1. Проверяем, не ищет ли человек грузы для свободной машины
    from src.bot.handlers.trucks import _looks_like_truck_free_text_candidate, smart_truck_text
    if _looks_like_truck_free_text_candidate(text):
        await smart_truck_text(message, state, override_text=text)
        return
        
    # 2. Если это не машина, пробуем распарсить как груз
    try:
        parsed = await parse_cargo_nlp(text)
    except Exception as e:
        logger.error("parse_cargo_nlp error: %s", e)
        return

    if not parsed:
        await message.answer("Не смог найти вес и маршрут. Скажите, например: «Самара Питер 10 тонн тент»")
        return

    # Подготавливаем карточку груза для публикации (старая логика)
    if not parsed.get("price"):
        try:
            from src.core.ai import estimate_price_smart
            est = await estimate_price_smart(parsed["from_city"], parsed["to_city"], parsed["weight"], parsed.get("cargo_type", "тент"))
            if est.get("price"):
                parsed["price"] = est["price"]
                parsed["price_estimated"] = True
        except Exception as e:
            logger.warning("estimate_price_smart error: %s", e)

    weight_display = parsed["weight"]
    if parsed.get("load_date"):
        from datetime import datetime as _dt
        load_date_str = _dt.strptime(parsed["load_date"], "%Y-%m-%d").strftime("%d.%m.%Y")
        if parsed.get("load_time"):
            load_date_str += f" в {parsed['load_time']}"
    else:
        load_date_str = "не указана"

    reply = "<b>📦 Распознал груз:</b>\n"
    reply += f"📍 {parsed['from_city']} ➔ {parsed['to_city']}\n"
    reply += f"📦 {parsed.get('cargo_type', 'тент')}\n"
    reply += f"⚖️ {weight_display} т\n"
    
    if parsed.get("price"):
        reply += f"💰 {parsed['price']}\n"
    else:
        reply += "💰 не указана\n"
        
    reply += f"📅 {load_date_str}\n\n"
    reply += "Опубликовать?"

    await state.set_state(CargoNLPConfirm.wait_confirm)
    await state.update_data(parsed_cargo=parsed)
    await message.answer(reply, parse_mode="HTML", reply_markup=_nlp_confirm_kb())

@router.callback_query(F.data == "nlp_cargo_confirm")
async def nlp_cargo_confirm(cb: CallbackQuery, state: FSMContext):
    from src.core.services.notifications import notify_subscribers
    from datetime import datetime as _dt

    data = await state.get_data()
    parsed = data.get("nlp_parsed", {})

    if not parsed or not parsed.get("from_city"):
        await cb.answer("Данные устарели, отправь заявку заново", show_alert=True)
        await cb.message.edit_reply_markup(reply_markup=None)
        return

    load_date_raw = parsed.get("load_date")
    load_date = _dt.strptime(load_date_raw, "%Y-%m-%d") if load_date_raw else _dt.now()
    price = parsed.get("price") or 0

    async with async_session() as session:
        cargo = Cargo(
            owner_id=cb.from_user.id,
            from_city=parsed["from_city"],
            to_city=parsed["to_city"],
            cargo_type=parsed.get("cargo_type", "груз"),
            weight=parsed["weight"],
            price=price,
            load_date=load_date,
            load_time=parsed.get("load_time"),
            comment="⚡ СРОЧНО" if parsed.get("is_urgent") else None,
        )
        session.add(cargo)
        await session.commit()
        await session.refresh(cargo)
        cargo_id = cargo.id

    await state.clear()
    await cb.message.edit_text(f"✅ Груз #{cargo_id} опубликован!", reply_markup=main_menu())

    try:
        await notify_subscribers(cargo)
    except Exception as e:
        logger.warning("NLP cargo notify failed for #%s: %s", cargo_id, e)

    try:
        await _sync_manual_feed_event(cargo, source_note="telegram_bot_nlp_create")
    except Exception as e:
        logger.warning("Manual feed sync failed for NLP cargo #%s: %s", cargo_id, e)

    try:
        await _publish_cargo_sync_event(cargo, event_type="cargo.created")
    except Exception as e:
        logger.warning("NLP cargo cross-sync failed for #%s: %s", cargo_id, e)

    # AI analysis + antifraud in background
    cargo_text = (
        f"{parsed.get('from_city', '')} — {parsed.get('to_city', '')}, "
        f"{parsed.get('cargo_type', 'груз')}, {parsed.get('weight', '')} т, "
        f"дата: {parsed.get('load_date', 'не указана')}, "
        f"{parsed.get('price', '')} руб"
    )
    asyncio.create_task(_run_ai_after_cargo(cargo_id, cb.from_user.id, cargo_text))

    await cb.answer()
    logger.info("NLP cargo %s created by %s", cargo_id, cb.from_user.id)


@router.callback_query(F.data == "nlp_cargo_cancel")
async def nlp_cargo_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Отменено", reply_markup=main_menu())
    await cb.answer()

@router.message(F.text.startswith("/cargo_"))
async def show_cargo(message: Message):
    try:
        cargo_id = int(message.text.split("_")[1])
    except:
        return

    await send_cargo_details(message, cargo_id)

@router.callback_query(F.data.startswith("respond_"))
async def respond_cargo(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[1])

    async with async_session() as session:
        existing = await session.execute(
            select(CargoResponse)
            .where(CargoResponse.cargo_id == cargo_id)
            .where(CargoResponse.carrier_id == cb.from_user.id)
        )
        if existing.scalar_one_or_none():
            await cb.answer("❌ Ты уже откликался", show_alert=True)
            return

        response = CargoResponse(cargo_id=cargo_id, carrier_id=cb.from_user.id)
        session.add(response)
        await session.commit()

        cargo = await session.execute(select(Cargo).where(Cargo.id == cargo_id))
        cargo = cargo.scalar_one_or_none()

        owner = None
        owner_contact = None
        if cargo:
            link = cargo_deeplink(cargo_id)
            carrier_user = await session.get(User, cb.from_user.id)
            if carrier_user and carrier_user.username:
                carrier_contact = "@" + carrier_user.username
            elif carrier_user and carrier_user.phone:
                carrier_contact = carrier_user.phone
            else:
                carrier_contact = cb.from_user.full_name
            try:
                await bot.send_message(
                    cargo.owner_id,
                    f"✉️ Новый отклик на груз #{cargo_id}!\nПеревозчик: {carrier_contact}\n{link}"
                )
            except Exception:
                pass
            owner = await session.get(User, cargo.owner_id)
            if owner and not is_parsed:
                owner_contact = ("@" + owner.username) if owner.username else owner.phone

    await cb.answer("✅ Отклик отправлен!", show_alert=True)
    if owner_contact:
        await cb.message.answer(
            f"✅ Отклик отправлен #{cargo_id}!\n📞 Контакт заказчика: {owner_contact}\nЗаказчик получил уведомление — ожидай связи."
        )
    logger.info(f"Response from {cb.from_user.id} to cargo {cargo_id}")



@router.callback_query(F.data.startswith("responses_"))
async def show_responses(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[1])

    async with async_session() as session:
        cargo = (await session.execute(select(Cargo).where(Cargo.id == cargo_id))).scalar_one_or_none()
        if not cargo or cargo.owner_id != cb.from_user.id:
            await cb.answer("❌ Нет доступа", show_alert=True)
            return

        responses_result = await session.execute(
            select(CargoResponse).where(CargoResponse.cargo_id == cargo_id)
        )
        responses = responses_result.scalars().all()

        if not responses:
            await cb.answer("📭 Нет откликов", show_alert=True)
            return

        carrier_ids = [r.carrier_id for r in responses]
        users_result = await session.execute(select(User).where(User.id.in_(carrier_ids)))
        users = {u.id: u for u in users_result.scalars().all()}

        profiles_result = await session.execute(
            select(UserProfile).where(UserProfile.user_id.in_(carrier_ids))
        )
        profiles = {p.user_id: p for p in profiles_result.scalars().all()}

        companies_result = await session.execute(
            select(CompanyDetails).where(CompanyDetails.user_id.in_(carrier_ids))
        )
        companies_by_user = {c.user_id: c for c in companies_result.scalars().all()}
        company_ids = [c.id for c in companies_by_user.values()]

        open_claims_by_company = {}
        if company_ids:
            claims_result = await session.execute(
                select(Claim.to_company_id, func.count())
                .where(Claim.to_company_id.in_(company_ids))
                .where(Claim.status == ClaimStatus.OPEN)
                .group_by(Claim.to_company_id)
            )
            open_claims_by_company = dict(claims_result.all())

        ratings_result = await session.execute(
            select(Rating.to_user_id, func.avg(Rating.score), func.count())
            .where(Rating.to_user_id.in_(carrier_ids))
            .group_by(Rating.to_user_id)
        )
        ratings = {row[0]: (row[1], row[2]) for row in ratings_result.all()}

    header = f"👥 <b>Отклики на груз #{cargo_id}</b>\n\n"
    try:
        await cb.message.edit_text(
            header,
            reply_markup=cargo_actions(cargo_id, True, cargo.status),
        )
    except TelegramBadRequest:
        pass

    for response in responses:
        user = users.get(response.carrier_id)
        profile = profiles.get(response.carrier_id)
        carrier_company = companies_by_user.get(response.carrier_id)
        rating_avg, rating_count = ratings.get(response.carrier_id, (None, 0))
        status = "⏳" if response.is_accepted is None else ("✅" if response.is_accepted else "❌")
        name = user.full_name if user else "Перевозчик"

        text = f"🚛 <b>{name}</b>\n"

        if carrier_company:
            rating = carrier_company.total_rating
            stars = "⭐" * rating + "☆" * (10 - rating)
            text += f"🏢 {carrier_company.company_name or 'Компания'}\n"
            text += f"📊 Рейтинг: {stars} ({rating}/10)\n"
            if rating < 4:
                text += "⚠️ <i>Низкий рейтинг — будьте внимательны</i>\n"
            open_claims = open_claims_by_company.get(carrier_company.id, 0)
            if open_claims > 0:
                text += f"🚨 Открытых претензий: {open_claims}\n"
        else:
            text += "⚠️ Компания не зарегистрирована\n"

        stars_old = "⭐" * round(rating_avg) if rating_avg else "нет оценок"
        text += f"⭐ Оценки: {stars_old} ({rating_count})\n"
        text += f"🛡 Верификация: {_verification_label(profile)}\n"
        if response.price_offer:
            text += f"💰 Ставка: {response.price_offer:,} ₽\n"
        if response.comment:
            text += f"💬 {response.comment}\n"

        reply_markup = None
        if response.is_accepted is None and cargo.status == CargoStatus.NEW:
            carrier_company_id = carrier_company.id if carrier_company else None
            reply_markup = response_actions(response.id, carrier_company_id)

        await cb.message.answer(text, reply_markup=reply_markup)

    await cb.answer()


@router.callback_query(F.data.startswith("accept_"))
async def accept_response_cb(cb: CallbackQuery):
    try:
        response_id = int(cb.data.split("_")[1])
    except:
        await cb.answer("❌ Некорректный отклик", show_alert=True)
        return

    async with async_session() as session:
        response = (
            await session.execute(select(CargoResponse).where(CargoResponse.id == response_id))
        ).scalar_one_or_none()
        if not response:
            await cb.answer("❌ Отклик не найден", show_alert=True)
            return

        cargo = (
            await session.execute(select(Cargo).where(Cargo.id == response.cargo_id))
        ).scalar_one_or_none()
        if not cargo or cargo.owner_id != cb.from_user.id:
            await cb.answer("❌ Нет доступа", show_alert=True)
            return

        if cargo.status != CargoStatus.NEW:
            await cb.answer("⚠️ Перевозчик уже выбран", show_alert=True)
            return

        response.is_accepted = True
        cargo.carrier_id = response.carrier_id
        cargo.status = CargoStatus.IN_PROGRESS

        others = await session.execute(
            select(CargoResponse).where(
                CargoResponse.cargo_id == cargo.id,
                CargoResponse.id != response_id,
                CargoResponse.is_accepted.is_(None)
            )
        )
        for other in others.scalars().all():
            other.is_accepted = False

        owner = (
            await session.execute(select(User).where(User.id == cargo.owner_id))
        ).scalar_one_or_none()
        carrier = (
            await session.execute(select(User).where(User.id == response.carrier_id))
        ).scalar_one_or_none()

        await session.commit()

    owner_phone = owner.phone if owner else None
    carrier_phone = carrier.phone if carrier else None

    try:
        if carrier:
            await bot.send_message(
                carrier.id,
                "✅ Ваш отклик принят. Сделка началась. Контакты открыты.\n\n"
                f"Заказчик: {owner.full_name if owner else 'N/A'} — {owner_phone or 'телефон не указан'}\n"
                "Доступные действия — в меню ниже.",
                reply_markup=deal_actions(cargo.id, False)
            )
    except:
        pass

    try:
        if owner and carrier:
            await bot.send_message(
                owner.id,
                "✅ Перевозчик выбран. Контакты открыты.\n\n"
                f"Перевозчик: {carrier.full_name} — {carrier_phone or 'телефон не указан'}\n"
                "Доступные действия — в меню ниже.",
                reply_markup=deal_actions(cargo.id, True)
            )
    except:
        pass

    try:
        await cb.message.edit_text(
            "✅ Перевозчик выбран. Контакты открыты.",
            reply_markup=deal_actions(cargo.id, True)
        )
    except TelegramBadRequest:
        pass

    await cb.answer()


@router.callback_query(F.data.startswith("reject_"))
async def reject_response_cb(cb: CallbackQuery):
    try:
        response_id = int(cb.data.split("_")[1])
    except:
        await cb.answer("❌ Некорректный отклик", show_alert=True)
        return

    async with async_session() as session:
        response = (
            await session.execute(select(CargoResponse).where(CargoResponse.id == response_id))
        ).scalar_one_or_none()
        if not response:
            await cb.answer("❌ Отклик не найден", show_alert=True)
            return

        cargo = (
            await session.execute(select(Cargo).where(Cargo.id == response.cargo_id))
        ).scalar_one_or_none()
        if not cargo or cargo.owner_id != cb.from_user.id:
            await cb.answer("❌ Нет доступа", show_alert=True)
            return

        response.is_accepted = False
        await session.commit()

    try:
        await cb.message.edit_text("❌ Отклик отклонён")
    except TelegramBadRequest:
        pass

    await cb.answer()

@router.callback_query(F.data.startswith("complete_"))
async def complete_cargo(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[1])

    async with async_session() as session:
        result = await session.execute(select(Cargo).where(Cargo.id == cargo_id))
        cargo = result.scalar_one_or_none()

        if not cargo or cargo.owner_id != cb.from_user.id:
            await cb.answer("❌ Нет доступа", show_alert=True)
            return

        cargo.status = CargoStatus.COMPLETED
        await session.commit()

        if cargo.carrier_id:
            try:
                await bot.send_message(
                    cargo.carrier_id,
                    f"✅ Груз #{cargo_id} завершён!\n\nОцени заказчика: /rate_{cargo_id}"
                )
            except:
                pass

    await cb.message.edit_text(
        f"✅ Груз #{cargo_id} завершён!\n\nОцени перевозчика: /rate_{cargo_id}",
        reply_markup=main_menu()
    )
    await cb.answer()
    logger.info(f"Cargo {cargo_id} completed")

@router.callback_query(F.data.startswith("cancel_"))
async def cancel_cargo(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[1])

    async with async_session() as session:
        result = await session.execute(select(Cargo).where(Cargo.id == cargo_id))
        cargo = result.scalar_one_or_none()

        if not cargo or cargo.owner_id != cb.from_user.id:
            await cb.answer("❌ Нет доступа", show_alert=True)
            return

        cargo.status = CargoStatus.CANCELLED
        await session.commit()

    await cb.message.edit_text(f"❌ Груз #{cargo_id} отменён", reply_markup=main_menu())
    await cb.answer()
    logger.info(f"Cargo {cargo_id} cancelled")

@router.callback_query(F.data.startswith("delete_yes_"))
async def delete_cargo_yes(cb: CallbackQuery):
    try:
        cargo_id = int(cb.data.split("_")[2])
    except:
        await cb.answer("❌ Ошибка", show_alert=True)
        return

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo:
            await cb.answer("Груз не найден", show_alert=True)
            return
        if cargo.owner_id != cb.from_user.id:
            await cb.answer("Нет доступа", show_alert=True)
            return
        if cargo.status != CargoStatus.NEW:
            await cb.answer("Нельзя удалить после начала сделки. Используй 'Отменить'.", show_alert=True)
            return

        await session.delete(cargo)
        await session.commit()

    try:
        await cb.message.edit_text(f"🗑 Груз #{cargo_id} удалён", reply_markup=main_menu())
    except TelegramBadRequest:
        await cb.message.answer(f"🗑 Груз #{cargo_id} удалён", reply_markup=main_menu())
    await cb.answer()
    logger.info(f"Cargo {cargo_id} deleted")

@router.callback_query(F.data.startswith("delete_no_"))
async def delete_cargo_no(cb: CallbackQuery):
    try:
        cargo_id = int(cb.data.split("_")[2])
    except:
        await cb.answer("❌ Ошибка", show_alert=True)
        return

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo:
            await cb.answer("Груз не найден", show_alert=True)
            return

        text, is_owner, owner_company_id, can_bump = await render_cargo_card(
            session, cargo, cb.from_user.id
        )

    try:
        await cb.message.edit_text(
            text,
            reply_markup=deal_actions(cargo.id, is_owner, has_ttn=bool(cargo.ttn_photo_file_id)) if cargo.status == CargoStatus.IN_PROGRESS else cargo_actions(
                cargo.id, is_owner, cargo.status, owner_company_id, can_bump=can_bump
            ),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            text,
            reply_markup=deal_actions(cargo.id, is_owner, has_ttn=bool(cargo.ttn_photo_file_id)) if cargo.status == CargoStatus.IN_PROGRESS else cargo_actions(
                cargo.id, is_owner, cargo.status, owner_company_id, can_bump=can_bump
            ),
        )
    await cb.answer()

@router.callback_query(F.data.startswith("delete_"))
async def delete_cargo_ask(cb: CallbackQuery):
    if cb.data.startswith("delete_yes_") or cb.data.startswith("delete_no_"):
        return

    try:
        cargo_id = int(cb.data.split("_")[1])
    except:
        await cb.answer("❌ Ошибка", show_alert=True)
        return

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo:
            await cb.answer("Груз не найден", show_alert=True)
            return
        if cargo.owner_id != cb.from_user.id:
            await cb.answer("Нет доступа", show_alert=True)
            return
        if cargo.status != CargoStatus.NEW:
            await cb.answer("Нельзя удалить после начала сделки. Используй 'Отменить'.", show_alert=True)
            return

    text = (
        f"🗑 <b>Удалить груз #{cargo_id}?</b>\n\n"
        "Он исчезнет из базы. Это действие нельзя отменить."
    )
    try:
        await cb.message.edit_text(text, reply_markup=delete_confirm_kb(cargo_id))
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=delete_confirm_kb(cargo_id))
    await cb.answer()

@router.callback_query(F.data.startswith("ttn_"))
async def send_ttn(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[1])

    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo:
            await cb.answer("❌ Груз не найден", show_alert=True)
            return

        owner = await session.get(User, cargo.owner_id)
        carrier = await session.get(User, cargo.carrier_id) if cargo.carrier_id else None
        
        # Получаем реквизиты компаний
        owner_company = await session.scalar(select(CompanyDetails).where(CompanyDetails.user_id == cargo.owner_id))
        carrier_company = None
        if cargo.carrier_id:
            carrier_company = await session.scalar(select(CompanyDetails).where(CompanyDetails.user_id == cargo.carrier_id))

    # Генерация документа по новым шаблонам (Синергия/Интеллект)
    from src.core.documents import generate_deal_documents
    pdf_bytes = generate_deal_documents(cargo, owner, carrier, owner_company, carrier_company)

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✍️ Подписать по SMS", callback_data=f"sign_sms_{cargo_id}"))
    b.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"cargo_open_{cargo_id}"))

    await cb.message.answer_document(
        BufferedInputFile(pdf_bytes, filename=f"Dogovor_Zayavka_{cargo_id}.pdf"),
        caption=f"📄 Сформирован договор-заявка №{cargo_id}.\n\nВы можете подписать его удаленно через SMS.",
        reply_markup=b.as_markup()
    )
    await cb.answer()


def _has_contact_access(user: User) -> bool:
    """Пробный период 14 дней или активный премиум."""
    if user.is_premium:
        if user.premium_until is None or user.premium_until >= datetime.utcnow():
            return True
    trial_days = (datetime.utcnow() - user.created_at).days
    return trial_days < 14


def _trial_days_left(user: User) -> int:
    return max(0, 14 - (datetime.utcnow() - user.created_at).days)


@router.callback_query(F.data.startswith("unlock_cargo_contact:"))
async def unlock_contact(cb: CallbackQuery):
    cargo_id = int(cb.data.split(":")[-1])
    
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo:
            await cb.answer("❌ Груз не найден", show_alert=True)
            return

        user = await session.get(User, cb.from_user.id)
        if not user:
            await cb.answer("❌ Зарегистрируйтесь в боте", show_alert=True)
            return

        # Если есть доступ (Premium или Trial)
        if _has_contact_access(user):
            phone = None
            if cargo.source_platform != "manual":
                cutoff = cargo.created_at - timedelta(minutes=15)
                event = await session.scalar(
                    select(ParserIngestEvent)
                    .where(
                        ParserIngestEvent.from_city.ilike(f"%{cargo.from_city}%"),
                        ParserIngestEvent.to_city.ilike(f"%{cargo.to_city}%"),
                        ParserIngestEvent.phone.isnot(None),
                        ParserIngestEvent.created_at >= cutoff,
                    )
                    .order_by(ParserIngestEvent.created_at.desc())
                    .limit(1)
                )
                phone = event.phone if event else None
            
            if not phone:
                owner = await session.get(User, cargo.owner_id)
                phone = owner.phone if owner else None

            if phone:
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=f"📞 Позвонить", url=f"tel:{phone}")
                ]])
                await cb.message.answer(
                    f"📞 <b>Контакт по грузу #{cargo_id}</b>\n\n"
                    f"Телефон: <code>{phone}</code>\n\n"
                    f"💡 <i>Вы видите этот контакт, так как у вас активен Premium доступ.</i>",
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            else:
                await cb.answer("📭 Телефон не указан заказчиком", show_alert=True)
            return

        # НЕТ ДОСТУПА — ПРЕДЛАГАЕМ ОПЛАТУ ЗВЕЗДАМИ
        stars_amount = 50 # Разовая цена за открытие контакта
        title = f"Открыть контакт груза #{cargo_id}"
        description = f"Маршрут: {cargo.from_city} → {cargo.to_city}. Открывает прямой телефон заказчика."
        invoice_payload = f"cargo_unlock:{cb.from_user.id}:{cargo_id}:{stars_amount}"

        await cb.bot.send_invoice(
            chat_id=cb.from_user.id,
            title=title,
            description=description,
            payload=invoice_payload,
            currency="XTR",
            prices=[LabeledPrice(label=title, amount=stars_amount)],
            provider_token=None,
            start_parameter=f"unlock_{cargo_id}",
        )
        await cb.answer()
