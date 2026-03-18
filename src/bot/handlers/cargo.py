from src.core.config import settings
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, or_, func
from datetime import datetime, timedelta
import re
from src.bot.states import CargoForm, EditCargo
from src.bot.keyboards import main_menu, confirm_kb, cargo_actions, cargos_menu, cargo_open_list_kb, skip_kb, response_actions, deal_actions, city_kb, delete_confirm_kb, my_cargos_kb, cargo_edit_kb, price_suggest_kb, cancel_kb
from src.bot.utils import cargo_deeplink
from src.bot.utils.cities import city_suggest
from src.core.ai import _parse_search_simple, parse_cargo_nlp, parse_city, parse_load_datetime
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
)
from src.core.schemas.sync import SharedOrderSchema, SharedSyncEvent
from src.core.services.cross_sync import make_search_id, publish_sync_event
from src.core.documents import generate_ttn
from src.core.logger import logger
from src.bot.bot import bot

router = Router()

CANCEL_HINT = "\n\n❌ Отмена: /cancel"
STOP_WORDS = {"да", "ок", "okay", "привет", "hello", "hi", "угу", "ага"}
SEARCH_PREFIXES = ("ищу ", "найди", "подбери", "/find", "find ")


class CargoNLPConfirm(StatesGroup):
    wait_confirm = State()

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


def _looks_like_cargo_offer_text(text: str, parsed: dict | None) -> bool:
    raw = (text or "").strip()
    lowered = raw.lower()
    if not raw or raw.startswith("/"):
        return False
    if any(lowered.startswith(prefix) for prefix in SEARCH_PREFIXES):
        return False
    if not parsed:
        return False
    if not (parsed.get("from_city") and parsed.get("to_city")):
        return False
    if parsed.get("weight") is None and parsed.get("volume_m3") is None:
        return False
    truck_hints = (
        "ищу машину",
        "нужна машина",
        "подбери машину",
        "камаз",
        "тонник",
        "манипулятор",
        "трал",
        "самосвал",
        "газель",
    )
    if any(hint in lowered for hint in truck_hints):
        return False
    offer_hints = (
        "завтра", "послезавтра", "сегодня", "тнп", "паллет", "паллета", "короб",
        "доски", "металл", "кирпич", "цемент", "нал", "безнал", "ставка", "руб", "₽", "к ",
    )
    if any(hint in lowered for hint in offer_hints):
        return True
    cargo_type = str(parsed.get("cargo_type") or "").strip().lower()
    if cargo_type and cargo_type not in {"груз", "тент"}:
        return True
    return True


def _looks_like_cargo_candidate_text(text: str | None) -> bool:
    raw = (text or "").strip()
    if not raw or raw.startswith("/"):
        return False
    lowered = raw.lower()
    if lowered in STOP_WORDS:
        return False
    if any(lowered.startswith(prefix) for prefix in SEARCH_PREFIXES):
        return False

    route_params = _parse_search_simple(raw) or {}
    has_route = bool(route_params.get("from_city") and route_params.get("to_city"))
    if not has_route:
        has_route = bool(re.search(r"из\s+[А-Яа-яЁё][А-Яа-яЁё\s\-]+\s+в\s+[А-Яа-яЁё]", raw))
    has_weight = bool(re.search(r"(\d+(?:[.,]\d+)?)\s*(?:кг|kg|т\b|t\b|тн\b|тонн(?:а|ы)?)", lowered))
    has_volume = bool(re.search(r"(\d+(?:[.,]\d+)?)\s*(?:м3|м³|m3|куб(?:\.|а|ов)?(?:ик)?|кубовик)\b", lowered))
    has_date = any(token in lowered for token in ("сегодня", "завтра", "послезавтра"))
    has_cargo_hint = any(
        token in lowered
        for token in (
            "тнп", "паллет", "паллета", "короб", "доски", "металл", "кирпич", "цемент",
            "реф", "замороз", "товар", "строймат",
        )
    )
    return has_route and (has_weight or has_volume or has_date or has_cargo_hint)


def _cargo_nlp_preview_text(parsed: dict) -> str:
    parts = ["📦 <b>Распознал груз</b>"]
    route = f"{parsed.get('from_city', '—')} → {parsed.get('to_city', '—')}"
    parts.append(f"📍 {route}")
    if parsed.get("cargo_type") or parsed.get("body_type"):
        type_bits: list[str] = []
        if parsed.get("cargo_type"):
            type_bits.append(str(parsed["cargo_type"]))
        if parsed.get("body_type"):
            type_bits.append(str(parsed["body_type"]))
        parts.append(f"🚛 {' • '.join(type_bits)}")
    if parsed.get("weight") is not None:
        parts.append(f"⚖️ {parsed['weight']} т")
    if parsed.get("price"):
        parts.append(f"💰 {int(parsed['price']):,} ₽".replace(",", " "))
    if parsed.get("load_date"):
        when = str(parsed["load_date"])
        if parsed.get("load_time"):
            when += f" в {parsed['load_time']}"
        parts.append(f"📅 {when}")
    parts.append("")
    parts.append("Разместить этот груз или перейти к ручному заполнению?")
    return "\n".join(parts)


def _cargo_nlp_kb() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📦 Разместить груз", callback_data="cargo_nlp_publish"))
    builder.row(InlineKeyboardButton(text="✏️ Заполнить вручную", callback_data="cargo_nlp_manual"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cargo_nlp_cancel"))
    return builder


async def _start_cargo_nlp_preview(message: Message, state: FSMContext, text: str) -> bool:
    logger.info("cargo.free_text candidate=%r", text)

    parsed = await parse_cargo_nlp(text)
    logger.info("cargo.free_text parsed=%s text=%r", parsed, text)
    if not _looks_like_cargo_offer_text(text, parsed):
        logger.info("cargo.free_text classified=skip text=%r", text)
        return False

    route_params = _parse_search_simple(text)
    logger.info("cargo.free_text route_params=%s text=%r", route_params, text)
    if route_params:
        if route_params.get("from_city"):
            parsed["from_city"] = route_params["from_city"]
        if route_params.get("to_city"):
            parsed["to_city"] = route_params["to_city"]
        if route_params.get("min_weight") is not None and parsed.get("weight") is None:
            parsed["weight"] = route_params["min_weight"]

    await state.set_state(CargoNLPConfirm.wait_confirm)
    await state.update_data(cargo_nlp_draft=parsed)
    await message.answer(
        _cargo_nlp_preview_text(parsed),
        reply_markup=_cargo_nlp_kb().as_markup(),
    )
    return True


async def _create_cargo_from_draft(owner_id: int, data: dict) -> Cargo:
    load_date = datetime.now()
    if data.get("load_date"):
        raw = str(data["load_date"])
        try:
            load_date = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            pass

    cargo = Cargo(
        owner_id=owner_id,
        from_city=str(data["from_city"]),
        to_city=str(data["to_city"]),
        cargo_type=str(data.get("cargo_type") or "Груз"),
        weight=float(data.get("weight") or 0),
        price=int(data.get("price") or 0),
        load_date=load_date,
        load_time=data.get("load_time"),
        comment=data.get("comment"),
        source_platform="tg-bot",
    )
    async with async_session() as session:
        session.add(cargo)
        await session.commit()
        await session.refresh(cargo)
    return cargo


def _cargo_post_publish_kb(cargo_id: int) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔎 Найти машину под этот груз",
            callback_data=f"cargo_find_truck_{cargo_id}",
        )
    )
    builder.row(InlineKeyboardButton(text="🧾 Мои грузы", callback_data="my_cargos"))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"))
    return builder


def _cargo_prefill_summary(data: dict) -> str:
    bits = []
    if data.get("from_city") or data.get("to_city"):
        bits.append(f"📍 {data.get('from_city', '—')} → {data.get('to_city', '—')}")
    if data.get("cargo_type"):
        bits.append(f"📦 {data['cargo_type']}")
    if data.get("weight") is not None:
        bits.append(f"⚖️ {data['weight']} т")
    if data.get("price"):
        bits.append(f"💰 {int(data['price']):,} ₽".replace(",", " "))
    if data.get("load_date"):
        when = str(data["load_date"])
        if data.get("load_time"):
            when += f" в {data['load_time']}"
        bits.append(f"📅 {when}")
    return "\n".join(bits)


async def _prompt_price_step(message: Message, state: FSMContext):
    data = await state.get_data()
    from_city = data.get("from_city")
    to_city = data.get("to_city")
    cargo_type = data.get("cargo_type", "тент")
    weight = data.get("weight")

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


async def _continue_cargo_prefill(message: Message, state: FSMContext):
    data = await state.get_data()

    if not data.get("from_city"):
        await message.answer(
            "📦 <b>Новый груз — Шаг 1/6</b>\n\n"
            "<b>Откуда?</b> Начни вводить город (например: «самар», «мос», «спб»)",
            reply_markup=cancel_kb(),
        )
        await state.set_state(CargoForm.from_city)
        return

    if not data.get("to_city"):
        await message.answer(
            f"✅ Откуда: {data['from_city']}\n\n"
            "📦 <b>Шаг 2/6 — Куда?</b>\n\nВведи город назначения:",
            reply_markup=cancel_kb(),
        )
        await state.set_state(CargoForm.to_city)
        return

    if not data.get("cargo_type"):
        await message.answer(
            "📦 <b>Шаг 3/6 — Тип груза</b>\n\nЧто везём? Например: паллеты, металл, оборудование, продукты",
            reply_markup=cancel_kb(),
        )
        await state.set_state(CargoForm.cargo_type)
        return

    if data.get("weight") is None:
        await message.answer(
            "📦 <b>Шаг 4/6 — Вес</b>\n\nСколько тонн? (число, например: 5 или 20)",
            reply_markup=cancel_kb(),
        )
        await state.set_state(CargoForm.weight)
        return

    if data.get("price") is None:
        await _prompt_price_step(message, state)
        return

    if not data.get("load_date"):
        await message.answer(
            "📦 <b>Шаг 6/6 — Дата загрузки</b>\n\n"
            "Когда нужна машина?\n\n"
            "Можно написать: <b>сегодня</b>, <b>завтра</b> или дату <b>15.03</b>",
            reply_markup=cancel_kb(),
        )
        await state.set_state(CargoForm.load_date)
        return

    if not data.get("load_time"):
        await message.answer(
            "🕐 Время загрузки? (ЧЧ:ММ)\n\nПропустить — нажми кнопку",
            reply_markup=skip_kb(),
        )
        await state.set_state(CargoForm.load_time)
        return

    if data.get("comment") is None:
        await message.answer("💬 Комментарий?", reply_markup=skip_kb())
        await state.set_state(CargoForm.comment)
        return

    await show_confirm(message, state)


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


def _status_timeline(cargo: "Cargo") -> str:
    """Returns visual status bar for cargo card."""
    from src.core.models import CargoStatus as _CS
    steps = [
        ("📋", "Размещён"),
        ("🔍", "Ищем"),
        ("👤", "Найден"),
        ("🚛", "В пути"),
        ("✅", "Доставлен"),
    ]
    if cargo.status in (_CS.CANCELLED,):
        return "❌ Отменён"
    if cargo.status == _CS.ARCHIVED:
        return "🗄️ Архив"

    if cargo.status == _CS.COMPLETED:
        cur = 4
    elif cargo.status == _CS.IN_PROGRESS:
        cur = 3 if cargo.pickup_confirmed_at else 2
    else:  # NEW / ACTIVE
        cur = 1

    parts = []
    for i, (emoji, label) in enumerate(steps):
        if i < cur:
            parts.append(f"✅ {label}")
        elif i == cur:
            parts.append(f"{emoji} <b>{label}</b>")
        else:
            parts.append(f"○ {label}")
    return " → ".join(parts)

async def render_cargo_card(session, cargo: Cargo, viewer_id: int) -> tuple[str, bool, int | None]:
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
    text += f"💰 {cargo.price} ₽\n"
    text += f"📅 {cargo.load_date.strftime('%d.%m.%Y')}"
    if cargo.load_time:
        text += f" в {cargo.load_time}"
    text += "\n"
    text += f"\n📊 {_status_timeline(cargo)}\n\n"
    if cargo.comment:
        text += f"💬 {cargo.comment}\n"
    if cargo.photo_file_id and cargo.photo_approved:
        text += "📸 Есть фото груза (одобрено)\n"
    elif cargo.photo_file_id:
        text += "📸 Фото на проверке\n"

    is_owner = cargo.owner_id == viewer_id
    is_carrier = cargo.carrier_id == viewer_id if cargo.carrier_id else False
    is_participant = is_owner or is_carrier
    can_show_contacts = is_participant and cargo.status in {CargoStatus.IN_PROGRESS, CargoStatus.COMPLETED}

    if owner:
        text += f"\n👤 Заказчик: {owner.full_name if owner.full_name else owner.id}"
        if owner_company:
            rating = owner_company.total_rating
            stars = "⭐" * rating + "☆" * (10 - rating)
            text += f"\n🏢 {owner_company.company_name or 'Компания'}"
            text += f"\n📊 Рейтинг: {stars} ({rating}/10)"
        else:
            if viewer_id == settings.admin_id:
                text += "\n⚠️ Компания не зарегистрирована"
        stars_old = "⭐" * round(avg_rating) if avg_rating else "нет оценок"
        text += f"\n⭐ Оценки: {stars_old} ({rating_count or 0})"
        if viewer_id == settings.admin_id:
            text += f"\n🛡 Верификация: {_verification_label(owner_profile)}"
        text += "\n🔒 Контакт скрыт — откройте через подписку или разово"

    owner_company_id = owner_company.id if owner_company else None
    return text, is_owner, owner_company_id




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
    can_show_contacts = is_participant and cargo.status in {CargoStatus.IN_PROGRESS, CargoStatus.COMPLETED}

    text = f"📦 <b>Груз #{cargo.id}</b>\n\n"
    text += f"📍 {cargo.from_city} → {cargo.to_city}\n"
    text += f"📦 {cargo.cargo_type}\n"
    text += f"⚖️ {cargo.weight} т\n"
    text += f"💰 {cargo.price} ₽\n"
    text += f"📅 {cargo.load_date.strftime('%d.%m.%Y')}"
    if cargo.load_time:
        text += f" в {cargo.load_time}"
    text += "\n"
    text += f"\n📊 {_status_timeline(cargo)}\n\n"
    if cargo.comment:
        text += f"💬 {cargo.comment}\n"

    owner_name = owner.full_name if owner else "N/A"
    text += f"\n👤 Заказчик: {owner_name}"

    if owner_company:
        rating = owner_company.total_rating
        stars = "⭐" * rating + "☆" * (10 - rating)
        text += f"\n🏢 {owner_company.company_name or 'Компания'}"
        text += f"\n📊 Рейтинг: {stars} ({rating}/10)"
    else:
        if message.from_user.id == settings.admin_id:
            text += "\n⚠️ Компания не зарегистрирована"

    stars = "⭐" * round(avg_rating) if avg_rating else "нет оценок"
    text += f"\n⭐ Оценки: {stars} ({rating_count or 0})"
    if message.from_user.id == settings.admin_id:
        text += f"\n🛡 Верификация: {_verification_label(owner_profile)}"
    if not is_owner:
        text += "\n🔒 Контакт скрыт — откройте через подписку или разово"

    if cargo.status == CargoStatus.IN_PROGRESS and is_participant:
        text += "\n\n🗺 Трекинг доступен в меню сделки"

    owner_company_id = owner_company.id if owner_company else None
    if cargo.status == CargoStatus.IN_PROGRESS and is_participant:
        reply_markup = deal_actions(cargo.id, is_owner, pickup_confirmed=bool(cargo.pickup_confirmed_at))
    else:
        reply_markup = cargo_actions(
            cargo.id, is_owner, cargo.status, owner_company_id,
            show_unlock=not is_owner,
        )

    await message.answer(text, reply_markup=reply_markup)
    return True

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

        text, is_owner, owner_company_id = await render_cargo_card(
            session, cargo, cb.from_user.id
        )

    try:
        await cb.message.edit_text(
            text,
            reply_markup=cargo_actions(
                cargo.id, is_owner, cargo.status, owner_company_id
            ),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            text,
            reply_markup=cargo_actions(
                cargo.id, is_owner, cargo.status, owner_company_id
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
    await cb.message.edit_text("💰 Введи новую цену (₽):\n\n<i>Отмена — /cancel</i>", reply_markup=cancel_kb())
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
        reply_markup=cancel_kb(),
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
        reply_markup=cancel_kb(),
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
    await cb.message.edit_text("💬 Введи новый комментарий:\n\n<i>Отмена — /cancel</i>", reply_markup=cancel_kb())
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




@router.callback_query(F.data.startswith("pickup_confirm_"))
async def pickup_confirm(cb: CallbackQuery):
    """Carrier confirms they picked up the cargo — sets pickup_confirmed_at."""
    from datetime import datetime as _dt
    cargo_id = int(cb.data.split("_")[2])
    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo or cargo.carrier_id != cb.from_user.id:
            await cb.answer("Нет доступа", show_alert=True)
            return
        if cargo.pickup_confirmed_at:
            await cb.answer("Уже отмечено ранее", show_alert=False)
            return
        cargo.pickup_confirmed_at = _dt.utcnow()
        owner_id = cargo.owner_id
        cargo_from = cargo.from_city
        cargo_to = cargo.to_city
        await session.commit()

    # Уведомляем заказчика
    try:
        from src.bot.bot import bot as _bot
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton as _IKB
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [_IKB(text=f"📦 Открыть груз #{cargo_id}", callback_data=f"cargo_open_{cargo_id}")]
        ])
        await _bot.send_message(
            owner_id,
            f"🚛 <b>Перевозчик забрал груз и выехал!</b>\n\n"
            f"📍 {cargo_from} → {cargo_to}\n"
            f"Груз #{cargo_id} в пути.",
            reply_markup=kb,
        )
    except Exception as _e:
        logger.warning("pickup notify failed: %s", _e)

    await cb.answer("✅ Отмечено — заказчик уведомлён", show_alert=False)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.answer(
            "🚛 <b>Груз в пути!</b> Заказчик получил уведомление.",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("photo_approve_"))
async def photo_approve(cb: CallbackQuery):
    from src.core.config import settings as _s
    admin_ids = {_s.admin_id, _s.admin_chat_id} - {None}
    if cb.from_user.id not in admin_ids and cb.message.chat.id not in admin_ids:
        await cb.answer("Нет доступа", show_alert=True)
        return
    cargo_id = int(cb.data.split("_")[2])
    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if cargo:
            cargo.photo_approved = True
            await session.commit()
    try:
        await cb.message.edit_caption(
            caption=(cb.message.caption or "") + "\n\n✅ Одобрено",
            reply_markup=None,
        )
    except Exception:
        pass
    await cb.answer("✅ Фото одобрено")


@router.callback_query(F.data.startswith("photo_reject_"))
async def photo_reject(cb: CallbackQuery):
    from src.core.config import settings as _s
    admin_ids = {_s.admin_id, _s.admin_chat_id} - {None}
    if cb.from_user.id not in admin_ids and cb.message.chat.id not in admin_ids:
        await cb.answer("Нет доступа", show_alert=True)
        return
    cargo_id = int(cb.data.split("_")[2])
    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if cargo:
            cargo.photo_file_id = None
            cargo.photo_approved = False
            await session.commit()
    try:
        await cb.message.edit_caption(
            caption=(cb.message.caption or "") + "\n\n🚫 Удалено",
            reply_markup=None,
        )
    except Exception:
        pass
    await cb.answer("🚫 Фото удалено")



@router.callback_query(F.data.startswith("rate_inline_"))
async def rate_inline_start(cb: CallbackQuery, state: FSMContext):
    """Запускает оценку из инлайн-кнопки после завершения рейса."""
    cargo_id = int(cb.data.split("_")[2])
    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo:
            await cb.answer("Груз не найден", show_alert=True)
            return
        if cargo.owner_id == cb.from_user.id:
            to_user_id = cargo.carrier_id
        elif cargo.carrier_id == cb.from_user.id:
            to_user_id = cargo.owner_id
        else:
            await cb.answer("Нет доступа", show_alert=True)
            return
        if not to_user_id:
            await cb.answer("Некого оценивать", show_alert=True)
            return
        existing = await session.scalar(
            select(Rating).where(
                Rating.cargo_id == cargo_id,
                Rating.from_user_id == cb.from_user.id,
            )
        )
        if existing:
            await cb.answer("Ты уже оценил этот рейс 👍", show_alert=False)
            return

    await state.update_data(cargo_id=cargo_id, to_user_id=to_user_id)
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton as _IKB
    stars_kb = InlineKeyboardMarkup(inline_keyboard=[[
        _IKB(text="⭐", callback_data="rate_star_1"),
        _IKB(text="⭐⭐", callback_data="rate_star_2"),
        _IKB(text="⭐⭐⭐", callback_data="rate_star_3"),
        _IKB(text="⭐⭐⭐⭐", callback_data="rate_star_4"),
        _IKB(text="⭐⭐⭐⭐⭐", callback_data="rate_star_5"),
    ]])
    try:
        await cb.message.edit_text(
            f"⭐ Оцени контрагента по рейсу #{cargo_id}:",
            reply_markup=stars_kb,
        )
    except Exception:
        await cb.message.answer(
            f"⭐ Оцени контрагента по рейсу #{cargo_id}:",
            reply_markup=stars_kb,
        )
    await state.set_state(RateForm.score)
    await cb.answer()


@router.callback_query(F.data.startswith("repeat_cargo_"))
async def repeat_cargo_preview(cb: CallbackQuery, state: FSMContext):
    """Показываем превью повторного груза — дата = сегодня."""
    from datetime import date as _date, timedelta
    cargo_id = int(cb.data.split("_")[2])

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo or cargo.owner_id != cb.from_user.id:
            await cb.answer("❌ Груз не найден", show_alert=True)
            return

    today = _date.today()
    tomorrow = today + timedelta(days=1)

    text = (
        "♻️ <b>Повторить груз</b>\n\n"
        f"📍 {cargo.from_city} → {cargo.to_city}\n"
        f"📦 {cargo.cargo_type} · {cargo.weight} т\n"
        f"💰 {cargo.price:,} ₽\n"
        f"📅 Дата загрузки: <b>сегодня {today.strftime('%d.%m')}</b>\n\n"
        "Опубликовать с теми же параметрами?"
    )
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton as _IKB
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_IKB(text="✅ Опубликовать сегодня", callback_data=f"confirm_repeat_{cargo_id}_today")],
        [_IKB(text="📅 На завтра", callback_data=f"confirm_repeat_{cargo_id}_tomorrow")],
        [_IKB(text="◀️ Назад", callback_data=f"cargo_open_{cargo_id}")],
    ])
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("confirm_repeat_"))
async def confirm_repeat_cargo(cb: CallbackQuery):
    """Создаём копию груза с новой датой."""
    from datetime import date as _date, timedelta as _timedelta
    from src.core.services.notifications import notify_subscribers

    parts = cb.data.split("_")
    # confirm_repeat_{id}_{when}
    cargo_id = int(parts[2])
    when = parts[3] if len(parts) > 3 else "today"

    async with async_session() as session:
        src = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not src or src.owner_id != cb.from_user.id:
            await cb.answer("❌ Груз не найден", show_alert=True)
            return

        load_date = _date.today() if when == "today" else _date.today() + _timedelta(days=1)
        new_cargo = Cargo(
            owner_id=src.owner_id,
            from_city=src.from_city,
            to_city=src.to_city,
            cargo_type=src.cargo_type,
            weight=src.weight,
            price=src.price,
            load_date=load_date,
            load_time=src.load_time,
            comment=src.comment,
            source_platform="tg-bot-repeat",
        )
        session.add(new_cargo)
        await session.commit()
        await session.refresh(new_cargo)
        new_id = new_cargo.id

    date_label = "сегодня" if when == "today" else "завтра"
    try:
        await cb.message.edit_text(
            f"✅ Груз #{new_id} опубликован на <b>{date_label}</b>!\n\n"
            f"📍 {new_cargo.from_city} → {new_cargo.to_city}",
            reply_markup=_cargo_post_publish_kb(new_id).as_markup(),
        )
    except Exception:
        await cb.message.answer(
            f"✅ Груз #{new_id} опубликован!",
            reply_markup=_cargo_post_publish_kb(new_id).as_markup(),
        )

    try:
        await notify_subscribers(new_cargo)
    except Exception as e:
        logger.warning("notify failed for repeat cargo #%s: %s", new_id, e)

    await cb.answer("Опубликовано!")

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
        await _publish_cargo_sync_event(restored, event_type="cargo.created")
    except Exception as e:
        logger.warning("Cross-sync publish failed for cargo #%s: %s", new_id, e)

    await cb.message.answer(f"✅ Груз восстановлен как #{new_id}")
    await send_cargo_details(cb.message, new_id)
    await cb.answer()

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
        "📦 <b>Новый груз — Шаг 1/6</b>\n\n"
        "<b>Откуда?</b> Начни вводить город (например: «самар», «мос», «спб»)",
        reply_markup=cancel_kb(),
    )
    await state.set_state(CargoForm.from_city)
    await cb.answer()




@router.message(StateFilter(CargoForm), F.text.func(lambda t: (t or "").strip().lower() in {"отмена", "cancel", "стоп", "/cancel"}))
async def cancel_cargo_form(message: Message, state: FSMContext):
    """Отмена из любого шага формы груза."""
    await state.clear()
    await message.answer("❌ Размещение отменено.", reply_markup=main_menu())

@router.message(F.text, CargoNLPConfirm.wait_confirm)
async def cargo_nlp_ignore_text_while_confirm(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Используй кнопки ниже: разместить, заполнить вручную или отменить.")
        return

    if _looks_like_cargo_candidate_text(text):
        await state.clear()
        try:
            if await _start_cargo_nlp_preview(message, state, text):
                return
        except Exception as exc:
            logger.exception("cargo.free_text replace failed text=%r error=%s", text, exc)
            await message.answer("Не удалось разобрать заявку. Попробуйте еще раз.")
            return

    from src.core.truck_search import (
        extract_truck_search_params,
        looks_like_truck_offer_text,
        looks_like_truck_search_text,
    )

    if looks_like_truck_offer_text(text) or looks_like_truck_search_text(text):
        await state.clear()
        from src.bot.handlers.trucks import _reply_truck_offer_hint, _run_match_and_reply

        try:
            if looks_like_truck_offer_text(text):
                await _reply_truck_offer_hint(message, text)
                return

            params = await extract_truck_search_params(text)
            if not params or not params.get("from_city"):
                await message.answer(
                    "Напишите запрос свободным текстом, например:\n"
                    "<code>ищу машину из Москвы в Самару 4 тонны завтра</code>\n\n"
                    "Или используйте /findtruck для пошагового подбора."
                )
                return

            await _run_match_and_reply(
                message=message,
                from_city=params.get("from_city"),
                to_city=params.get("to_city"),
                weight=params.get("weight"),
                truck_type=params.get("truck_type"),
            )
            return
        except Exception as exc:
            logger.exception("truck.free_text replace failed text=%r error=%s", text, exc)
            await message.answer("Не удалось выполнить подбор. Попробуйте еще раз.")
            return

    await message.answer("Используй кнопки ниже: разместить, заполнить вручную или отменить.")


@router.message(StateFilter(None), F.text.func(_looks_like_cargo_candidate_text))
async def cargo_nlp_shortcut(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is not None:
        return

    text = (message.text or "").strip()
    if not text or text.startswith("/") or text.lower() in STOP_WORDS:
        return

    try:
        await _start_cargo_nlp_preview(message, state, text)
    except Exception as exc:
        logger.exception("cargo.free_text failed text=%r error=%s", text, exc)
        await message.answer("Не удалось разобрать заявку. Попробуйте еще раз.")


@router.callback_query(CargoNLPConfirm.wait_confirm, F.data == "cargo_nlp_publish")
async def cargo_nlp_publish(cb: CallbackQuery, state: FSMContext):
    from src.core.services.notifications import notify_subscribers
    from datetime import datetime as _dt

    data = await state.get_data()
    draft = data.get("cargo_nlp_draft") or {}
    if not draft:
        await state.clear()
        await cb.message.edit_text("Черновик груза потерялся. Попробуй еще раз.", reply_markup=main_menu())
        await cb.answer()
        return

    cargo = await _create_cargo_from_draft(cb.from_user.id, draft)
    await state.clear()
    await cb.message.edit_text(
        f"✅ Груз #{cargo.id} опубликован!\n\n"
        "Хочешь сразу подобрать под него машину?",
        reply_markup=_cargo_post_publish_kb(cargo.id).as_markup(),
    )

    try:
        await notify_subscribers(cargo)
        async with async_session() as session:
            c = await session.scalar(select(Cargo).where(Cargo.id == cargo.id))
            if c:
                c.notified_at = _dt.utcnow()
                await session.commit()
    except Exception as e:
        logger.warning("Notification failed for cargo #%s: %s", cargo.id, e)

    try:
        await _publish_cargo_sync_event(cargo, event_type="cargo.created")
    except Exception as e:
        logger.warning("Cross-sync publish failed for cargo #%s: %s", cargo.id, e)

    await cb.answer("Груз размещен")


@router.callback_query(CargoNLPConfirm.wait_confirm, F.data == "cargo_nlp_manual")
async def cargo_nlp_manual(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    draft = dict(data.get("cargo_nlp_draft") or {})
    await state.clear()
    await state.update_data(
        from_city=draft.get("from_city"),
        to_city=draft.get("to_city"),
        cargo_type=draft.get("cargo_type"),
        weight=draft.get("weight"),
        price=draft.get("price"),
        load_date=draft.get("load_date"),
        load_time=draft.get("load_time"),
        comment=draft.get("comment"),
    )
    summary = _cargo_prefill_summary(draft)
    await cb.message.edit_text(
        "✏️ <b>Заполняем вручную</b>\n\n"
        "Я уже подставил то, что смог распознать:\n"
        f"{summary or 'Пока без распознанных полей.'}\n\n"
        "Сейчас уточним недостающие данные.",
    )
    await _continue_cargo_prefill(cb.message, state)
    await cb.answer()


@router.callback_query(CargoNLPConfirm.wait_confirm, F.data == "cargo_nlp_cancel")
async def cargo_nlp_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Отменено", reply_markup=main_menu())
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
        f"✅ Откуда: <b>{city}</b>\n\n"
        "📦 <b>Шаг 2/6 — Куда?</b>\n\nВведи город назначения:",
        reply_markup=cancel_kb(),
    )
    await cb.answer()

@router.callback_query(CargoForm.to_city, F.data.startswith("city:to:"))
async def cargo_to_select(cb: CallbackQuery, state: FSMContext):
    _, _, city = cb.data.split(":", 2)
    await state.update_data(to_city=city)
    await state.set_state(CargoForm.cargo_type)
    await cb.message.edit_text(
        f"✅ Куда: <b>{city}</b>\n\n"
        "📦 <b>Шаг 3/6 — Тип груза</b>\n\nЧто везём? Например: паллеты, металл, оборудование, продукты",
        reply_markup=cancel_kb(),
    )
    await cb.answer()

@router.message(CargoForm.cargo_type)
async def cargo_type(message: Message, state: FSMContext):
    await state.update_data(cargo_type=message.text)
    await message.answer(
        "📦 <b>Шаг 4/6 — Вес</b>\n\nСколько тонн? (число, например: 5 или 20)",
        reply_markup=cancel_kb(),
    )
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
        await message.answer("❌ Введи число. Пример: 20 или 5.5", reply_markup=cancel_kb())
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
        await message.answer("❌ Введи число", reply_markup=cancel_kb())
        return

    await state.update_data(price=price)
    await message.answer(
        "📦 <b>Шаг 6/6 — Дата загрузки</b>\n\n"
        "Когда нужна машина?\n\n"
        "Можно написать: <b>сегодня</b>, <b>завтра</b> или дату <b>15.03</b>",
        reply_markup=cancel_kb(),
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
    await ask_photo(message, state)

@router.callback_query(CargoForm.comment, F.data == "skip")
async def cargo_skip_comment(cb: CallbackQuery, state: FSMContext):
    await state.update_data(comment=None)
    await ask_photo(cb.message, state)
    await cb.answer()


async def ask_photo(message: Message, state: FSMContext):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton as _IKB
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_IKB(text="⏭ Пропустить", callback_data="skip_photo")],
        [_IKB(text="❌ Отмена", callback_data="cancel")],
    ])
    await message.answer(
        "📸 Прикрепи фото груза (необязательно)\n\n"
        "Фото повышает доверие — перевозчики охотнее откликаются.",
        reply_markup=kb,
    )
    await state.set_state(CargoForm.photo)


@router.message(CargoForm.photo, F.photo)
async def cargo_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id  # берём самое большое
    await state.update_data(photo_file_id=file_id)
    await show_confirm(message, state)


@router.callback_query(CargoForm.photo, F.data == "skip_photo")
async def cargo_skip_photo(cb: CallbackQuery, state: FSMContext):
    await state.update_data(photo_file_id=None)
    await show_confirm(cb.message, state)
    await cb.answer()


@router.message(CargoForm.photo)
async def cargo_photo_wrong(message: Message, state: FSMContext):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton as _IKB
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_IKB(text="⏭ Пропустить", callback_data="skip_photo")],
    ])
    await message.answer("📸 Отправь фото или нажми «Пропустить»", reply_markup=kb)

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
    text = f"📦 <b>Подтверди публикацию:</b>\n\n"
    text += f"📍 {data['from_city']} → {data['to_city']}\n"
    text += f"📦 {data['cargo_type']}\n"
    text += f"⚖️ {data['weight']} т\n"
    text += f"💰 {data['price']} ₽\n"
    text += f"📅 {load_date.strftime('%d.%m.%Y')}"
    if data.get("load_time"):
        text += f" в {data['load_time']}"
    text += "\n"
    if data.get('comment'):
        text += f"💬 {data['comment']}\n"
    if data.get('photo_file_id'):
        text += "📸 Фото прикреплено\n"
    if data.get('photo_file_id'):
        await message.answer_photo(data['photo_file_id'], caption=text, reply_markup=confirm_kb())
    else:
        await message.answer(text, reply_markup=confirm_kb())
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
            photo_file_id=data.get('photo_file_id'),
            source_platform="tg-bot",
        )
        session.add(cargo)
        await session.commit()
        await session.refresh(cargo)
        cargo_id = cargo.id

    await state.clear()

    # Если есть фото — шлём на модерацию в админ-чат
    if cargo.photo_file_id:
        try:
            from src.bot.bot import bot as _bot
            from src.core.config import settings as _s
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton as _IKB
            admin_chat = _s.admin_chat_id or _s.admin_id
            if admin_chat:
                mod_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        _IKB(text="✅ Одобрить", callback_data=f"photo_approve_{cargo_id}"),
                        _IKB(text="🚫 Удалить", callback_data=f"photo_reject_{cargo_id}"),
                    ]
                ])
                await _bot.send_photo(
                    admin_chat,
                    cargo.photo_file_id,
                    caption=f"📸 Фото к грузу #{cargo_id}\n"
                            f"👤 user_id={cargo.owner_id}\n"
                            f"📍 {cargo.from_city} → {cargo.to_city}\n"
                            f"📦 {cargo.cargo_type} {cargo.weight}т",
                    reply_markup=mod_kb,
                )
        except Exception as _e:
            logger.warning("photo moderation send failed: %s", _e)

    await cb.message.edit_text(
        f"✅ Груз #{cargo_id} опубликован!\n\n"
        + ("📸 Фото отправлено на проверку — появится после одобрения.\n\n" if cargo.photo_file_id else "")
        + "Хочешь сразу подобрать под него машину?",
        reply_markup=_cargo_post_publish_kb(cargo_id).as_markup(),
    )

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
        await _publish_cargo_sync_event(cargo, event_type="cargo.created")
    except Exception as e:
        logger.warning("Cross-sync publish failed for cargo #%s: %s", cargo_id, e)

    await cb.answer()
    logger.info("Cargo %s created by %s", cargo_id, cb.from_user.id)

@router.callback_query(CargoForm.confirm, F.data == "no")
async def cargo_confirm_no(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Отменено", reply_markup=main_menu())
    await cb.answer()


@router.callback_query(F.data.startswith("cargo_find_truck_"))
async def cargo_find_truck(cb: CallbackQuery):
    cargo_id = int(cb.data.split("_")[-1])

    async with async_session() as session:
        cargo = await session.scalar(select(Cargo).where(Cargo.id == cargo_id))
        if not cargo:
            await cb.answer("Груз не найден", show_alert=True)
            return
        if cargo.owner_id != cb.from_user.id:
            await cb.answer("Нет доступа к этому грузу", show_alert=True)
            return

        from src.core.matching import match_trucks

        trucks = await match_trucks(
            session,
            from_city=cargo.from_city,
            to_city=cargo.to_city,
            truck_type=None,
            capacity_tons=float(cargo.weight or 0),
            top_n=3,
        )

    if not trucks:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🧾 Мои грузы", callback_data="my_cargos"))
        builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"))
        await cb.message.edit_text(
            f"😔 Пока не нашёл подходящих машин под груз #{cargo_id}.\n\n"
            "Груз уже опубликован. Проверь позже или попробуй другой запрос на подбор.",
            reply_markup=builder.as_markup(),
        )
        await cb.answer()
        return

    from src.bot.handlers.trucks import (
        _format_truck,
        _get_unlocked_truck_ids,
        _is_premium_active,
        _premium_teaser_text,
        _results_keyboard,
    )

    user_id = cb.from_user.id if cb.from_user else None
    is_premium = await _is_premium_active(user_id)
    unlocked_ids = await _get_unlocked_truck_ids(user_id, [truck.id for truck in trucks])

    if not is_premium:
        text = _premium_teaser_text(
            trucks=trucks,
            from_city=cargo.from_city,
            to_city=cargo.to_city,
            weight=float(cargo.weight or 0),
            truck_type=None,
            unlocked_ids=unlocked_ids,
        )
        await cb.message.edit_text(
            text,
            reply_markup=_results_keyboard(trucks, is_premium=False, unlocked_ids=unlocked_ids),
        )
        await cb.answer()
        return

    header = (
        f"🎯 <b>Подобрал {len(trucks)} машин</b> под груз #{cargo_id}\n"
        f"📍 {cargo.from_city} → {cargo.to_city} • {cargo.weight}т\n"
    )
    blocks = [header]
    for index, truck in enumerate(trucks, 1):
        blocks.append(_format_truck(truck, index))
    await cb.message.edit_text(
        "\n\n".join(blocks),
        reply_markup=_results_keyboard(trucks, is_premium=True),
        disable_web_page_preview=True,
    )
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
        
        if cargo:
            link = cargo_deeplink(cargo_id)
            try:
                await bot.send_message(
                    cargo.owner_id,
                    f"📞 Новый отклик на груз #{cargo_id}!\n{link}"
                )
            except:
                pass
    
    await cb.answer("✅ Отклик отправлен!", show_alert=True)
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
            if cb.from_user.id == settings.admin_id:
                text += "⚠️ Компания не зарегистрирована\n"

        stars_old = "⭐" * round(rating_avg) if rating_avg else "нет оценок"
        text += f"⭐ Оценки: {stars_old} ({rating_count})\n"
        if cb.from_user.id == settings.admin_id:
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
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton as _IKB
                rate_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [_IKB(text="⭐ Оценить заказчика", callback_data=f"rate_inline_{cargo_id}")],
                    [_IKB(text="Позже", callback_data="menu")],
                ])
                await bot.send_message(
                    cargo.carrier_id,
                    f"✅ Груз #{cargo_id} завершён! Оцени заказчика:",
                    reply_markup=rate_kb,
                )
            except:
                pass

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton as _IKB
    rate_kb_owner = InlineKeyboardMarkup(inline_keyboard=[
        [_IKB(text="⭐ Оценить перевозчика", callback_data=f"rate_inline_{cargo_id}")],
        [_IKB(text="Позже", callback_data="menu")],
    ])
    await cb.message.edit_text(
        f"✅ Груз #{cargo_id} завершён!",
        reply_markup=rate_kb_owner,
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

        text, is_owner, owner_company_id = await render_cargo_card(
            session, cargo, cb.from_user.id
        )

    try:
        await cb.message.edit_text(
            text,
            reply_markup=cargo_actions(
                cargo.id, is_owner, cargo.status, owner_company_id
            ),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            text,
            reply_markup=cargo_actions(
                cargo.id, is_owner, cargo.status, owner_company_id
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
        result = await session.execute(select(Cargo).where(Cargo.id == cargo_id))
        cargo = result.scalar_one_or_none()
        
        if not cargo:
            await cb.answer("❌ Груз не найден", show_alert=True)
            return

        is_owner = cargo.owner_id == cb.from_user.id
        is_carrier = cargo.carrier_id == cb.from_user.id if cargo.carrier_id else False
        if not (is_owner or is_carrier):
            await cb.answer("❌ Нет доступа", show_alert=True)
            return

        if cargo.status not in {CargoStatus.IN_PROGRESS, CargoStatus.COMPLETED}:
            await cb.answer("🔒 Документы доступны после выбора перевозчика", show_alert=True)
            return
        
        owner = await session.execute(select(User).where(User.id == cargo.owner_id))
        owner = owner.scalar_one_or_none()
        
        carrier = None
        if cargo.carrier_id:
            carrier_result = await session.execute(select(User).where(User.id == cargo.carrier_id))
            carrier = carrier_result.scalar_one_or_none()
    
    pdf_bytes = generate_ttn(cargo, owner, carrier)
    
    await cb.message.answer_document(
        BufferedInputFile(pdf_bytes, filename=f"TTN_{cargo_id}.pdf"),
        caption=f"📄 ТТН для груза #{cargo_id}"
    )
    await cb.answer()
    logger.info(f"TTN generated for cargo {cargo_id}")
