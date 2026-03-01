from __future__ import annotations

from src.core.models import UserVehicle
from src.core.schemas.sync import SharedSyncEvent, SharedVehicleSchema
from src.core.services.cross_sync import make_search_id, publish_sync_event


async def publish_vehicle_sync_event(
    vehicle: UserVehicle,
    *,
    event_type: str = "vehicle_upsert",
) -> bool:
    payload = SharedVehicleSchema(
        id=str(vehicle.id),
        search_id=f"vehicle_{vehicle.id}",
        user_id=int(vehicle.user_id),
        from_city=vehicle.location_city,
        location_city=vehicle.location_city,
        body_type=vehicle.body_type,
        capacity_t=float(vehicle.capacity_tons or 0),
        capacity_tons=float(vehicle.capacity_tons or 0),
        plate_number=(vehicle.plate_number or "").strip() or None,
        is_available=bool(vehicle.is_available),
        status="active",
        source="tg-bot",
        meta={
            "is_available": bool(vehicle.is_available),
            "sts_verified": bool(vehicle.sts_verified),
        },
    )
    event = SharedSyncEvent(
        event_id=make_search_id(),
        event_type=event_type,
        source="tg-bot",
        search_id=payload.search_id,
        user_id=payload.user_id,
        vehicle=payload,
        metadata={
            "origin": "fleet",
            "is_available": bool(vehicle.is_available),
            "plate_number": payload.plate_number,
        },
    )
    return await publish_sync_event(event)
