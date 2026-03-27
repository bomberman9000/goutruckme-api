from __future__ import annotations

from datetime import datetime
import json

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from src.bot.keyboards import webapp_entry_kb, cancel_kb
from src.core.config import settings
from src.core.database import async_session
from src.core.logger import logger
from src.core.matching import TruckMatch, match_trucks
from src.core.models import TruckContactUnlock, User
from src.core.redis import get_redis
from src.core.truck_search import (
    extract_truck_search_params,
    looks_like_truck_offer_text,
    looks_like_truck_search_text,
    parse_truck_type,
)

router = Router()

CANCEL_TEXT = "\n\n❌ Отмена: /cancel"
_CANCEL_WORDS = {"отмена", "cancel", "/cancel"}
_SKIP_WORDS = {"/skip", "skip", "—", "-", "любой", "любой тип"}


class FindTruck(StatesGroup):
    route = State()
    weight = State()
    truck_type = State()


def _parse_route(text: str) -> tuple[str | None, str | None]:
    import re

    text = text.strip()
    parts = re.split(r"\s*[-–—→>]+\s*|\s{2,}|\s+до\s+|\s+в\s+", text, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip().title(), parts[1].strip().title()
    raw = text.split()
    if len(raw) == 2:
        return raw[0].title(), raw[1].title()
    return text.title(), None


def _parse_weight(text: str) -> float | None:
    import re

    match = re.search(r"(\d+(?:[.,]\d+)?)", text.replace(",", "."))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _is_cancel_text(text: str | None) -> bool:
    return (text or "").strip().lower() in _CANCEL_WORDS


def _truck_to_cargo_prefill_key(user_id: int) -> str:
    return f"truck:to_cargo:{user_id}"


async def _store_truck_to_cargo_prefill(
    *,
    user_id: int | None,
    from_city: str | None,
    to_city: str | None,
    weight: float | None,
) -> None:
    if not user_id:
        return
    payload = {
        "from_city": from_city,
        "to_city": to_city,
        "weight": weight,
    }
    redis = await get_redis()
    await redis.set(
        _truck_to_cargo_prefill_key(int(user_id)),
        json.dumps(payload, ensure_ascii=False),
        ex=3600,
    )


async def _load_truck_to_cargo_prefill(user_id: int | None) -> dict | None:
    if not user_id:
        return None
    redis = await get_redis()
    raw = await redis.get(_truck_to_cargo_prefill_key(int(user_id)))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _looks_like_truck_free_text_candidate(text: str | None) -> bool:
    raw = (text or "").strip()
    if not raw or raw.startswith("/") or _is_cancel_text(raw):
        return False
    return looks_like_truck_offer_text(raw) or looks_like_truck_search_text(raw)


async def _is_premium_active(user_id: int | None) -> bool:
    if not user_id:
        return False
    async with async_session() as session:
        user = await session.get(User, int(user_id))
    if not user or not user.is_premium:
        return False
    if user.premium_until is None:
        return True
    return user.premium_until >= datetime.now()


async def _get_unlocked_truck_ids(user_id: int | None, truck_ids: list[int]) -> set[int]:
    if not user_id or not truck_ids:
        return set()
    async with async_session() as session:
        rows = (
            await session.execute(
                select(TruckContactUnlock.truck_id).where(
                    TruckContactUnlock.user_id == int(user_id),
                    TruckContactUnlock.truck_id.in_(truck_ids),
                    TruckContactUnlock.status == "success",
                )
            )
        ).scalars().all()
    return {int(row) for row in rows}


def _truck_emoji(truck_type: str | None) -> str:
    mapping = {
        "газель": "🚐",
        "тент": "🚛",
        "рефрижератор": "❄️",
        "борт": "🏗",
        "трал": "🔩",
        "манипулятор": "🏗",
        "самосвал": "🚜",
        "изотерм": "📦",
        "контейнер": "📦",
    }
    return mapping.get(truck_type or "", "🚛")


def _format_truck(truck: TruckMatch, idx: int) -> str:
    emoji = _truck_emoji(truck.truck_type)
    parts = [f"{emoji} <b>#{idx}</b>"]

    type_cap = []
    if truck.truck_type:
        type_cap.append(truck.truck_type.title())
    if truck.capacity_tons:
        type_cap.append(f"{truck.capacity_tons}т")
    if type_cap:
        parts[0] += f" {' '.join(type_cap)}"

    location = truck.base_city or truck.base_region or "—"
    parts.append(f"📍 {location}")

    if truck.routes:
        parts.append(f"🗺 {truck.routes[:80]}")
    if truck.phone:
        parts.append(f"📞 {truck.phone}")
    if truck.avito_url:
        parts.append(f'<a href="{truck.avito_url}">Открыть источник</a>')

    return "\n".join(parts)


def _results_keyboard(
    trucks: list[TruckMatch],
    *,
    is_premium: bool,
    unlocked_ids: set[int] | None = None,
) -> InlineKeyboardMarkup:
    unlocked_ids = unlocked_ids or set()
    builder = InlineKeyboardBuilder()

    if not is_premium:
        for idx, truck in enumerate(trucks, 1):
            if truck.id in unlocked_ids:
                if truck.phone:
                    phone_clean = "".join(c for c in truck.phone if c.isdigit() or c == "+")
                    builder.row(InlineKeyboardButton(text=f"📞 Контакт #{idx}", url=f"tel:{phone_clean}"))
                if truck.avito_url:
                    builder.row(InlineKeyboardButton(text=f"📍 Источник #{idx}", url=truck.avito_url))
                continue
            if truck.phone or truck.avito_url:
                builder.row(
                    InlineKeyboardButton(
                        text=f"🔓 Открыть #{idx} — {settings.truck_contact_unlock_stars} XTR",
                        callback_data=f"unlock_truck:{truck.id}",
                    )
                )
        builder.row(
            InlineKeyboardButton(text="⭐ Premium 7 дней", callback_data="buy_premium:7"),
            InlineKeyboardButton(text="💎 Premium 30 дней", callback_data="buy_premium:30"),
        )
        builder.row(InlineKeyboardButton(text="📱 Открыть Mini App", callback_data="menu"))
        builder.row(InlineKeyboardButton(text="🔄 Искать снова", callback_data="find_truck"))
        return builder.as_markup()

    for idx, truck in enumerate(trucks, 1):
        if truck.phone:
            phone_clean = "".join(c for c in truck.phone if c.isdigit() or c == "+")
            builder.row(InlineKeyboardButton(text=f"📞 Позвонить #{idx}", url=f"tel:{phone_clean}"))
        if truck.avito_url:
            builder.row(InlineKeyboardButton(text=f"🔗 Открыть источник #{idx}", url=truck.avito_url))
    builder.row(InlineKeyboardButton(text="🔄 Искать снова", callback_data="find_truck"))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def _premium_teaser_text(
    *,
    trucks: list[TruckMatch],
    from_city: str | None,
    to_city: str | None,
    weight: float | None,
    truck_type: str | None,
    unlocked_ids: set[int] | None = None,
) -> str:
    unlocked_ids = unlocked_ids or set()
    lines = [f"🎯 <b>Подобрано {len(trucks)} машин</b> по запросу {from_city or '?'} → {to_city or '?'}"]
    meta: list[str] = []
    if weight:
        meta.append(f"{weight}т")
    if truck_type:
        meta.append(truck_type)
    if meta:
        lines[0] += f" ({', '.join(meta)})"
    lines.append("")
    for idx, truck in enumerate(trucks, 1):
        parts = [f"#{idx}"]
        if truck.truck_type:
            parts.append(truck.truck_type)
        if truck.capacity_tons:
            parts.append(f"{truck.capacity_tons}т")
        if truck.base_city:
            parts.append(truck.base_city)
        if truck.id in unlocked_ids:
            parts.append("контакт открыт")
        lines.append(" • ".join(parts))
    lines.append("")
    if unlocked_ids:
        lines.append("Для уже открытых позиций кнопки контакта доступны ниже.")
    lines.append("Контакты и подробности открываются по разовому доступу или по Premium.")
    lines.append(
        f"Разовый доступ: {settings.truck_contact_unlock_stars} XTR за одну машину. "
        "Premium выгоднее, если ищете часто."
    )
    return "\n".join(lines)


async def _reply_truck_offer_hint(message: Message, text: str) -> None:
    from src.parser_bot.truck_extractor import parse_truck_regex

    parsed = parse_truck_regex(text)
    parts = ["🚚 Похоже, вы предлагаете свободную машину."]
    preview: list[str] = []
    if parsed.truck_type:
        preview.append(parsed.truck_type)
    if parsed.capacity_tons:
        preview.append(f"{parsed.capacity_tons}т")
    if parsed.base_city:
        preview.append(parsed.base_city)
    if preview:
        parts.append("Распознал: " + " • ".join(preview))
    parts.append("Сейчас публикация своей машины идет через Mini App → раздел «Мой парк».")
    await message.answer("\n\n".join(parts), reply_markup=webapp_entry_kb())


async def _run_match_and_reply(
    *,
    message: Message,
    from_city: str | None,
    to_city: str | None,
    weight: float | None,
    truck_type: str | None,
) -> None:
    await message.answer("⏳ Подбираю машины...")
    user_id = message.from_user.id if message.from_user else None

    async with async_session() as session:
        trucks = await match_trucks(
            session,
            from_city=from_city,
            to_city=to_city,
            truck_type=truck_type,
            capacity_tons=weight,
            top_n=3,
        )

    if not trucks:
        await _store_truck_to_cargo_prefill(
            user_id=user_id,
            from_city=from_city,
            to_city=to_city,
            weight=weight,
        )
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔄 Искать другую машину", callback_data="find_truck"))
        builder.row(InlineKeyboardButton(text="📦 Это мой груз — разместить", callback_data="truck_to_cargo"))
        builder.row(InlineKeyboardButton(text="📱 Открыть Mini App", callback_data="menu"))
        await message.answer(
            "😔 Машин по вашему запросу не нашлось.\n\n"
            "Это был именно поиск машины.\n"
            "Попробуйте другой маршрут, тоннаж или тип кузова.\n\n"
            "Если вы заказчик и у вас уже есть груз, можно сразу опубликовать его как заявку — "
            "маршрут и тоннаж я уже перенесу в форму.",
            reply_markup=builder.as_markup(),
        )
        return

    is_premium = await _is_premium_active(user_id)
    unlocked_ids = await _get_unlocked_truck_ids(user_id, [truck.id for truck in trucks])

    if not is_premium:
        await message.answer(
            _premium_teaser_text(
                trucks=trucks,
                from_city=from_city,
                to_city=to_city,
                weight=weight,
                truck_type=truck_type,
                unlocked_ids=unlocked_ids,
            ),
            reply_markup=_results_keyboard(trucks, is_premium=False, unlocked_ids=unlocked_ids),
        )
        return

    header = (
        f"🎯 <b>Нашёл {len(trucks)} машин{'у' if len(trucks) == 1 else 'и'}</b> "
        f"по маршруту {from_city or '?'} → {to_city or '?'}"
        f"{f', {weight}т' if weight else ''}"
        f"{f', {truck_type}' if truck_type else ''}\n"
    )
    blocks = [header]
    for index, truck in enumerate(trucks, 1):
        blocks.append(_format_truck(truck, index))
    await message.answer(
        "\n\n".join(blocks),
        reply_markup=_results_keyboard(trucks, is_premium=True),
        disable_web_page_preview=True,
    )


@router.message(Command("findtruck"))
@router.callback_query(F.data == "find_truck")
async def start_find_truck(event: Message | CallbackQuery, state: FSMContext):
    await state.set_state(FindTruck.route)
    text = (
        "🔍 <b>Поиск машины</b>\n\n"
        "Шаг 1 из 3 — <b>Маршрут</b>\n\n"
        "Введи маршрут, например:\n"
        "<code>Москва → Питер</code>\n"
        "<code>Казань Самара</code>\n"
        "<code>Екб - Тюмень</code>"
    )
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.answer(text, reply_markup=cancel_kb())
    else:
        await event.answer(text, reply_markup=cancel_kb())


@router.message(FindTruck.route)
async def got_route(message: Message, state: FSMContext):
    if _is_cancel_text(message.text):
        await state.clear()
        from src.bot.keyboards import main_menu as _mm
        await message.answer("Отменено.", reply_markup=_mm())
        return

    from_city, to_city = _parse_route(message.text or "")
    if not from_city:
        await message.answer(
            "Не понял маршрут. Попробуй: <code>Москва - Тверь</code>\n"
            "Или напиши <code>отмена</code>." + CANCEL_TEXT
        )
        return

    await state.update_data(from_city=from_city, to_city=to_city)
    await state.set_state(FindTruck.weight)
    from aiogram.utils.keyboard import InlineKeyboardBuilder as _IKB2
    from aiogram.types import InlineKeyboardButton as _IKB3
    _wb = _IKB2()
    _wb.row(_IKB3(text="⏭ Пропустить", callback_data="skip_weight"))
    _wb.row(_IKB3(text="◀️ Отмена / Главное меню", callback_data="menu"))
    await message.answer(
        f"📍 Маршрут: <b>{from_city} → {to_city or '?'}</b>\n\n"
        "Шаг 2 из 3 — <b>Вес груза (тонны)</b>\n\n"
        "Сколько тонн нужно перевезти?",
        reply_markup=_wb.as_markup(),
    )


@router.message(FindTruck.weight)
async def got_weight(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        from src.bot.keyboards import main_menu as _mm2
        await state.clear()
        await message.answer("Отменено.", reply_markup=_mm2())
        return

    weight: float | None = None
    if text.lower() not in _SKIP_WORDS:
        weight = _parse_weight(text)
        if weight is None:
            await message.answer(
                "Введи число тонн, например <code>10</code>, или /skip.\n"
                "Для выхода напиши <code>отмена</code>." + CANCEL_TEXT
            )
            return

    await state.update_data(weight=weight)
    await state.set_state(FindTruck.truck_type)
    await message.answer(
        "Тип кузова? Например: <code>тент</code>, <code>реф</code>, <code>газель</code>, <code>борт</code>\n"
        "Или /skip для любого" + CANCEL_TEXT
    )


@router.message(Command("skip"))
async def skip_field(message: Message, state: FSMContext):
    current = await state.get_state()
    if current == FindTruck.weight.state:
        await state.update_data(weight=None)
        await state.set_state(FindTruck.truck_type)
        from aiogram.utils.keyboard import InlineKeyboardBuilder as _IKB6
        from aiogram.types import InlineKeyboardButton as _IKB7
        _tb2 = _IKB6()
        _tb2.row(_IKB7(text="⏭ Любой тип", callback_data="skip_truck_type"))
        _tb2.row(_IKB7(text="◀️ Отмена / Главное меню", callback_data="menu"))
        await message.answer(
            "Шаг 3 из 3 — <b>Тип машины</b>\n\n"
            "Какой тип кузова?\n\n"
            "<i>тент / реф / газель / борт / контейнер / манипулятор</i>",
            reply_markup=_tb2.as_markup(),
        )
    elif current == FindTruck.truck_type.state:
        await _do_search(message, state)
    else:
        await message.answer("Нечего пропускать.")


@router.message(FindTruck.truck_type)
async def got_truck_type(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if _is_cancel_text(text):
        from src.bot.keyboards import main_menu as _mm3
        await state.clear()
        await message.answer("Отменено.", reply_markup=_mm3())
        return

    truck_type: str | None = None
    if text.lower() not in _SKIP_WORDS:
        truck_type = parse_truck_type(text)

    await state.update_data(truck_type=truck_type)
    await _do_search(message, state)


async def _do_search(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    from_city: str | None = data.get("from_city")
    to_city: str | None = data.get("to_city")
    weight: float | None = data.get("weight")
    truck_type: str | None = data.get("truck_type")

    await _run_match_and_reply(
        message=message,
        from_city=from_city,
        to_city=to_city,
        weight=weight,
        truck_type=truck_type,
    )

    logger.info(
        "truck match user=%s route=%s->%s weight=%s type=%s found=done",
        message.from_user.id if message.from_user else "?",
        from_city,
        to_city,
        weight,
        truck_type,
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    if current and current.startswith("FindTruck"):
        await state.clear()
        await message.answer("Отменено.")


@router.message(StateFilter(FindTruck), F.text.func(lambda text: (text or "").strip().lower() in _CANCEL_WORDS))
async def cancel_by_text(message: Message, state: FSMContext):
    from src.bot.keyboards import main_menu as _main_menu
    await state.clear()
    await message.answer("Отменено.", reply_markup=_main_menu())


@router.callback_query(F.data == "truck_to_cargo")
async def truck_to_cargo(cb: CallbackQuery, state: FSMContext):
    draft = await _load_truck_to_cargo_prefill(cb.from_user.id if cb.from_user else None)
    if not draft:
        await cb.answer("Черновик не найден", show_alert=True)
        return

    from src.bot.handlers.cargo import _continue_cargo_prefill

    await state.clear()
    await state.update_data(
        from_city=draft.get("from_city"),
        to_city=draft.get("to_city"),
        cargo_type=None,
        weight=draft.get("weight"),
        price=None,
        load_date=None,
        load_time=None,
        comment=None,
    )
    try:
        await cb.message.edit_text(
            "📦 <b>Заполняем груз</b>\n\n"
            "Вы выбрали публикацию груза после неудачного подбора машины.\n"
            "Я уже подставил маршрут и тоннаж из вашего запроса.\n"
            "Сейчас уточним недостающие поля.",
        )
    except Exception:
        pass
    await _continue_cargo_prefill(cb.message, state)
    await cb.answer()


@router.message(StateFilter(None), F.text.func(_looks_like_truck_free_text_candidate))
async def smart_truck_text(message: Message, state: FSMContext, override_text: str | None = None):
    _ = state
    text = override_text or (message.text or "").strip()
    if not text or text.startswith("/") or _is_cancel_text(text):
        return

    try:
        logger.info("truck.free_text candidate=%r", text)

        if looks_like_truck_offer_text(text):
            logger.info("truck.free_text classified=offer text=%r", text)
            await _reply_truck_offer_hint(message, text)
            return

        params = await extract_truck_search_params(text)
        logger.info("truck.free_text params=%s text=%r", params, text)
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
    except Exception as exc:
        logger.exception("truck.free_text failed text=%r error=%s", text, exc)
        await message.answer("Не удалось выполнить подбор. Попробуйте еще раз.")
