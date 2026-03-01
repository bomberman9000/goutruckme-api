from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class SharedOrderSchema(BaseModel):
    id: str
    search_id: str | None = None
    user_id: int | None = None
    from_city: str | None = None
    to_city: str | None = None
    cargo_type: str | None = None
    weight_t: float | None = None
    price_rub: int | None = None
    load_date: str | None = None
    status: str | None = None
    source: Literal["tg-bot", "gruzpotok-api", "unknown"] = "unknown"
    meta: dict[str, Any] = Field(default_factory=dict)


class SharedVehicleSchema(BaseModel):
    id: str
    search_id: str | None = None
    user_id: int | None = None
    from_city: str | None = None
    to_city: str | None = None
    location_city: str | None = None
    location_region: str | None = None
    body_type: str | None = None
    capacity_t: float | None = None
    capacity_tons: float | None = None
    volume_m3: float | None = None
    price_rub: int | None = None
    plate_number: str | None = None
    is_available: bool | None = None
    vehicle_kind: str | None = None
    name: str | None = None
    status: str | None = None
    source: Literal["tg-bot", "gruzpotok-api", "unknown"] = "unknown"
    meta: dict[str, Any] = Field(default_factory=dict)


class InternalNotifyUserRequest(BaseModel):
    user_id: int
    message: str = Field(min_length=1, max_length=4096)
    action_link: str | None = None
    action_text: str = Field(default="Открыть", min_length=1, max_length=64)
    disable_web_page_preview: bool = True


class SharedSyncEvent(BaseModel):
    event_id: str
    event_type: str
    source: Literal["tg-bot", "gruzpotok-api", "unknown"] = "unknown"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    search_id: str | None = None
    user_id: int | None = None
    order: SharedOrderSchema | None = None
    vehicle: SharedVehicleSchema | None = None
    message: str | None = None
    action_link: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BotInternalEvent(BaseModel):
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    event_id: str | None = None
    source: Literal["tg-bot", "gruzpotok-api", "unknown"] = "unknown"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
