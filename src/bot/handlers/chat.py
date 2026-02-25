from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, or_, and_, desc
from src.bot.states import ChatForm
from src.bot.keyboards import main_menu, chat_kb, back_menu
from src.core.database import async_session
from src.core.models import ChatMessage, Cargo, CargoStatus, User
from src.core.logger import logger
from src.bot.bot import bot

router = Router()

@router.callback_query(F.data == "messages")
async def show_messages(cb: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(
            select(ChatMessage)
            .where(
                or_(
                    ChatMessage.from_user_id == cb.from_user.id,
                    ChatMessage.to_user_id == cb.from_user.id
                )
            )
            .order_by(desc(ChatMessage.created_at))
            .limit(20)
        )
        messages = result.scalars().all()
    
    if not messages:
        try:
            await cb.message.edit_text("💬 Нет сообщений", reply_markup=back_menu())
        except TelegramBadRequest:
            pass
        await cb.answer()
        return
    
    chats = {}
    for m in messages:
        other_id = m.to_user_id if m.from_user_id == cb.from_user.id else m.from_user_id
        if other_id not in chats:
            chats[other_id] = {"cargo_id": m.cargo_id, "last": m, "unread": 0}
        if m.to_user_id == cb.from_user.id and not m.is_read:
            chats[other_id]["unread"] += 1
    
    text = "💬 <b>Сообщения:</b>\n\n"
    for user_id, data in list(chats.items())[:10]:
        async with async_session() as session:
            user_result = await session.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
        
        name = user.full_name if user else f"ID:{user_id}"
        unread = f" 🔴{data['unread']}" if data['unread'] > 0 else ""
        text += f"👤 {name}{unread}\n"
        text += f"   Груз #{data['cargo_id']} — /chat_{data['cargo_id']}_{user_id}\n\n"
    
    try:
        await cb.message.edit_text(text, reply_markup=back_menu())
    except TelegramBadRequest:
        pass
    await cb.answer()

@router.callback_query(F.data.startswith("chat_"))
async def start_chat(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split("_")
    cargo_id = int(parts[1])
    
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

        if cargo.status not in (CargoStatus.IN_PROGRESS, CargoStatus.COMPLETED):
            await cb.answer("🔒 Чат доступен после выбора перевозчика", show_alert=True)
            return
        
        if is_owner:
            to_user_id = cargo.carrier_id
        else:
            to_user_id = cargo.owner_id
        
        if not to_user_id:
            await cb.answer("❌ Некому писать", show_alert=True)
            return
    
    await state.update_data(cargo_id=cargo_id, to_user_id=to_user_id)
    await cb.message.edit_text(f"✏️ Напиши сообщение по грузу #{cargo_id}:")
    await state.set_state(ChatForm.message)
    await cb.answer()

@router.message(F.text.startswith("/chat_"))
async def start_chat_cmd(message: Message, state: FSMContext):
    try:
        parts = message.text.split("_")
        cargo_id = int(parts[1])
        to_user_id = int(parts[2])
    except:
        return

    async with async_session() as session:
        result = await session.execute(select(Cargo).where(Cargo.id == cargo_id))
        cargo = result.scalar_one_or_none()
        if not cargo:
            await message.answer("❌ Груз не найден")
            return

        is_owner = cargo.owner_id == message.from_user.id
        is_carrier = cargo.carrier_id == message.from_user.id if cargo.carrier_id else False
        if not (is_owner or is_carrier):
            await message.answer("❌ Нет доступа")
            return

        if cargo.status not in (CargoStatus.IN_PROGRESS, CargoStatus.COMPLETED):
            await message.answer("🔒 Чат доступен после выбора перевозчика")
            return

        other_id = cargo.carrier_id if is_owner else cargo.owner_id
        if other_id != to_user_id:
            await message.answer("❌ Некорректный чат")
            return

    await state.update_data(cargo_id=cargo_id, to_user_id=to_user_id)

    async with async_session() as session:
        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.cargo_id == cargo_id)
            .where(
                or_(
                    and_(ChatMessage.from_user_id == message.from_user.id, ChatMessage.to_user_id == to_user_id),
                    and_(ChatMessage.from_user_id == to_user_id, ChatMessage.to_user_id == message.from_user.id)
                )
            )
            .order_by(desc(ChatMessage.created_at))
            .limit(10)
        )
        messages = result.scalars().all()
        
        for m in messages:
            if m.to_user_id == message.from_user.id:
                m.is_read = True
        await session.commit()
    
    if messages:
        text = f"💬 <b>Чат по грузу #{cargo_id}:</b>\n\n"
        for m in reversed(messages):
            arrow = "➡️" if m.from_user_id == message.from_user.id else "⬅️"
            text += f"{arrow} {m.message}\n"
        text += "\n✏️ Напиши ответ:"
    else:
        text = f"💬 Чат по грузу #{cargo_id}\n\n✏️ Напиши сообщение:"
    
    await message.answer(text)
    await state.set_state(ChatForm.message)

@router.message(ChatForm.message)
async def send_chat_message(message: Message, state: FSMContext):
    data = await state.get_data()
    cargo_id = data['cargo_id']
    to_user_id = data['to_user_id']
    
    async with async_session() as session:
        chat_msg = ChatMessage(
            cargo_id=cargo_id,
            from_user_id=message.from_user.id,
            to_user_id=to_user_id,
            message=message.text
        )
        session.add(chat_msg)
        await session.commit()
    
    try:
        await bot.send_message(
            to_user_id,
            f"💬 <b>Новое сообщение</b> по грузу #{cargo_id}:\n\n"
            f"{message.text}\n\n"
            f"Ответить: /chat_{cargo_id}_{message.from_user.id}"
        )
    except:
        pass
    
    await state.clear()
    await message.answer("✅ Сообщение отправлено!", reply_markup=main_menu())
    logger.info(f"Chat message from {message.from_user.id} to {to_user_id} for cargo {cargo_id}")

@router.callback_query(F.data.startswith("reply_"))
async def reply_chat(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split("_")
    cargo_id = int(parts[1])
    to_user_id = int(parts[2])
    
    await state.update_data(cargo_id=cargo_id, to_user_id=to_user_id)
    await cb.message.answer("✏️ Напиши ответ:")
    await state.set_state(ChatForm.message)
    await cb.answer()
