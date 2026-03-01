from __future__ import annotations

import asyncio

from sqlalchemy import select

from src.core.database import async_session
from src.core.models import UserVehicle
from src.core.services.fleet_sync import publish_vehicle_sync_event


async def main() -> None:
    synced = 0
    failed = 0

    async with async_session() as session:
        rows = (
            await session.execute(
                select(UserVehicle).order_by(UserVehicle.id.asc())
            )
        ).scalars().all()

    for vehicle in rows:
        ok = await publish_vehicle_sync_event(vehicle, event_type="vehicle_backfill")
        if ok:
            synced += 1
        else:
            failed += 1

    print(f"vehicle_sync_backfill synced={synced} failed={failed}")


if __name__ == "__main__":
    asyncio.run(main())
