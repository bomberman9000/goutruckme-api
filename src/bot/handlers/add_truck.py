"""FSM регистрации/просмотра/управления машинами водителя."""
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from src.bot.states import AddTruck
from src.bot.keyboards import (
    body_type_kb,
    confirm_kb,
    skip_kb,
    trucks_menu,
    truck_list_kb,
    truck_detail_kb,
    main_menu,
)
from src.core.database import async_session
from src.core.models import UserVehicle
from src.core.services.gruzpotok_bridge import sync_vehicle_to_site

router = Router()

CANCEL_HINT = "\n\n❌ Отмена: /cancel"


# ──────────────────────────────────────────────────────────────
# МЕНЮ МАШИН
# ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "my_trucks")
async def show_trucks_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    async with async_session() as session:
        rows = (
            await session.execute(
                select(UserVehicle)
                .where(UserVehicle.user_id == cb.from_user.id)
                .order_by(UserVehicle.created_at.desc())
            )
        ).scalars().all()

    if not rows:
        await cb.message.edit_text(
            "🚛 <b>Мои машины</b>\n\nУ вас ещё нет зарегистрированных машин.",
            reply_markup=trucks_menu(),
        )
    else:
        await cb.message.edit_text(
            f"🚛 <b>Мои машины</b> ({len(rows)} шт.)\n\nВыберите машину для управления:",
            reply_markup=truck_list_kb(rows),
        )


# ──────────────────────────────────────────────────────────────
# ДЕТАЛИ МАШИНЫ
# ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("truck_view_"))
async def truck_detail(cb: CallbackQuery):
    vehicle_id = int(cb.data.removeprefix("truck_view_"))
    await cb.answer()
    async with async_session() as session:
        v = await session.get(UserVehicle, vehicle_id)

    if not v or v.user_id != cb.from_user.id:
        await cb.message.answer("❌ Машина не найдена.")
        return

    status = "🟢 На линии" if v.is_available else "🔴 Не доступна"
    text = (
        f"🚛 <b>{v.body_type}</b>\n\n"
        f"Грузоподъёмность: {v.capacity_tons} т\n"
        f"Город: {v.location_city or '—'}\n"
        f"Номер: {v.plate_number or '—'}\n"
        f"Статус: {status}"
    )
    await cb.message.edit_text(text, reply_markup=truck_detail_kb(v.id, v.is_available))


@router.callback_query(F.data.startswith("truck_on_"))
async def truck_set_available(cb: CallbackQuery):
    vehicle_id = int(cb.data.removeprefix("truck_on_"))
    await cb.answer()
    async with async_session() as session:
        v = await session.get(UserVehicle, vehicle_id)
        if not v or v.user_id != cb.from_user.id:
            await cb.message.answer("❌ Машина не найдена.")
            return
        v.is_available = True
        await session.commit()
    await cb.message.edit_text(
        f"🟢 <b>{v.body_type} {v.capacity_tons}т</b> — выставлена на линию.",
        reply_markup=truck_detail_kb(vehicle_id, True),
    )


@router.callback_query(F.data.startswith("truck_off_"))
async def truck_set_unavailable(cb: CallbackQuery):
    vehicle_id = int(cb.data.removeprefix("truck_off_"))
    await cb.answer()
    async with async_session() as session:
        v = await session.get(UserVehicle, vehicle_id)
        if not v or v.user_id != cb.from_user.id:
            await cb.message.answer("❌ Машина не найдена.")
            return
        v.is_available = False
        await session.commit()
    await cb.message.edit_text(
        f"🔴 <b>{v.body_type} {v.capacity_tons}т</b> — снята с линии.",
        reply_markup=truck_detail_kb(vehicle_id, False),
    )


@router.callback_query(F.data.startswith("truck_del_"))
async def truck_delete(cb: CallbackQuery):
    vehicle_id = int(cb.data.removeprefix("truck_del_"))
    await cb.answer()
    async with async_session() as session:
        v = await session.get(UserVehicle, vehicle_id)
        if not v or v.user_id != cb.from_user.id:
            await cb.message.answer("❌ Машина не найдена.")
            return
        label = f"{v.body_type} {v.capacity_tons}т"
        await session.delete(v)
        await session.commit()
    await cb.message.edit_text(
        f"🗑 Машина <b>{label}</b> удалена.",
        reply_markup=trucks_menu(),
    )


# ──────────────────────────────────────────────────────────────
# FSM ДОБАВЛЕНИЯ МАШИНЫ
# ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "add_truck")
async def start_add_truck(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await cb.message.edit_text(
        "🚛 <b>Добавление машины</b>\n\nВыберите тип кузова:" + CANCEL_HINT,
        reply_markup=body_type_kb(),
    )
    await state.set_state(AddTruck.body_type)


@router.callback_query(AddTruck.body_type, F.data.startswith("body_"))
async def add_truck_body(cb: CallbackQuery, state: FSMContext):
    body = cb.data.removeprefix("body_")
    await state.update_data(body_type=body)
    await cb.answer()
    await cb.message.edit_text(
        f"✅ Кузов: <b>{body}</b>\n\n"
        "Введите грузоподъёмность в тоннах (например: 20):" + CANCEL_HINT,
    )
    await state.set_state(AddTruck.capacity_tons)


@router.message(AddTruck.capacity_tons)
async def add_truck_capacity(message: Message, state: FSMContext):
    text = (message.text or "").strip().replace(",", ".")
    try:
        tons = float(text)
        if not (0.1 <= tons <= 200):
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите число от 0.1 до 200 (например: 20)")
        return

    await state.update_data(capacity_tons=tons)
    await message.answer(
        f"✅ Грузоподъёмность: <b>{tons} т</b>\n\n"
        "Укажите город текущего местонахождения машины:" + CANCEL_HINT,
    )
    await state.set_state(AddTruck.location_city)


@router.message(AddTruck.location_city)
async def add_truck_city(message: Message, state: FSMContext):
    city = (message.text or "").strip()
    if len(city) < 2:
        await message.answer("❌ Введите название города (минимум 2 символа)")
        return

    await state.update_data(location_city=city)
    await message.answer(
        f"✅ Город: <b>{city}</b>\n\n"
        "Введите объём кузова в м³ (например: 82 для фуры):" + CANCEL_HINT,
        reply_markup=skip_kb(),
    )
    await state.set_state(AddTruck.volume_m3)


@router.callback_query(AddTruck.volume_m3, F.data == "skip")
async def add_truck_volume_skip(cb: CallbackQuery, state: FSMContext):
    await state.update_data(volume_m3=None)
    await cb.answer()
    await cb.message.answer(
        "Введите гос. номер машины (необязательно):" + CANCEL_HINT,
        reply_markup=skip_kb(),
    )
    await state.set_state(AddTruck.plate_number)


@router.message(AddTruck.volume_m3)
async def add_truck_volume(message: Message, state: FSMContext):
    text = (message.text or "").strip().replace(",", ".")
    try:
        vol = float(text)
        if not (1 <= vol <= 500):
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите число от 1 до 500 (например: 82)")
        return
    await state.update_data(volume_m3=vol)
    await message.answer(
        f"✅ Объём: <b>{vol} м³</b>\n\n"
        "Введите гос. номер машины (необязательно):" + CANCEL_HINT,
        reply_markup=skip_kb(),
    )
    await state.set_state(AddTruck.plate_number)


@router.callback_query(AddTruck.plate_number, F.data == "skip")
async def add_truck_plate_skip(cb: CallbackQuery, state: FSMContext):
    await state.update_data(plate_number=None)
    await cb.answer()
    await _show_truck_confirm(cb.message, state, edit=True)
    await state.set_state(AddTruck.confirm)


@router.message(AddTruck.plate_number)
async def add_truck_plate(message: Message, state: FSMContext):
    plate = (message.text or "").strip().upper()
    if plate.lower() in {"нет", "skip", "-", "пропустить"}:
        plate = None
    await state.update_data(plate_number=plate)
    await _show_truck_confirm(message, state, edit=False)
    await state.set_state(AddTruck.confirm)


async def _show_truck_confirm(obj, state: FSMContext, edit: bool = False):
    data = await state.get_data()
    text = (
        "📋 <b>Проверьте данные машины:</b>\n\n"
        f"Кузов: <b>{data['body_type']}</b>\n"
        f"Грузоподъёмность: <b>{data['capacity_tons']} т</b>\n"
        f"Объём: <b>{data.get('volume_m3') or '—'} м³</b>\n"
        f"Город: <b>{data['location_city']}</b>\n"
        f"Номер: <b>{data.get('plate_number') or '—'}</b>\n\n"
        "Всё верно?"
    )
    if edit:
        await obj.edit_text(text, reply_markup=confirm_kb())
    else:
        await obj.answer(text, reply_markup=confirm_kb())


@router.callback_query(AddTruck.confirm, F.data == "yes")
async def add_truck_save(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await cb.answer()

    async with async_session() as session:
        vehicle = UserVehicle(
            user_id=cb.from_user.id,
            body_type=data["body_type"],
            capacity_tons=data["capacity_tons"],
            volume_m3=data.get("volume_m3"),
            location_city=data.get("location_city"),
            plate_number=data.get("plate_number"),
            is_available=True,
        )
        session.add(vehicle)
        await session.commit()
        await session.refresh(vehicle)

    # Синхронизируем с gruzpotok-api
    try:
        await sync_vehicle_to_site(vehicle, user_id=cb.from_user.id)
    except Exception:
        pass

    await state.clear()
    await cb.message.edit_text(
        f"✅ <b>Машина добавлена!</b>\n\n"
        f"{vehicle.body_type} {vehicle.capacity_tons}т — {vehicle.location_city}\n"
        "Статус: 🟢 На линии\n\n"
        "Ваша машина видна перевозчикам в поиске.",
        reply_markup=trucks_menu(),
    )


@router.callback_query(AddTruck.confirm, F.data == "no")
async def add_truck_cancel_confirm(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await cb.message.edit_text("Добавление отменено.", reply_markup=main_menu())
