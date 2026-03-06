from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import async_session
from src.core.models import AvailableTruck
from src.parser_bot.truck_extractor import parse_truck_regex

_INVALID_BASE_CITIES = {"Китай", "Китая", "РФ", "РБ", "СНГ", "межгород"}


def _needs_city_replacement(current: str | None, candidate: str | None) -> bool:
    if not candidate:
        return False
    if not current:
        return True
    return current.strip() in _INVALID_BASE_CITIES and candidate.strip() not in _INVALID_BASE_CITIES


async def main() -> None:
    updated = 0
    type_updates = 0
    capacity_updates = 0
    route_updates = 0
    city_updates = 0
    phone_updates = 0
    price_updates = 0

    async with async_session() as session:
        rows = (await session.execute(select(AvailableTruck).order_by(AvailableTruck.id))).scalars().all()

        for row in rows:
            parsed = parse_truck_regex(row.raw_text or "")
            changed = False

            if not row.truck_type and parsed.truck_type:
                row.truck_type = parsed.truck_type
                changed = True
                type_updates += 1
            if row.capacity_tons is None and parsed.capacity_tons is not None:
                row.capacity_tons = parsed.capacity_tons
                changed = True
                capacity_updates += 1
            if row.volume_m3 is None and parsed.volume_m3 is not None:
                row.volume_m3 = parsed.volume_m3
                changed = True
            if _needs_city_replacement(row.base_city, parsed.base_city):
                row.base_city = parsed.base_city
                changed = True
                city_updates += 1
            if not row.base_region and parsed.base_region:
                row.base_region = parsed.base_region
                changed = True
            if not row.routes and parsed.routes:
                row.routes = parsed.routes
                changed = True
                route_updates += 1
            if not row.phone and parsed.phone:
                row.phone = parsed.phone
                changed = True
                phone_updates += 1
            if (row.price_rub is None or row.price_rub <= 0) and parsed.price_rub and parsed.price_rub > 0:
                row.price_rub = parsed.price_rub
                changed = True
                price_updates += 1

            if changed:
                updated += 1

        await session.commit()

    print(
        {
            "updated": updated,
            "type_updates": type_updates,
            "capacity_updates": capacity_updates,
            "route_updates": route_updates,
            "city_updates": city_updates,
            "phone_updates": phone_updates,
            "price_updates": price_updates,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
