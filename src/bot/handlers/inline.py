from aiogram import Router
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from sqlalchemy import select

from src.core.database import async_session
from src.core.models import Cargo, CargoStatus
from src.bot.utils import cargo_deeplink

router = Router()


def _parse_query(q: str):
    q = (q or "").strip()
    if not q:
        return None, None
    parts = [p for p in q.split() if p]
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


@router.inline_query()
async def inline_search(inline: InlineQuery):
    from_city, to_city = _parse_query(inline.query)

    async with async_session() as session:
        q = select(Cargo).where(Cargo.status == CargoStatus.NEW)
        if from_city:
            q = q.where(Cargo.from_city.ilike(f"%{from_city}%"))
        if to_city:
            q = q.where(Cargo.to_city.ilike(f"%{to_city}%"))

        res = await session.execute(q.order_by(Cargo.created_at.desc()).limit(10))
        cargos = res.scalars().all()

    results = []
    for c in cargos:
        title = f"{c.from_city} → {c.to_city} | {c.weight}т | {c.price}₽"
        desc = f"{c.cargo_type} • {c.load_date.strftime('%d.%m.%Y')}"
        link = cargo_deeplink(c.id)
        text = (
            f"📦 <b>Груз #{c.id}</b>\n"
            f"📍 {c.from_city} → {c.to_city}\n"
            f"📦 {c.cargo_type}\n"
            f"⚖️ {c.weight} т\n"
            f"💰 {c.price} ₽\n"
            f"📅 {c.load_date.strftime('%d.%m.%Y')}\n"
            f"\nОткрыть в боте: {link}"
        )

        results.append(
            InlineQueryResultArticle(
                id=str(c.id),
                title=title,
                description=desc,
                input_message_content=InputTextMessageContent(
                    message_text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                ),
            )
        )

    await inline.answer(results, cache_time=2, is_personal=True)
