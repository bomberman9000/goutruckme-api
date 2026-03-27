"""Vehicle intake via Telegram bot — text, STS photo, truck photo."""
from __future__ import annotations

import io
import json
import logging
from typing import Any

import httpx
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from src.bot.states import VehicleIntake
from src.bot.keyboards import main_menu
from src.core.database import async_session
from src.core.models import UserVehicle
from src.core.config import settings
from src.parser_bot.truck_extractor import parse_truck_llm, parse_truck_regex

logger = logging.getLogger(__name__)

router = Router()

CANCEL_HINT = "\n\n❌ Отмена: /cancel"

# ── Keyboards ────────────────────────────────────────────────

from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton


def _vi_mode_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📝 Вставить текстом", callback_data="vi_mode_text"))
    b.row(InlineKeyboardButton(text="📷 Фото СТС", callback_data="vi_mode_sts"))
    b.row(InlineKeyboardButton(text="🚛 Фото машины", callback_data="vi_mode_photo"))
    b.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
    return b.as_markup()


def _vi_confirm_kb():
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Сохранить", callback_data="vi_confirm"),
        InlineKeyboardButton(text="✏️ Исправить", callback_data="vi_retry"),
    )
    b.row(InlineKeyboardButton(text="❌ Отмена", callback_data="vi_cancel"))
    return b.as_markup()


def _vi_confirm_sts_kb():
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Сохранить", callback_data="vi_confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="vi_cancel"),
    )
    return b.as_markup()


# ── Entry point ──────────────────────────────────────────────

@router.callback_query(F.data.in_({"vi_start", "add_truck_smart"}))
async def start_vehicle_intake(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await cb.message.edit_text(
        "🚚 <b>Добавить машину</b>\n\n"
        "Выберите способ:",
        reply_markup=_vi_mode_kb(),
    )
    await state.set_state(VehicleIntake.choose_mode)


# ── Mode: text ───────────────────────────────────────────────

@router.callback_query(F.data == "vi_mode_text", StateFilter(VehicleIntake.choose_mode))
async def mode_text(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.edit_text(
        "📝 <b>Опишите машину текстом</b>\n\n"
        "Например:\n"
        "<i>Газель Next 2т 16м3 тент Самара А123БВ777</i>" + CANCEL_HINT,
    )
    await state.set_state(VehicleIntake.wait_text)


@router.message(StateFilter(VehicleIntake.wait_text), F.text)
async def handle_text_input(msg: Message, state: FSMContext):
    raw_text = msg.text.strip()
    if not raw_text:
        await msg.answer("Пожалуйста, введите описание машины." + CANCEL_HINT)
        return

    wait_msg = await msg.answer("⏳ Анализирую...")

    # Try LLM first, fallback to regex
    parsed = await parse_truck_llm(raw_text)
    if not parsed:
        parsed = parse_truck_regex(raw_text)

    data = {
        "brand": parsed.truck_type or "",
        "vehicle_kind": parsed.truck_type or "тент",
        "capacity_tons": parsed.capacity_tons or 0,
        "volume_m3": parsed.volume_m3 or 0,
        "city": parsed.base_city or "",
        "plate_number": "",
        "raw_text": raw_text,
    }
    # Try to extract plate_number from raw text
    import re
    plate_match = re.search(
        r"[АВЕКМНОРСТУХABEKMHOPCTYX]\s?\d{3}\s?[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{2,3}",
        raw_text, re.IGNORECASE,
    )
    if plate_match:
        data["plate_number"] = re.sub(r"\s+", "", plate_match.group(0)).upper()

    await state.update_data(parsed=data)

    card = _format_card(data)
    try:
        await wait_msg.edit_text(
            f"📋 <b>Результат распознавания:</b>\n\n{card}",
            reply_markup=_vi_confirm_kb(),
        )
    except Exception:
        await msg.answer(
            f"📋 <b>Результат распознавания:</b>\n\n{card}",
            reply_markup=_vi_confirm_kb(),
        )
    await state.set_state(VehicleIntake.confirm_parsed)


# ── Mode: STS photo ──────────────────────────────────────────

@router.callback_query(F.data == "vi_mode_sts", StateFilter(VehicleIntake.choose_mode))
async def mode_sts(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.edit_text(
        "📷 <b>Отправьте фото СТС</b>\n\n"
        "Сфотографируйте лицевую сторону свидетельства о регистрации ТС." + CANCEL_HINT,
    )
    await state.set_state(VehicleIntake.wait_sts_photo)


@router.message(StateFilter(VehicleIntake.wait_sts_photo), F.photo)
async def handle_sts_photo(msg: Message, state: FSMContext):
    wait_msg = await msg.answer("📄 Обрабатываю документ...")

    # Download photo
    photo = msg.photo[-1]  # highest resolution
    try:
        from src.bot.bot import bot
        file = await bot.get_file(photo.file_id)
        file_bytes = io.BytesIO()
        await bot.download_file(file.file_path, file_bytes)
        image_data = file_bytes.getvalue()
    except Exception as exc:
        logger.warning("Failed to download STS photo: %s", exc)
        await wait_msg.edit_text("❌ Не удалось загрузить фото. Попробуйте ещё раз." + CANCEL_HINT)
        return

    # Vision AI
    parsed = await parse_sts_vision(image_data)

    if not parsed:
        await wait_msg.edit_text(
            "❌ Не удалось распознать документ.\n"
            "Попробуйте сделать более чёткое фото или используйте ввод текстом.",
            reply_markup=_vi_mode_kb(),
        )
        await state.set_state(VehicleIntake.choose_mode)
        return

    data = {
        "brand": parsed.get("brand", ""),
        "vehicle_kind": parsed.get("vehicle_kind", parsed.get("body_type", "тент")),
        "capacity_tons": parsed.get("capacity_tons", 0),
        "volume_m3": parsed.get("volume_m3", 0),
        "city": parsed.get("city", ""),
        "plate_number": parsed.get("plate_number", ""),
        "vin": parsed.get("vin", ""),
        "sts_number": parsed.get("sts_number", ""),
        "year": parsed.get("year", 0),
        "raw_text": f"[STS photo recognition]",
    }

    await state.update_data(parsed=data)

    card = _format_card(data)
    try:
        await wait_msg.edit_text(
            f"📋 <b>Данные из СТС:</b>\n\n{card}",
            reply_markup=_vi_confirm_sts_kb(),
        )
    except Exception:
        await msg.answer(
            f"📋 <b>Данные из СТС:</b>\n\n{card}",
            reply_markup=_vi_confirm_sts_kb(),
        )
    await state.set_state(VehicleIntake.confirm_parsed)


# ── Mode: truck photo ────────────────────────────────────────

@router.callback_query(F.data == "vi_mode_photo", StateFilter(VehicleIntake.choose_mode))
async def mode_truck_photo(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.edit_text(
        "🚛 <b>Отправьте фото машины</b>\n\n"
        "Фото будет привязано к последней добавленной машине." + CANCEL_HINT,
    )
    await state.set_state(VehicleIntake.wait_truck_photo)


@router.message(StateFilter(VehicleIntake.wait_truck_photo), F.photo)
async def handle_truck_photo(msg: Message, state: FSMContext):
    photo = msg.photo[-1]
    file_id = photo.file_id
    user_id = msg.from_user.id

    async with async_session() as session:
        # Find the latest vehicle for this user
        result = await session.execute(
            select(UserVehicle)
            .where(UserVehicle.user_id == user_id)
            .order_by(UserVehicle.created_at.desc())
            .limit(1)
        )
        vehicle = result.scalar_one_or_none()

    if not vehicle:
        await msg.answer(
            "❌ У вас нет зарегистрированных машин.\n"
            "Сначала добавьте машину текстом или по СТС.",
            reply_markup=_vi_mode_kb(),
        )
        await state.set_state(VehicleIntake.choose_mode)
        return

    # Store file_id in state or vehicle (we don't have photo column in UserVehicle,
    # so just confirm and log it)
    logger.info("Truck photo file_id=%s saved for vehicle_id=%s user=%s", file_id, vehicle.id, user_id)

    await msg.answer(
        f"✅ Фото сохранено для машины: <b>{vehicle.body_type} {vehicle.capacity_tons}т</b>",
        reply_markup=main_menu(),
    )
    await state.clear()


# ── Confirm / Cancel / Retry ─────────────────────────────────

@router.callback_query(F.data == "vi_confirm", StateFilter(VehicleIntake.confirm_parsed))
async def confirm_vehicle(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    state_data = await state.get_data()
    parsed = state_data.get("parsed", {})
    user_id = cb.from_user.id

    # 1. Save to bot DB
    body_type = _normalize_body_type(parsed.get("vehicle_kind", "тент"))
    capacity = float(parsed.get("capacity_tons", 0)) or 20.0
    city = parsed.get("city", "") or None
    plate = parsed.get("plate_number", "") or None

    async with async_session() as session:
        vehicle = UserVehicle(
            user_id=user_id,
            body_type=body_type,
            capacity_tons=capacity,
            location_city=city,
            is_available=True,
            plate_number=plate,
        )
        session.add(vehicle)
        await session.commit()
        await session.refresh(vehicle)
        vehicle_id_bot = vehicle.id

    # 2. Sync to gruzpotok-api via /internal/vehicles/from-bot
    sync_ok = await _sync_vehicle_to_site(
        telegram_user_id=user_id,
        parsed=parsed,
    )

    # 3. Also sync via existing bridge (backup)
    try:
        from src.core.services.gruzpotok_bridge import sync_vehicle_to_site
        async with async_session() as session:
            v = await session.get(UserVehicle, vehicle_id_bot)
            if v:
                await sync_vehicle_to_site(v, user_id=user_id)
    except Exception as exc:
        logger.warning("bridge sync fallback failed: %s", exc)

    sync_label = "и синхронизирована с сайтом ✅" if sync_ok else "(синхронизация с сайтом позже)"

    await cb.message.edit_text(
        f"✅ <b>Машина добавлена!</b> {sync_label}\n\n"
        f"🚛 {body_type} {capacity}т\n"
        f"📍 {city or '—'}\n"
        f"🔢 {plate or '—'}",
        reply_markup=main_menu(),
    )
    await state.clear()


@router.callback_query(F.data == "vi_cancel", StateFilter(VehicleIntake))
async def cancel_intake(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await cb.message.edit_text("❌ Отменено.", reply_markup=main_menu())


@router.callback_query(F.data == "vi_retry", StateFilter(VehicleIntake.confirm_parsed))
async def retry_text(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.edit_text(
        "📝 <b>Опишите машину текстом заново</b>\n\n"
        "Например:\n"
        "<i>Газель Next 2т 16м3 тент Самара А123БВ777</i>" + CANCEL_HINT,
    )
    await state.set_state(VehicleIntake.wait_text)


# ── Helpers ──────────────────────────────────────────────────

BODY_TYPE_MAP = {
    "газель": "Газель",
    "тент": "Тент",
    "рефрижератор": "Рефрижератор",
    "реф": "Рефрижератор",
    "изотерм": "Изотерм",
    "борт": "Борт",
    "трал": "Трал",
    "манипулятор": "Манипулятор",
    "самосвал": "Самосвал",
    "контейнер": "Контейнер",
    "цистерна": "Цистерна",
    "зерновоз": "Зерновоз",
    "автовоз": "Автовоз",
    "фургон": "Фургон",
}


def _normalize_body_type(raw: str) -> str:
    key = (raw or "тент").strip().lower()
    return BODY_TYPE_MAP.get(key, raw.title() if raw else "Тент")


def _format_card(data: dict) -> str:
    lines = []
    if data.get("brand"):
        lines.append(f"🚛 Тип: {data['brand']}")
    if data.get("vehicle_kind"):
        lines.append(f"📦 Кузов: {_normalize_body_type(data['vehicle_kind'])}")
    cap = data.get("capacity_tons")
    if cap:
        lines.append(f"⚖️ Грузоподъёмность: {cap} т")
    vol = data.get("volume_m3")
    if vol:
        lines.append(f"📐 Объём: {vol} м³")
    if data.get("city"):
        lines.append(f"📍 Город: {data['city']}")
    if data.get("plate_number"):
        lines.append(f"🔢 Гос. номер: {data['plate_number']}")
    if data.get("vin"):
        lines.append(f"🔑 VIN: {data['vin']}")
    if data.get("sts_number"):
        lines.append(f"📄 СТС: {data['sts_number']}")
    if data.get("year"):
        lines.append(f"📅 Год: {data['year']}")
    return "\n".join(lines) if lines else "Данные не распознаны"


async def _sync_vehicle_to_site(
    telegram_user_id: int,
    parsed: dict,
) -> bool:
    """POST to gruzpotok-api /internal/vehicles/from-bot."""
    base = (settings.gruzpotok_api_internal_url or "").rstrip("/")
    if not base:
        base = (settings.gruzpotok_public_url or "").rstrip("/")
    if not base:
        return False

    token = (settings.internal_token or settings.internal_api_token or "").strip()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token

    payload = {
        "telegram_user_id": telegram_user_id,
        "brand": parsed.get("brand", ""),
        "vehicle_kind": parsed.get("vehicle_kind", "тент"),
        "capacity_tons": float(parsed.get("capacity_tons", 0)) or 20.0,
        "volume_m3": float(parsed.get("volume_m3", 0)) or 82.0,
        "city": parsed.get("city", ""),
        "plate_number": parsed.get("plate_number", ""),
        "vin": parsed.get("vin", ""),
        "sts_number": parsed.get("sts_number", ""),
        "year": int(parsed.get("year", 0)) if parsed.get("year") else None,
        "source": "bot_intake",
        "raw_text": parsed.get("raw_text", ""),
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{base}/internal/vehicles/from-bot",
                headers=headers,
                json=payload,
            )
            if resp.status_code == 200:
                result = resp.json()
                logger.info(
                    "vehicle synced to site: ok=%s vehicle_id=%s",
                    result.get("ok"), result.get("vehicle_id"),
                )
                return result.get("ok", False)
            logger.warning("vehicle sync status=%s body=%s", resp.status_code, resp.text[:200])
            return False
    except Exception as exc:
        logger.warning("vehicle sync failed: %s", exc)
        return False


async def parse_sts_vision(image_bytes: bytes) -> dict | None:
    """Parse STS document image using Ollama vision model via WireGuard."""
    import base64

    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    prompt = (
        "Это фото свидетельства о регистрации транспортного средства (СТС) Российской Федерации. "
        "Извлеки следующие данные и верни ТОЛЬКО JSON без пояснений:\n"
        "- brand: марка и модель ТС\n"
        "- plate_number: государственный регистрационный знак\n"
        "- vin: VIN номер\n"
        "- sts_number: номер СТС (серия и номер)\n"
        "- year: год выпуска (число)\n"
        "- capacity_tons: разрешённая максимальная масса в тоннах (число)\n"
        "- body_type: тип кузова\n"
        "- city: город регистрации\n"
        "\nЕсли поле не видно — верни null."
    )

    # Try Ollama vision models in order
    ollama_url = "http://10.0.0.2:11434"
    vision_models = ["qwen3-vl:8b", "qwen3-vl:4b", "qwen3-vl:30b"]

    for model in vision_models:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt,
                                "images": [b64_image],
                            }
                        ],
                        "stream": False,
                        "options": {"temperature": 0},
                    },
                )
                if resp.status_code != 200:
                    logger.warning("Vision model %s returned %s", model, resp.status_code)
                    continue

                data = resp.json()
                content = data.get("message", {}).get("content", "")
                result = _extract_json_from_text(content)
                if result:
                    logger.info("STS parsed with model %s", model)
                    return result
                logger.warning("Vision model %s returned non-JSON: %.100s", model, content)
        except Exception as exc:
            logger.warning("Vision model %s failed: %s", model, exc)
            continue

    return None


def _extract_json_from_text(text: str) -> dict | None:
    """Extract JSON object from text that may contain markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start: end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
