from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from src.bot.states import ClaimForm
from src.bot.keyboards import claim_type_kb, company_actions_kb, back_menu, main_menu
from src.core.database import async_session
from src.core.models import Claim, ClaimStatus, CompanyDetails, User
from src.core.logger import logger

router = Router()

CLAIM_TYPES = {
    "payment": "💰 Неоплата / задержка оплаты",
    "damage": "📦 Повреждение груза",
    "delay": "⏰ Срыв сроков",
    "fraud": "🚨 Мошенничество",
    "other": "❓ Другое",
}


@router.callback_query(F.data.startswith("company_profile_"))
async def view_company_profile(cb: CallbackQuery):
    """Просмотр профиля компании с рейтингом"""
    company_id = int(cb.data.split("_")[-1])

    async with async_session() as session:
        company = await session.scalar(
            select(CompanyDetails).where(CompanyDetails.id == company_id)
        )
        if not company:
            await cb.answer("❌ Компания не найдена", show_alert=True)
            return

        await session.scalar(select(User).where(User.id == company.user_id))

        # Считаем претензии
        claims_count = await session.scalar(
            select(func.count()).select_from(Claim).where(Claim.to_company_id == company_id)
        )
        open_claims = await session.scalar(
            select(func.count())
            .select_from(Claim)
            .where(Claim.to_company_id == company_id, Claim.status == ClaimStatus.OPEN)
        )

    # Формируем рейтинг
    rating = company.total_rating
    stars = "⭐" * rating + "☆" * (10 - rating)

    text = f"🏢 <b>{company.company_name or 'Без названия'}</b>\n\n"
    text += f"📊 Рейтинг: {stars} ({rating}/10)\n\n"

    text += "<b>Составляющие рейтинга:</b>\n"
    text += f"• Регистрация: +{company.rating_registration}\n"
    text += f"• Подписка: +{company.rating_subscription}\n"
    text += f"• Опыт >1 года: +{company.rating_experience}\n"
    text += f"• Верификация: +{company.rating_verified}\n"
    text += f"• Сделки: +{company.rating_deals_completed}\n"
    text += f"• Без претензий: {'+' if company.rating_no_claims >= 0 else ''}{company.rating_no_claims}\n"
    text += f"• Скорость ответа: +{company.rating_response_time}\n"
    text += f"• Документы: +{company.rating_documents}\n\n"

    if company.inn:
        text += f"📋 ИНН: {company.inn}\n"
    if claims_count:
        text += f"⚠️ Претензий: {claims_count} (открытых: {open_claims})\n"

    await cb.message.edit_text(
        text,
        reply_markup=company_actions_kb(company_id, cb.from_user.id),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("new_claim_"))
async def start_claim(cb: CallbackQuery, state: FSMContext):
    """Начать подачу претензии"""
    to_company_id = int(cb.data.split("_")[-1])

    await state.update_data(to_company_id=to_company_id)
    await cb.message.edit_text(
        "📝 <b>Подача претензии</b>\n\n"
        "Выберите тип претензии:",
        reply_markup=claim_type_kb(),
    )
    await state.set_state(ClaimForm.claim_type)
    await cb.answer()


@router.callback_query(F.data.startswith("claim_type_"), ClaimForm.claim_type)
async def set_claim_type(cb: CallbackQuery, state: FSMContext):
    claim_type = cb.data.split("_")[-1]
    await state.update_data(claim_type=claim_type)

    await cb.message.edit_text(
        f"📝 Тип: {CLAIM_TYPES.get(claim_type, claim_type)}\n\n"
        "Введите заголовок претензии (кратко):"
    )
    await state.set_state(ClaimForm.title)
    await cb.answer()


@router.message(ClaimForm.title)
async def set_claim_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text[:255])
    await message.answer(
        "📝 Опишите претензию подробно:\n\n"
        "<i>Что произошло, когда, какие последствия</i>"
    )
    await state.set_state(ClaimForm.description)


@router.message(ClaimForm.description)
async def set_claim_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer(
        "💰 Укажите сумму претензии (в рублях):\n\n"
        "Или напишите 0 если без суммы"
    )
    await state.set_state(ClaimForm.amount)


@router.message(ClaimForm.amount)
async def set_claim_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.replace(" ", "").replace("₽", ""))
    except ValueError:
        amount = 0

    data = await state.get_data()

    async with async_session() as session:
        from_company = await session.scalar(
            select(CompanyDetails).where(CompanyDetails.user_id == message.from_user.id)
        )
        to_company = await session.scalar(
            select(CompanyDetails).where(CompanyDetails.id == data["to_company_id"])
        )

        claim = Claim(
            from_user_id=message.from_user.id,
            from_company_id=from_company.id if from_company else None,
            to_user_id=to_company.user_id if to_company else 0,
            to_company_id=data["to_company_id"],
            claim_type=data["claim_type"],
            title=data["title"],
            description=data["description"],
            amount=amount if amount > 0 else None,
            status=ClaimStatus.OPEN,
        )

        if to_company:
            to_company.rating_no_claims = max(-2, to_company.rating_no_claims - 1)

        session.add(claim)
        await session.commit()
        await session.refresh(claim)

        logger.info("Claim #%s created by %s", claim.id, message.from_user.id)

    await state.clear()
    await message.answer(
        f"✅ Претензия #{claim.id} подана!\n\n"
        f"📋 {data['title']}\n"
        f"💰 Сумма: {amount:,} ₽\n\n"
        "Компания получит уведомление и сможет ответить.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data.startswith("company_claims_"))
async def company_claims_list(cb: CallbackQuery):
    """Список претензий к компании"""
    company_id = int(cb.data.split("_")[-1])
    async with async_session() as session:
        company = await session.scalar(
            select(CompanyDetails).where(CompanyDetails.id == company_id)
        )
        if not company:
            await cb.answer("❌ Компания не найдена", show_alert=True)
            return
        claims = (
            await session.execute(
                select(Claim)
                .where(Claim.to_company_id == company_id)
                .order_by(Claim.created_at.desc())
                .limit(15)
            )
        ).scalars().all()
    status_icons = {"open": "🔴", "in_review": "🟡", "resolved": "🟢", "rejected": "⚫"}
    text = f"📋 <b>Претензии к компании</b> {company.company_name or ''}\n\n"
    if not claims:
        text += "Претензий нет."
    else:
        for c in claims:
            icon = status_icons.get(c.status.value, "❓")
            text += f"{icon} #{c.id} {c.title[:40]} — {c.status.value}\n"
    await cb.message.edit_text(
        text,
        reply_markup=company_actions_kb(company_id, cb.from_user.id),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("my_claims"))
async def my_claims(cb: CallbackQuery):
    """Мои претензии (поданные и полученные)"""
    async with async_session() as session:
        company = await session.scalar(
            select(CompanyDetails).where(CompanyDetails.user_id == cb.from_user.id)
        )

        sent = (
            await session.execute(
                select(Claim).where(Claim.from_user_id == cb.from_user.id).limit(10)
            )
        ).scalars().all()

        received = []
        if company:
            received = (
                await session.execute(
                    select(Claim)
                    .where(Claim.to_company_id == company.id)
                    .limit(10)
                )
            ).scalars().all()

    status_icons = {
        "open": "🔴",
        "in_review": "🟡",
        "resolved": "🟢",
        "rejected": "⚫",
    }

    text = "📋 <b>Мои претензии</b>\n\n"

    if sent:
        text += "<b>Поданные:</b>\n"
        for c in sent:
            icon = status_icons.get(c.status.value, "❓")
            text += f"{icon} #{c.id} {c.title[:30]}\n"

    if received:
        text += "\n<b>Полученные:</b>\n"
        for c in received:
            icon = status_icons.get(c.status.value, "❓")
            text += f"{icon} #{c.id} {c.title[:30]}\n"

    if not sent and not received:
        text += "Претензий нет"

    await cb.message.edit_text(text, reply_markup=back_menu())
    await cb.answer()
