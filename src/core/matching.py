from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import AvailableTruck

logger = logging.getLogger(__name__)

_COMPATIBLE_TYPES: dict[str, list[str]] = {
    "тент": ["тент", "борт", "фура"],
    "борт": ["борт", "тент"],
    "рефрижератор": ["рефрижератор", "изотерм"],
    "изотерм": ["изотерм", "рефрижератор"],
    "газель": ["газель", "борт"],
    "трал": ["трал"],
    "манипулятор": ["манипулятор"],
    "самосвал": ["самосвал"],
    "контейнер": ["контейнер"],
    "цистерна": ["цистерна"],
}


@dataclass
class TruckMatch:
    id: int
    source: str
    truck_type: str | None
    capacity_tons: float | None
    base_city: str | None
    base_region: str | None
    routes: str | None
    phone: str | None
    avito_url: str | None
    raw_text: str
    score: float = 1.0


async def find_trucks(
    session: AsyncSession,
    *,
    from_city: str | None = None,
    to_city: str | None = None,
    truck_type: str | None = None,
    capacity_tons: float | None = None,
    limit: int = 20,
    allow_unknown_capacity: bool = False,
    allow_unknown_type: bool = False,
    allow_unknown_geo: bool = False,
    require_route_match: bool = False,
) -> list[TruckMatch]:
    stmt = (
        select(AvailableTruck)
        .where(AvailableTruck.is_active.is_(True))
        .order_by(AvailableTruck.last_seen_at.desc())
    )

    if capacity_tons is not None and capacity_tons > 0:
        capacity_filters = [AvailableTruck.capacity_tons >= capacity_tons]
        if allow_unknown_capacity:
            capacity_filters.append(AvailableTruck.capacity_tons.is_(None))
        stmt = stmt.where(or_(*capacity_filters))

    if truck_type:
        compatible = _COMPATIBLE_TYPES.get(truck_type.lower(), [truck_type.lower()])
        type_filters = [AvailableTruck.truck_type.in_(compatible)]
        if allow_unknown_type:
            type_filters.append(AvailableTruck.truck_type.is_(None))
        stmt = stmt.where(or_(*type_filters))

    if from_city:
        city_norm = from_city.strip().lower()
        geo_filters = [
            func.lower(AvailableTruck.base_city) == city_norm,
            func.lower(AvailableTruck.base_region).contains(city_norm[:6]),
            func.lower(AvailableTruck.routes).contains(city_norm[:6]),
        ]
        if allow_unknown_geo:
            geo_filters.append(AvailableTruck.base_city.is_(None))
        stmt = stmt.where(or_(*geo_filters))

    if to_city and require_route_match:
        dest_norm = to_city.strip().lower()
        route_filters = [func.lower(AvailableTruck.routes).contains(dest_norm[:6])]
        if allow_unknown_geo:
            route_filters.append(AvailableTruck.routes.is_(None))
        stmt = stmt.where(or_(*route_filters))

    stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        TruckMatch(
            id=row.id,
            source=row.source,
            truck_type=row.truck_type,
            capacity_tons=row.capacity_tons,
            base_city=row.base_city,
            base_region=row.base_region,
            routes=row.routes,
            phone=row.phone,
            avito_url=row.avito_url,
            raw_text=row.raw_text,
        )
        for row in rows
    ]


async def rank_trucks_ai(
    trucks: list[TruckMatch],
    *,
    from_city: str | None,
    to_city: str | None,
    truck_type: str | None,
    capacity_tons: float | None,
    top_n: int = 3,
) -> list[TruckMatch]:
    from src.core.config import settings

    if not trucks:
        return []
    if not (settings.groq_api_key or settings.openai_api_key):
        return trucks[:top_n]
    if len(trucks) <= top_n:
        return trucks

    items = []
    for index, truck in enumerate(trucks):
        desc = truck.raw_text[:300].replace("\n", " ")
        items.append(f"{index}: {desc}")

    prompt_system = (
        "Ты — логист. Выбери индексы наиболее подходящих перевозчиков "
        f"для маршрута {from_city or '?'} → {to_city or '?'}, "
        f"тип кузова: {truck_type or 'любой'}, "
        f"грузоподъёмность: {capacity_tons or '?'} т. "
        f"Верни только {top_n} индекса через запятую, например: 2,5,11. "
        "Предпочитай тех, кто работает без выходных, имеет конкретные маршруты и телефон."
    )
    prompt_user = "\n".join(items)

    try:
        if settings.groq_api_key:
            from groq import AsyncGroq

            client = AsyncGroq(api_key=settings.groq_api_key)
            response = await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": prompt_system},
                    {"role": "user", "content": prompt_user},
                ],
                max_tokens=20,
                temperature=0,
            )
            raw = response.choices[0].message.content.strip()
        else:
            import httpx

            async with httpx.AsyncClient(timeout=15) as http:
                response = await http.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json={
                        "model": settings.openai_model,
                        "messages": [
                            {"role": "system", "content": prompt_system},
                            {"role": "user", "content": prompt_user},
                        ],
                        "max_tokens": 20,
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"].strip()

        indices = [int(value.strip()) for value in raw.split(",") if value.strip().isdigit()]
        result = [trucks[index] for index in indices if 0 <= index < len(trucks)]
        if result:
            logger.info("AI ranking selected indices=%s from %s candidates", indices, len(trucks))
            return result[:top_n]
    except Exception as exc:
        logger.warning("AI ranking failed: %s", str(exc)[:200])

    return trucks[:top_n]


async def match_trucks(
    session: AsyncSession,
    *,
    from_city: str | None = None,
    to_city: str | None = None,
    truck_type: str | None = None,
    capacity_tons: float | None = None,
    top_n: int = 3,
) -> list[TruckMatch]:
    candidates = await find_trucks(
        session,
        from_city=from_city,
        to_city=to_city,
        truck_type=truck_type,
        capacity_tons=capacity_tons,
        limit=30,
        require_route_match=bool(to_city),
    )

    if not candidates:
        candidates = await find_trucks(
            session,
            from_city=from_city,
            to_city=to_city,
            truck_type=truck_type,
            capacity_tons=capacity_tons,
            limit=30,
            allow_unknown_capacity=True,
            allow_unknown_type=True,
        )

    if not candidates:
        candidates = await find_trucks(
            session,
            truck_type=truck_type,
            capacity_tons=capacity_tons,
            limit=30,
            allow_unknown_capacity=True,
            allow_unknown_type=True,
        )

    if not candidates:
        candidates = await find_trucks(
            session,
            truck_type=truck_type,
            capacity_tons=capacity_tons,
            limit=30,
            allow_unknown_capacity=True,
            allow_unknown_type=True,
            allow_unknown_geo=True,
        )

    return await rank_trucks_ai(
        candidates,
        from_city=from_city,
        to_city=to_city,
        truck_type=truck_type,
        capacity_tons=capacity_tons,
        top_n=top_n,
    )
