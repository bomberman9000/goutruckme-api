from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from math import asin, cos, radians, sin, sqrt
import re
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.ai.scoring import MarketStats, compute_ai_score
from app.core.security import get_current_user
from app.db.database import get_db
from app.dicts.vehicles import (
    ADR_CLASSES,
    BODY_TYPE_ALIASES,
    LEGACY_BODY_KIND,
    LOADING_TYPE_ALIASES,
    LOADING_TYPES,
    TEMP_VEHICLE_KINDS,
    VEHICLE_KINDS,
    VEHICLE_OPTIONS,
)
from app.matching.compat import check_compat
from app.models.models import City, Load, User, UserRole, Vehicle
from app.services.cargo_status import apply_cargo_status_filter
from app.services.vehicle_ai import analyze_vehicle_submission, count_matching_loads
from app.trust.service import get_company_trust_snapshot

router = APIRouter()


VEHICLE_KIND_META: dict[str, dict[str, str]] = {
    key: {"label": value["label"], "category": value["group"], "body_type": value.get("body_type", "")}
    for key, value in VEHICLE_KINDS.items()
}
LOADING_TYPE_LABELS = LOADING_TYPES
VEHICLE_OPTION_LABELS = VEHICLE_OPTIONS
_BODY_TYPE_ALIASES = BODY_TYPE_ALIASES
ALLOWED_VEHICLE_OPTIONS = set(VEHICLE_OPTIONS.keys())
ALLOWED_LOADING_TYPES = set(LOADING_TYPES.keys())
_ALLOWED_STATUSES = {"active", "inactive", "archived"}
_TEMP_KINDS = set(TEMP_VEHICLE_KINDS)
_ALLOWED_SCOPE = {"owner", "all"}
_MATCHING_CACHE_TTL_SECONDS = 60.0
_MATCHING_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_MAX_LIST_PAGE_SIZE = 100
_DEFAULT_LIST_PAGE_SIZE = 50

ADR_CLASS_RE = re.compile(r"^[1-9](?:\.[0-9])?$")
ALLOWED_ADR_CLASSES = set(ADR_CLASSES)


class VehicleCreateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    vehicle_kind: Optional[str] = None
    body_type: Optional[str] = None
    brand: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=64)
    plate_number: Optional[str] = Field(default=None, max_length=24)
    vin: Optional[str] = Field(default=None, max_length=64)
    pts_number: Optional[str] = Field(default=None, max_length=64)

    payload_tons: Optional[float] = Field(default=None, gt=0)
    capacity_tons: Optional[float] = Field(default=None, gt=0)
    volume_m3: Optional[float] = Field(default=None, gt=0)
    max_weight_t: Optional[float] = Field(default=None, gt=0)
    max_volume_m3: Optional[float] = Field(default=None, gt=0)

    length_m: Optional[float] = Field(default=None, gt=0)
    width_m: Optional[float] = Field(default=None, gt=0)
    height_m: Optional[float] = Field(default=None, gt=0)

    loading_types: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    adr_classes: list[str] = Field(default_factory=list)
    crew_size: int = Field(default=1, ge=1, le=4)
    temp_min: Optional[float] = None
    temp_max: Optional[float] = None

    city_id: Optional[int] = Field(default=None, ge=1)
    location_city: Optional[str] = Field(default=None, max_length=120)
    location_region: Optional[str] = Field(default=None, max_length=120)

    radius_km: Optional[int] = Field(default=50, ge=1, le=3000)
    available_from: Optional[date] = None
    available_to: Optional[date] = None
    rate_per_km: Optional[float] = Field(default=None, gt=0)

    start_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    start_lon: Optional[float] = Field(default=None, ge=-180, le=180)

    owner_user_id: Optional[int] = None
    carrier_id: Optional[int] = None

    @field_validator("vehicle_kind")
    @classmethod
    def normalize_kind(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        if normalized and normalized not in VEHICLE_KIND_META:
            allowed = ", ".join(sorted(VEHICLE_KIND_META.keys()))
            raise ValueError(f"vehicle_kind должен быть одним из: {allowed}")
        return normalized or None

    @field_validator("loading_types", mode="before")
    @classmethod
    def validate_loading_types(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, (list, tuple, set)):
            raw_items = [str(part).strip() for part in value if str(part).strip()]
        else:
            raise ValueError("loading_types должен быть массивом")

        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            normalized = LOADING_TYPE_ALIASES.get(_norm(item), _norm(item))
            if normalized not in ALLOWED_LOADING_TYPES:
                allowed = ", ".join(sorted(ALLOWED_LOADING_TYPES))
                raise ValueError(f"loading_types: '{item}' недопустим. Разрешено: {allowed}")
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    @field_validator("options", mode="before")
    @classmethod
    def validate_options(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, (list, tuple, set)):
            raw_items = [str(part).strip() for part in value if str(part).strip()]
        else:
            raise ValueError("options должен быть массивом")

        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            normalized = _norm(item)
            if normalized not in ALLOWED_VEHICLE_OPTIONS:
                allowed = ", ".join(sorted(ALLOWED_VEHICLE_OPTIONS))
                raise ValueError(f"options: '{item}' недопустим. Разрешено: {allowed}")
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    @field_validator("adr_classes", mode="before")
    @classmethod
    def validate_adr_classes(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, (list, tuple, set)):
            raw_items = [str(part).strip() for part in value if str(part).strip()]
        else:
            raise ValueError("adr_classes должен быть массивом")
        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            candidate = item.replace("class", "").replace(",", ".").replace(" ", "").strip(".")
            if not ADR_CLASS_RE.match(candidate) or candidate not in ALLOWED_ADR_CLASSES:
                allowed = ", ".join(ADR_CLASSES)
                raise ValueError(f"adr_classes: '{item}' недопустим. Разрешено: {allowed}")
            if candidate in seen:
                continue
            seen.add(candidate)
            result.append(candidate)
        return result

    @field_validator("plate_number")
    @classmethod
    def normalize_plate(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = _normalize_plate(value)
        return normalized or None

    @model_validator(mode="after")
    def normalize_temperature(self):
        if self.temp_min is not None and self.temp_max is not None and float(self.temp_min) > float(self.temp_max):
            raise ValueError("temp_min не может быть больше temp_max")
        if self.vehicle_kind and self.vehicle_kind not in _TEMP_KINDS:
            # Для не-температурных ТС поля температуры игнорируем.
            self.temp_min = None
            self.temp_max = None
        return self


class VehicleUpdateRequest(VehicleCreateRequest):
    status: Optional[str] = None


class VehicleStatusUpdateRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        status = _norm(value)
        if status not in _ALLOWED_STATUSES:
            allowed = ", ".join(sorted(_ALLOWED_STATUSES))
            raise ValueError(f"status должен быть одним из: {allowed}")
        return status


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _normalize_plate(value: str) -> str:
    compact = "".join(ch for ch in str(value).strip().upper() if ch.isalnum())
    return compact


def _canonical_body_type(body_type: Optional[str], *, vehicle_kind: Optional[str] = None) -> str:
    body = _BODY_TYPE_ALIASES.get(_norm(body_type), "")
    if body:
        return body
    if vehicle_kind and vehicle_kind in VEHICLE_KIND_META:
        return VEHICLE_KIND_META[vehicle_kind]["body_type"]
    return ""


def _resolve_vehicle_kind(vehicle_kind: Optional[str], body_type: Optional[str]) -> str:
    normalized_kind = str(vehicle_kind or "").strip().upper()
    if normalized_kind in VEHICLE_KIND_META:
        return normalized_kind

    body = _canonical_body_type(body_type)
    mapped_kind = LEGACY_BODY_KIND.get(body)
    if mapped_kind:
        return mapped_kind

    allowed = ", ".join(sorted(VEHICLE_KIND_META.keys()))
    raise HTTPException(status_code=422, detail=f"vehicle_kind должен быть одним из: {allowed}")


def _normalize_list(
    values: Any,
    *,
    allowed: set[str],
    aliases: Optional[dict[str, str]] = None,
    field_name: str,
) -> list[str]:
    aliases = aliases or {}

    if values is None:
        return []
    if isinstance(values, str):
        raw_items = [part.strip() for part in values.split(",") if part.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw_items = [str(part).strip() for part in values if str(part).strip()]
    else:
        raise HTTPException(status_code=422, detail=f"{field_name} должен быть списком")

    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        normalized = _norm(item)
        normalized = aliases.get(normalized, normalized)
        if normalized not in allowed:
            allowed_values = ", ".join(sorted(allowed))
            raise HTTPException(status_code=422, detail=f"{field_name}: недопустимое значение '{item}'. Разрешено: {allowed_values}")
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _normalize_generic_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_items = [part.strip() for part in values.replace(";", ",").split(",") if part.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw_items = [str(part).strip() for part in values if str(part).strip()]
    else:
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        normalized = _norm(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _normalize_adr_classes(values: Any, *, field_name: str = "adr_classes") -> list[str]:
    normalized_values = _normalize_generic_list(values)
    result: list[str] = []
    seen: set[str] = set()
    for raw in normalized_values:
        candidate = raw.replace("class", "").replace(" ", "").replace(",", ".")
        candidate = candidate.strip(".")
        if not ADR_CLASS_RE.match(candidate) or candidate not in ALLOWED_ADR_CLASSES:
            raise HTTPException(status_code=422, detail=f"{field_name}: недопустимое значение '{raw}'")
        if candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def _vehicle_owner_filter(user_id: int):
    return or_(
        Vehicle.owner_user_id == int(user_id),
        and_(Vehicle.owner_user_id.is_(None), Vehicle.carrier_id == int(user_id)),
    )


def _require_vehicle_access(vehicle: Optional[Vehicle], current_user: User) -> Vehicle:
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина не найдена")

    owner_id = int(vehicle.owner_user_id or vehicle.carrier_id)
    if owner_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return vehicle


def _matching_cache_key(*, vehicle_id: int, filters: dict[str, Any]) -> str:
    raw = json.dumps(filters, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    hashed = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"vehicle:{vehicle_id}:{hashed}"


def _matching_cache_get(key: str) -> Optional[dict[str, Any]]:
    entry = _MATCHING_CACHE.get(key)
    if not entry:
        return None

    expires_at, payload = entry
    if expires_at < time.time():
        _MATCHING_CACHE.pop(key, None)
        return None
    # Через JSON не разделяем mutable ссылки.
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _matching_cache_set(key: str, payload: dict[str, Any]) -> None:
    _MATCHING_CACHE[key] = (
        time.time() + _MATCHING_CACHE_TTL_SECONDS,
        json.loads(json.dumps(payload, ensure_ascii=False)),
    )

    if len(_MATCHING_CACHE) > 512:
        now_ts = time.time()
        expired_keys = [cache_key for cache_key, (expires_at, _) in _MATCHING_CACHE.items() if expires_at < now_ts]
        for cache_key in expired_keys:
            _MATCHING_CACHE.pop(cache_key, None)
        if len(_MATCHING_CACHE) > 512:
            oldest_key = min(_MATCHING_CACHE.items(), key=lambda item: item[1][0])[0]
            _MATCHING_CACHE.pop(oldest_key, None)


def _matching_cache_invalidate(vehicle_id: int) -> None:
    prefix = f"vehicle:{int(vehicle_id)}:"
    for cache_key in [key for key in _MATCHING_CACHE.keys() if key.startswith(prefix)]:
        _MATCHING_CACHE.pop(cache_key, None)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return radius * c


def _vehicle_point(vehicle: Vehicle, db: Session, city_cache: dict[int, tuple[float, float] | None]) -> tuple[float, float] | None:
    if vehicle.start_lat is not None and vehicle.start_lon is not None:
        return (float(vehicle.start_lat), float(vehicle.start_lon))

    if vehicle.city_id:
        city_id = int(vehicle.city_id)
        if city_id in city_cache:
            return city_cache[city_id]
        city = db.query(City).filter(City.id == city_id).first()
        if city and city.lat is not None and city.lon is not None:
            point = (float(city.lat), float(city.lon))
            city_cache[city_id] = point
            return point
        city_cache[city_id] = None

    return None


def _load_pickup_point(load: Load, db: Session, city_cache: dict[int, tuple[float, float] | None]) -> tuple[float, float] | None:
    if load.pickup_lat is not None and load.pickup_lon is not None:
        return (float(load.pickup_lat), float(load.pickup_lon))

    if load.from_city_id:
        city_id = int(load.from_city_id)
        if city_id in city_cache:
            return city_cache[city_id]
        city = db.query(City).filter(City.id == city_id).first()
        if city and city.lat is not None and city.lon is not None:
            point = (float(city.lat), float(city.lon))
            city_cache[city_id] = point
            return point
        city_cache[city_id] = None

    return None


def _resolve_city_fields(
    db: Session,
    *,
    city_id: Optional[int],
    location_city: Optional[str],
    location_region: Optional[str],
) -> tuple[Optional[int], str, Optional[str], Optional[float], Optional[float]]:
    selected_city_id = int(city_id) if city_id is not None else None
    selected_city = (location_city or "").strip()
    selected_region = (location_region or "").strip() or None
    lat: Optional[float] = None
    lon: Optional[float] = None

    if selected_city_id:
        city = db.query(City).filter(City.id == selected_city_id).first()
        if not city:
            raise HTTPException(status_code=422, detail="city_id не найден")
        selected_city = city.name
        if not selected_region:
            selected_region = city.region
        lat = float(city.lat) if city.lat is not None else None
        lon = float(city.lon) if city.lon is not None else None

    if not selected_city:
        raise HTTPException(status_code=422, detail="Укажите город базирования")

    return selected_city_id, selected_city, selected_region, lat, lon


def _is_admin(user: User) -> bool:
    return user.role == UserRole.admin


def _owner_id_from_payload(payload: VehicleCreateRequest | VehicleUpdateRequest, current_user: User) -> int:
    requested_owner = payload.owner_user_id or payload.carrier_id or current_user.id
    if requested_owner != current_user.id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Можно управлять только своими машинами")
    return int(requested_owner)


def _ensure_plate_unique(
    db: Session,
    *,
    owner_user_id: int,
    plate_number: Optional[str],
    exclude_vehicle_id: Optional[int] = None,
) -> None:
    if not plate_number:
        return

    query = db.query(Vehicle).filter(
        Vehicle.owner_user_id == owner_user_id,
        func.upper(Vehicle.plate_number) == plate_number.upper(),
    )
    if exclude_vehicle_id is not None:
        query = query.filter(Vehicle.id != int(exclude_vehicle_id))

    existing = query.first()
    if existing:
        raise HTTPException(status_code=409, detail="Машина с таким госномером уже существует")


def _to_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _resolve_vehicle_payload(
    *,
    payload: VehicleCreateRequest | VehicleUpdateRequest,
    db: Session,
    partial: bool,
    existing: Optional[Vehicle] = None,
) -> dict[str, Any]:
    raw = payload.model_dump(exclude_unset=partial)

    vehicle_kind_input = raw.get("vehicle_kind", existing.vehicle_kind if existing else None)
    body_input = raw.get("body_type", existing.body_type if existing else None)
    vehicle_kind = _resolve_vehicle_kind(vehicle_kind_input, body_input)
    body_type = _canonical_body_type(body_input, vehicle_kind=vehicle_kind)
    if not body_type:
        raise HTTPException(status_code=422, detail="Не удалось определить тип кузова")

    plate_number = _normalize_plate(raw.get("plate_number", existing.plate_number if existing else None) or "")
    if not plate_number and not partial:
        raise HTTPException(status_code=422, detail="Укажите госномер")

    name = (raw.get("name", existing.name if existing else None) or "").strip() or None
    brand = (raw.get("brand", existing.brand if existing else None) or "").strip() or None
    model = (raw.get("model", existing.model if existing else None) or "").strip() or None
    vin = (raw.get("vin", existing.vin if existing else None) or "").strip() or None
    pts_number = (raw.get("pts_number", existing.pts_number if existing else None) or "").strip() or None

    payload_tons = _to_optional_float(
        raw.get("payload_tons", raw.get("capacity_tons", existing.payload_tons if existing else (existing.capacity_tons if existing else None)))
    )
    if payload_tons is None:
        payload_tons = _to_optional_float(existing.capacity_tons if existing else None)
    if payload_tons is None and not partial:
        raise HTTPException(status_code=422, detail="Укажите грузоподъёмность (т)")
    if payload_tons is not None and payload_tons <= 0:
        raise HTTPException(status_code=422, detail="Грузоподъёмность должна быть больше 0")

    length_m = _to_optional_float(raw.get("length_m", existing.length_m if existing else None))
    width_m = _to_optional_float(raw.get("width_m", existing.width_m if existing else None))
    height_m = _to_optional_float(raw.get("height_m", existing.height_m if existing else None))

    volume_m3 = _to_optional_float(raw.get("volume_m3", existing.volume_m3 if existing else None))
    if volume_m3 is None and length_m and width_m and height_m:
        volume_m3 = round(length_m * width_m * height_m, 2)

    if volume_m3 is None and not partial:
        raise HTTPException(status_code=422, detail="Укажите объём (м³) или все габариты Д×Ш×В")
    if volume_m3 is not None and volume_m3 <= 0:
        raise HTTPException(status_code=422, detail="Объём должен быть больше 0")

    max_weight_t = _to_optional_float(raw.get("max_weight_t", existing.max_weight_t if existing else None))
    if max_weight_t is None:
        max_weight_t = payload_tons

    max_volume_m3 = _to_optional_float(raw.get("max_volume_m3", existing.max_volume_m3 if existing else None))
    if max_volume_m3 is None:
        max_volume_m3 = volume_m3

    city_id = raw.get("city_id", existing.city_id if existing else None)
    location_city = raw.get("location_city", existing.location_city if existing else None)
    location_region = raw.get("location_region", existing.location_region if existing else None)
    city_id, location_city, location_region, city_lat, city_lon = _resolve_city_fields(
        db,
        city_id=city_id,
        location_city=location_city,
        location_region=location_region,
    )

    radius_km_raw = raw.get("radius_km", existing.radius_km if existing else 50)
    radius_km = int(radius_km_raw or 50)
    if radius_km <= 0 or radius_km > 3000:
        raise HTTPException(status_code=422, detail="radius_km должен быть в диапазоне 1..3000")

    available_from = raw.get("available_from", existing.available_from if existing else date.today())
    if not available_from:
        available_from = date.today()
    available_to = raw.get("available_to", existing.available_to if existing else None)
    if available_to and available_to < available_from:
        raise HTTPException(status_code=422, detail="Дата 'доступен до' не может быть раньше 'доступен с'")

    rate_per_km = _to_optional_float(raw.get("rate_per_km", existing.rate_per_km if existing else None))
    if rate_per_km is not None and rate_per_km <= 0:
        raise HTTPException(status_code=422, detail="Ставка за км должна быть больше 0")

    start_lat = _to_optional_float(raw.get("start_lat", existing.start_lat if existing else None))
    start_lon = _to_optional_float(raw.get("start_lon", existing.start_lon if existing else None))
    if start_lat is None and city_lat is not None:
        start_lat = city_lat
    if start_lon is None and city_lon is not None:
        start_lon = city_lon

    loading_types = _normalize_list(
        raw.get("loading_types", existing.loading_types if existing else []),
        allowed=ALLOWED_LOADING_TYPES,
        aliases=LOADING_TYPE_ALIASES,
        field_name="loading_types",
    )
    options = _normalize_list(
        raw.get("options", existing.options if existing else []),
        allowed=ALLOWED_VEHICLE_OPTIONS,
        aliases=None,
        field_name="options",
    )
    adr_classes = _normalize_adr_classes(raw.get("adr_classes", existing.adr_classes if existing else []), field_name="adr_classes")
    if adr_classes and "adr" not in options:
        options.append("adr")

    crew_size_raw = raw.get("crew_size", existing.crew_size if existing else 1)
    try:
        crew_size = int(crew_size_raw if crew_size_raw is not None else 1)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="crew_size должен быть целым числом")
    if crew_size < 1 or crew_size > 4:
        raise HTTPException(status_code=422, detail="crew_size должен быть в диапазоне 1..4")

    temp_min = _to_optional_float(raw.get("temp_min", existing.temp_min if existing else None))
    temp_max = _to_optional_float(raw.get("temp_max", existing.temp_max if existing else None))

    if temp_min is not None and temp_max is not None and temp_min > temp_max:
        raise HTTPException(status_code=422, detail="temp_min не может быть больше temp_max")

    if vehicle_kind not in _TEMP_KINDS:
        temp_min = None
        temp_max = None

    status = _norm(raw.get("status", existing.status if existing else "active")) or "active"
    if status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail="status должен быть active|inactive|archived")

    if partial and existing is not None:
        if payload_tons is None:
            payload_tons = _to_optional_float(existing.payload_tons or existing.capacity_tons)
        if volume_m3 is None:
            volume_m3 = _to_optional_float(existing.volume_m3)
        if max_weight_t is None:
            max_weight_t = _to_optional_float(existing.max_weight_t or payload_tons)
        if max_volume_m3 is None:
            max_volume_m3 = _to_optional_float(existing.max_volume_m3 or volume_m3)

    return {
        "name": name,
        "vehicle_kind": vehicle_kind,
        "body_type": body_type,
        "brand": brand,
        "model": model,
        "plate_number": plate_number or None,
        "vin": vin,
        "pts_number": pts_number,
        "payload_tons": payload_tons,
        "capacity_tons": payload_tons,
        "volume_m3": volume_m3,
        "max_weight_t": max_weight_t,
        "max_volume_m3": max_volume_m3,
        "length_m": length_m,
        "width_m": width_m,
        "height_m": height_m,
        "loading_types": loading_types,
        "options": options,
        "adr_classes": adr_classes,
        "crew_size": crew_size,
        "temp_min": temp_min,
        "temp_max": temp_max,
        "city_id": city_id,
        "start_lat": start_lat,
        "start_lon": start_lon,
        "location_city": location_city,
        "location_region": location_region,
        "radius_km": radius_km,
        "available_from": available_from,
        "available_to": available_to,
        "rate_per_km": rate_per_km,
        "status": status,
    }


def _vehicle_to_dict(vehicle: Vehicle, db: Session, *, matching_loads: Optional[int] = None) -> dict:
    owner_id = int(vehicle.owner_user_id or vehicle.carrier_id)
    carrier = vehicle.carrier
    carrier_trust = get_company_trust_snapshot(db, owner_id)

    vehicle_matching_loads = matching_loads
    if vehicle_matching_loads is None:
        vehicle_matching_loads = count_matching_loads(
            db,
            capacity_tons=float(vehicle.payload_tons or vehicle.capacity_tons or 0.0),
            location_city=vehicle.location_city,
            location_region=vehicle.location_region,
            available_from=vehicle.available_from,
        )

    kind_meta = VEHICLE_KIND_META.get(str(vehicle.vehicle_kind or ""), {})
    loading_types = [item for item in (vehicle.loading_types or []) if item in ALLOWED_LOADING_TYPES]
    options = [item for item in (vehicle.options or []) if item in ALLOWED_VEHICLE_OPTIONS]
    try:
        adr_classes = _normalize_adr_classes(vehicle.adr_classes or [], field_name="adr_classes")
    except HTTPException:
        adr_classes = _normalize_generic_list(vehicle.adr_classes or [])

    return {
        "id": vehicle.id,
        "owner_user_id": owner_id,
        "carrier_id": vehicle.carrier_id,
        "name": vehicle.name,
        "vehicle_kind": vehicle.vehicle_kind,
        "vehicle_kind_label": kind_meta.get("label") or vehicle.vehicle_kind,
        "vehicle_kind_category": kind_meta.get("category"),
        "body_type": vehicle.body_type,
        "brand": vehicle.brand,
        "model": vehicle.model,
        "plate_number": vehicle.plate_number,
        "vin": vehicle.vin,
        "pts_number": vehicle.pts_number,
        "payload_tons": vehicle.payload_tons if vehicle.payload_tons is not None else vehicle.capacity_tons,
        "capacity_tons": vehicle.capacity_tons,
        "volume_m3": vehicle.volume_m3,
        "max_weight_t": vehicle.max_weight_t if vehicle.max_weight_t is not None else vehicle.capacity_tons,
        "max_volume_m3": vehicle.max_volume_m3 if vehicle.max_volume_m3 is not None else vehicle.volume_m3,
        "length_m": vehicle.length_m,
        "width_m": vehicle.width_m,
        "height_m": vehicle.height_m,
        "loading_types": loading_types,
        "loading_types_labels": [LOADING_TYPE_LABELS.get(item, item) for item in loading_types],
        "options": options,
        "options_labels": [VEHICLE_OPTION_LABELS.get(item, item) for item in options],
        "adr_classes": adr_classes,
        "crew_size": int(vehicle.crew_size or 1),
        "temp_min": vehicle.temp_min,
        "temp_max": vehicle.temp_max,
        "city_id": vehicle.city_id,
        "start_lat": vehicle.start_lat,
        "start_lon": vehicle.start_lon,
        "location_city": vehicle.location_city,
        "location_region": vehicle.location_region,
        "radius_km": vehicle.radius_km,
        "available_from": vehicle.available_from.isoformat() if vehicle.available_from else None,
        "available_to": vehicle.available_to.isoformat() if vehicle.available_to else None,
        "available_today": bool(vehicle.available_from and vehicle.available_from <= date.today() and (not vehicle.available_to or vehicle.available_to >= date.today())),
        "rate_per_km": vehicle.rate_per_km,
        "status": vehicle.status,
        "created_at": vehicle.created_at.isoformat() if vehicle.created_at else None,
        "updated_at": vehicle.updated_at.isoformat() if vehicle.updated_at else None,
        "carrier": {
            "id": carrier.id if carrier else owner_id,
            "organization_name": (carrier.organization_name if carrier else None) or (carrier.company if carrier else None),
            "rating": carrier.rating if carrier else None,
            "verified": carrier.verified if carrier else False,
            "trust_level": carrier.trust_level if carrier else None,
        },
        "trust": carrier_trust,
        "ai": {
            "risk_level": vehicle.ai_risk_level or "low",
            "score": vehicle.ai_score or 0,
            "warnings": vehicle.ai_warnings or [],
            "market_rate_per_km": vehicle.ai_market_rate,
            "idle_ratio": vehicle.ai_idle_ratio,
        },
        "matching_loads": vehicle_matching_loads,
    }


def _risk_rank(level: str) -> int:
    normalized = _norm(level)
    if normalized == "high":
        return 2
    if normalized == "medium":
        return 1
    return 0


def _risk_penalty_points(level: str) -> float:
    normalized = _norm(level)
    if normalized == "high":
        return 25.0
    if normalized == "medium":
        return 10.0
    return 0.0


def _calc_load_rate_per_km(load: Load) -> float:
    if load.rate_per_km and load.rate_per_km > 0:
        return float(load.rate_per_km)

    price = float(load.total_price if load.total_price is not None else (load.price or 0.0))
    distance = float(load.distance_km or 0.0)
    if distance > 0 and price > 0:
        return round(price / distance, 1)
    return 0.0


def _vehicle_matches_load(
    *,
    vehicle: Vehicle,
    load: Load,
    db: Session,
    city_cache: dict[int, tuple[float, float] | None],
    stats: MarketStats,
    trust_cache: dict[int, int],
    debug: bool = False,
) -> tuple[bool, Optional[dict[str, Any]], list[str]]:
    compat = check_compat(vehicle, load)
    if not bool(compat.get("ok")):
        blockers = list(compat.get("blockers") or ["Несовместимо по параметрам"])
        return False, None, blockers[:5]

    rejected_reasons: list[str] = []
    reasons: list[str] = list(compat.get("reasons") or [])

    if vehicle.available_from and load.loading_date and load.loading_date < vehicle.available_from:
        rejected_reasons.append("Дата погрузки раньше доступности машины")
        return False, None, rejected_reasons
    if vehicle.available_to and load.loading_date and load.loading_date > vehicle.available_to:
        rejected_reasons.append("Дата погрузки позже доступности машины")
        return False, None, rejected_reasons

    load_weight = float(load.weight_t if load.weight_t is not None else (load.weight or 0.0))
    load_volume = float(load.volume_m3 if load.volume_m3 is not None else (load.volume or 0.0))
    vehicle_kind = str(compat.get("vehicle_kind") or str(vehicle.vehicle_kind or "").upper())
    vehicle_body = str(compat.get("vehicle_body") or _canonical_body_type(vehicle.body_type, vehicle_kind=vehicle.vehicle_kind))

    vehicle_point = _vehicle_point(vehicle, db, city_cache)
    load_point = _load_pickup_point(load, db, city_cache)
    distance_km: Optional[float] = None

    if vehicle_point and load_point:
        distance_km = round(_haversine_km(vehicle_point[0], vehicle_point[1], load_point[0], load_point[1]), 1)
        if distance_km > float(vehicle.radius_km or 50):
            rejected_reasons.append(
                f"Точка погрузки вне радиуса ({distance_km:.1f} км > {vehicle.radius_km} км)"
            )
            return False, None, rejected_reasons
    else:
        from_city_norm = _norm(load.from_city)
        vehicle_city_norm = _norm(vehicle.location_city)
        region_norm = _norm(vehicle.location_region)
        if from_city_norm == vehicle_city_norm:
            distance_km = 0.0
        elif region_norm and region_norm in from_city_norm:
            distance_km = None
        else:
            rejected_reasons.append("Гео fallback не прошёл: город/регион не совпал")
            return False, None, rejected_reasons

    ai_payload = compute_ai_score(load, stats)
    ai_risk = _norm(ai_payload.get("ai_risk")) or "low"
    ai_score = int(ai_payload.get("ai_score") or 0)
    rate_per_km = _calc_load_rate_per_km(load)

    load_company_id = int(load.user_id or 0)
    if load_company_id <= 0:
        trust_score = 50
    elif load_company_id in trust_cache:
        trust_score = trust_cache[load_company_id]
    else:
        trust_snapshot = get_company_trust_snapshot(db, load_company_id)
        trust_score = int(trust_snapshot.get("trust_score") or trust_snapshot.get("score") or 50)
        trust_cache[load_company_id] = trust_score

    fill_weight_ratio = float(compat.get("fill_weight_ratio") or 0.0)
    fill_volume_ratio = float(compat.get("fill_volume_ratio") or 0.0)
    fill_ratio = max(fill_weight_ratio, fill_volume_ratio)
    fill_score = max(0.0, min(30.0, fill_ratio * 30.0))
    if distance_km is None:
        proximity_score = 50.0
    else:
        proximity_score = max(0.0, 100.0 - float(distance_km))
    profit_score = max(0.0, min(30.0, rate_per_km * 0.2))
    trust_bonus = max(0.0, min(20.0, float(trust_score) / 5.0))
    risk_penalty = _risk_penalty_points(ai_risk)

    score = round(max(0.0, min(100.0, proximity_score + fill_score + profit_score + trust_bonus - risk_penalty)), 1)

    reasons.extend(
        [
            f"Подходит по типу: {vehicle_kind}",
            f"Заполнение: вес {fill_weight_ratio * 100:.0f}% / объём {fill_volume_ratio * 100:.0f}%",
            f"Кузов совместим: {vehicle_body or 'любой'}",
        ]
    )
    if distance_km is not None:
        reasons.append(f"Подача {distance_km:.1f} км в радиусе {vehicle.radius_km} км")
    else:
        reasons.append("Гео fallback: совпадение по городу/региону")
    reasons.append(f"Trust отправителя: {trust_score}/100")
    reasons.append(
        "Скоринг: "
        f"близость {proximity_score:.1f} + заполнение {fill_score:.1f} + "
        f"выгода {profit_score:.1f} + trust {trust_bonus:.1f} - риск {risk_penalty:.1f}"
    )

    required_options = set(compat.get("required_options") or [])
    required_adr_classes = list(compat.get("required_adr_classes") or [])
    required_loading = str(compat.get("required_loading_type") or "")

    compatibility: dict[str, Any] = {
        "reasons": reasons,
    }
    if debug:
        compatibility["blockers"] = []

    payload = {
        "cargo_id": int(load.id),
        "load_id": int(load.id),
        "from_city": load.from_city,
        "to_city": load.to_city,
        "loading_date": load.loading_date.isoformat() if load.loading_date else None,
        "weight_t": round(load_weight, 3),
        "volume_m3": round(load_volume, 3),
        "price": float(load.total_price if load.total_price is not None else (load.price or 0.0)),
        "rate_per_km": rate_per_km,
        "distance_km": distance_km,
        "ai_risk": ai_risk,
        "ai_score": ai_score,
        "trust_score": trust_score,
        "score": score,
        "score_components": {
            "proximity_score": round(proximity_score, 1),
            "fill_score": round(fill_score, 1),
            "profit_score": round(profit_score, 1),
            "trust_bonus": round(trust_bonus, 1),
            "risk_penalty": round(risk_penalty, 1),
            # Backward-compatible aliases
            "proximity": round(proximity_score, 1),
            "fill": round(fill_score, 1),
            "profit": round(profit_score, 1),
            "trust": round(trust_bonus, 1),
            "risk": round(max(0.0, 100.0 - risk_penalty), 1),
        },
        "required_body_type": load.required_body_type,
        "cargo_kind": load.cargo_kind,
        "required_options": {
            "required_vehicle_kinds": sorted(compat.get("required_vehicle_kinds") or []),
            "required_options": sorted(required_options),
            "adr_classes": required_adr_classes,
            "crew_required": bool(load.crew_required),
            "adr": bool(required_adr_classes),
            "temp": bool(load.temp_required or load.temp_min is not None or load.temp_max is not None),
            "loading_type": required_loading or None,
        },
        "compatibility": compatibility,
        "reasons": reasons,
        "explain": reasons,
        "blockers": [],
        "rejected_reasons": [],
        "_sort_key": (
            -score,
            distance_km if distance_km is not None else float(vehicle.radius_km or 50) + 1.0,
            -rate_per_km,
            -trust_score,
            _risk_rank(ai_risk),
            -ai_score,
            int(load.id),
        ),
    }
    return True, payload, rejected_reasons


def _find_matching_cargos(
    *,
    db: Session,
    vehicle: Vehicle,
    limit: int,
    include_rejected: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    stats = MarketStats.from_db(db, lookback_days=60)
    city_cache: dict[int, tuple[float, float] | None] = {}
    trust_cache: dict[int, int] = {}

    query = apply_cargo_status_filter(db.query(Load), "active").order_by(Load.created_at.desc()).limit(800)
    matched: list[dict[str, Any]] = []
    rejected_items: list[dict[str, Any]] = []
    total_candidates = 0

    for load in query.all():
        total_candidates += 1
        ok, payload, rejected_reasons = _vehicle_matches_load(
            vehicle=vehicle,
            load=load,
            db=db,
            city_cache=city_cache,
            stats=stats,
            trust_cache=trust_cache,
            debug=debug,
        )
        if not ok or not payload:
            if include_rejected and rejected_reasons:
                rejected_item = {
                    "cargo_id": int(load.id),
                    "load_id": int(load.id),
                    "from_city": load.from_city,
                    "to_city": load.to_city,
                    "rejected_reasons": rejected_reasons[:4],
                }
                if debug:
                    rejected_item["compatibility"] = {
                        "reasons": [],
                        "blockers": rejected_reasons[:4],
                    }
                rejected_items.append(rejected_item)
            continue
        matched.append(payload)

    matched.sort(key=lambda item: item["_sort_key"])
    for item in matched:
        item.pop("_sort_key", None)
    return {
        "items": matched[:limit],
        "total_matched": len(matched),
        "rejected_items": rejected_items[:limit] if include_rejected else [],
        "total_rejected": len(rejected_items) if include_rejected else 0,
        "total_candidates": total_candidates,
    }


@router.get("/vehicles/meta")
def get_vehicles_meta() -> dict:
    return {
        "vehicle_kinds": [
            {
                "value": value,
                "label": meta["label"],
                "category": meta["category"],
                "body_type": meta["body_type"],
            }
            for value, meta in VEHICLE_KIND_META.items()
        ],
        "loading_types": [
            {"value": value, "label": label}
            for value, label in LOADING_TYPE_LABELS.items()
        ],
        "options": [
            {"value": value, "label": label}
            for value, label in VEHICLE_OPTION_LABELS.items()
        ],
        "adr_classes": [{"value": item, "label": f"Класс {item}"} for item in ADR_CLASSES],
        "crew_sizes": [{"value": size, "label": f"{size} водитель(я)"} for size in range(1, 5)],
    }


@router.get("/vehicles")
def list_vehicles(
    q: Optional[str] = Query(default=None, description="Поиск по названию/госномеру/марке"),
    city: Optional[str] = Query(default=None, description="Город/регион"),
    city_id: Optional[int] = Query(default=None, ge=1),
    vehicle_kind: Optional[str] = Query(default=None, description="Тип транспорта"),
    body_type: Optional[str] = Query(default=None, description="Тип кузова"),
    min_payload_tons: Optional[float] = Query(default=None, ge=0),
    min_capacity_tons: Optional[float] = Query(default=None, ge=0),
    option: Optional[str] = Query(default=None, description="Опции CSV: adr,liftgate"),
    crew_two: bool = Query(default=False, description="Экипаж 2+"),
    available_today: bool = Query(default=False),
    requires_temp: bool = Query(default=False),
    status: str = Query(default="active"),
    scope: str = Query(default="owner", description="owner|all"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=_DEFAULT_LIST_PAGE_SIZE, ge=1, le=_MAX_LIST_PAGE_SIZE),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    status_normalized = _norm(status) or "active"
    scope_normalized = _norm(scope) or "owner"
    if scope_normalized not in _ALLOWED_SCOPE:
        raise HTTPException(status_code=422, detail="scope должен быть owner|all")
    if scope_normalized == "all" and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Просмотр всех машин доступен только администратору")

    query = db.query(Vehicle)
    if not (_is_admin(current_user) and scope_normalized == "all"):
        query = query.filter(_vehicle_owner_filter(current_user.id))

    if status_normalized != "all":
        if status_normalized not in _ALLOWED_STATUSES:
            raise HTTPException(status_code=422, detail="status должен быть active|inactive|archived|all")
        query = query.filter(Vehicle.status == status_normalized)

    if city_id:
        query = query.filter(Vehicle.city_id == city_id)

    if city:
        city_like = f"%{city.strip()}%"
        query = query.filter(or_(Vehicle.location_city.ilike(city_like), Vehicle.location_region.ilike(city_like)))

    if q:
        q_like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Vehicle.name.ilike(q_like),
                Vehicle.plate_number.ilike(q_like),
                Vehicle.brand.ilike(q_like),
                Vehicle.model.ilike(q_like),
                Vehicle.location_city.ilike(q_like),
                Vehicle.location_region.ilike(q_like),
            )
        )

    if vehicle_kind:
        canonical_kind = str(vehicle_kind).strip().upper()
        if canonical_kind not in VEHICLE_KIND_META:
            raise HTTPException(status_code=422, detail="Некорректный vehicle_kind")
        query = query.filter(Vehicle.vehicle_kind == canonical_kind)

    if body_type:
        canonical_body = _canonical_body_type(body_type)
        if not canonical_body:
            raise HTTPException(status_code=422, detail="Некорректный body_type")
        query = query.filter(Vehicle.body_type == canonical_body)

    min_payload = min_payload_tons if min_payload_tons is not None else min_capacity_tons
    if min_payload is not None:
        query = query.filter(func.coalesce(Vehicle.payload_tons, Vehicle.capacity_tons) >= float(min_payload))

    if available_today:
        today = date.today()
        query = query.filter(Vehicle.available_from <= today)
        query = query.filter(or_(Vehicle.available_to.is_(None), Vehicle.available_to >= today))

    if requires_temp:
        query = query.filter(Vehicle.vehicle_kind.in_(list(_TEMP_KINDS)))

    requested_options: list[str] = []
    if option:
        requested_options = _normalize_list(option.split(","), allowed=ALLOWED_VEHICLE_OPTIONS, field_name="option")

    vehicles = query.order_by(Vehicle.created_at.desc(), Vehicle.id.desc()).all()
    if requested_options:
        vehicles = [
            vehicle
            for vehicle in vehicles
            if set(requested_options).issubset(set(_normalize_generic_list(vehicle.options)))
        ]
    if crew_two:
        vehicles = [vehicle for vehicle in vehicles if int(vehicle.crew_size or 1) >= 2]

    total = len(vehicles)
    start = (page - 1) * size
    end = start + size
    items = vehicles[start:end]

    return {
        "items": [_vehicle_to_dict(vehicle, db) for vehicle in items],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/vehicles/{vehicle_id}")
def get_vehicle(
    vehicle_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    vehicle = _require_vehicle_access(vehicle, current_user)
    return _vehicle_to_dict(vehicle, db)


@router.post("/vehicles", status_code=201)
def create_vehicle(
    payload: VehicleCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_user_id = _owner_id_from_payload(payload, current_user)
    owner = db.query(User).filter(User.id == owner_user_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Владелец машины не найден")

    resolved = _resolve_vehicle_payload(payload=payload, db=db, partial=False, existing=None)
    _ensure_plate_unique(db, owner_user_id=owner_user_id, plate_number=resolved["plate_number"])

    ai_report = analyze_vehicle_submission(
        db,
        carrier=owner,
        body_type=resolved["body_type"],
        capacity_tons=float(resolved["payload_tons"] or 0),
        location_city=resolved["location_city"],
        location_region=resolved["location_region"],
        available_from=resolved["available_from"],
        rate_per_km=resolved["rate_per_km"],
    )

    vehicle = Vehicle(
        owner_user_id=owner_user_id,
        carrier_id=owner_user_id,
        name=resolved["name"],
        vehicle_kind=resolved["vehicle_kind"],
        body_type=resolved["body_type"],
        brand=resolved["brand"],
        model=resolved["model"],
        plate_number=resolved["plate_number"],
        vin=resolved["vin"],
        pts_number=resolved["pts_number"],
        payload_tons=resolved["payload_tons"],
        capacity_tons=resolved["capacity_tons"],
        volume_m3=resolved["volume_m3"],
        max_weight_t=resolved["max_weight_t"],
        max_volume_m3=resolved["max_volume_m3"],
        length_m=resolved["length_m"],
        width_m=resolved["width_m"],
        height_m=resolved["height_m"],
        loading_types=resolved["loading_types"],
        options=resolved["options"],
        adr_classes=resolved["adr_classes"],
        crew_size=resolved["crew_size"],
        temp_min=resolved["temp_min"],
        temp_max=resolved["temp_max"],
        city_id=resolved["city_id"],
        start_lat=resolved["start_lat"],
        start_lon=resolved["start_lon"],
        location_city=resolved["location_city"],
        location_region=resolved["location_region"],
        radius_km=resolved["radius_km"],
        available_from=resolved["available_from"],
        available_to=resolved["available_to"],
        rate_per_km=resolved["rate_per_km"],
        status=resolved["status"],
        ai_risk_level=ai_report["risk_level"],
        ai_score=ai_report["score"],
        ai_warnings=ai_report["warnings"],
        ai_market_rate=ai_report["market_rate_per_km"],
        ai_idle_ratio=ai_report["idle_ratio"],
        updated_at=datetime.utcnow(),
    )

    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    _matching_cache_invalidate(vehicle.id)

    return _vehicle_to_dict(vehicle, db, matching_loads=ai_report["matching_loads"])


@router.patch("/vehicles/{vehicle_id}")
def update_vehicle(
    vehicle_id: int,
    payload: VehicleUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина не найдена")

    owner_id = int(vehicle.owner_user_id or vehicle.carrier_id)
    if owner_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    resolved = _resolve_vehicle_payload(payload=payload, db=db, partial=True, existing=vehicle)
    _ensure_plate_unique(db, owner_user_id=owner_id, plate_number=resolved["plate_number"], exclude_vehicle_id=vehicle.id)

    vehicle.name = resolved["name"]
    vehicle.vehicle_kind = resolved["vehicle_kind"]
    vehicle.body_type = resolved["body_type"]
    vehicle.brand = resolved["brand"]
    vehicle.model = resolved["model"]
    vehicle.plate_number = resolved["plate_number"]
    vehicle.vin = resolved["vin"]
    vehicle.pts_number = resolved["pts_number"]
    vehicle.payload_tons = resolved["payload_tons"]
    vehicle.capacity_tons = resolved["capacity_tons"]
    vehicle.volume_m3 = resolved["volume_m3"]
    vehicle.max_weight_t = resolved["max_weight_t"]
    vehicle.max_volume_m3 = resolved["max_volume_m3"]
    vehicle.length_m = resolved["length_m"]
    vehicle.width_m = resolved["width_m"]
    vehicle.height_m = resolved["height_m"]
    vehicle.loading_types = resolved["loading_types"]
    vehicle.options = resolved["options"]
    vehicle.adr_classes = resolved["adr_classes"]
    vehicle.crew_size = resolved["crew_size"]
    vehicle.temp_min = resolved["temp_min"]
    vehicle.temp_max = resolved["temp_max"]
    vehicle.city_id = resolved["city_id"]
    vehicle.start_lat = resolved["start_lat"]
    vehicle.start_lon = resolved["start_lon"]
    vehicle.location_city = resolved["location_city"]
    vehicle.location_region = resolved["location_region"]
    vehicle.radius_km = resolved["radius_km"]
    vehicle.available_from = resolved["available_from"]
    vehicle.available_to = resolved["available_to"]
    vehicle.rate_per_km = resolved["rate_per_km"]
    vehicle.status = resolved["status"]
    vehicle.updated_at = datetime.utcnow()

    owner = db.query(User).filter(User.id == owner_id).first()
    if owner:
        ai_report = analyze_vehicle_submission(
            db,
            carrier=owner,
            body_type=resolved["body_type"],
            capacity_tons=float(resolved["payload_tons"] or 0),
            location_city=resolved["location_city"],
            location_region=resolved["location_region"],
            available_from=resolved["available_from"],
            rate_per_km=resolved["rate_per_km"],
        )
        vehicle.ai_risk_level = ai_report["risk_level"]
        vehicle.ai_score = ai_report["score"]
        vehicle.ai_warnings = ai_report["warnings"]
        vehicle.ai_market_rate = ai_report["market_rate_per_km"]
        vehicle.ai_idle_ratio = ai_report["idle_ratio"]

    db.commit()
    db.refresh(vehicle)
    _matching_cache_invalidate(vehicle.id)
    return _vehicle_to_dict(vehicle, db)


@router.patch("/vehicles/{vehicle_id}/status")
def update_vehicle_status(
    vehicle_id: int,
    payload: VehicleStatusUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина не найдена")

    owner_id = int(vehicle.owner_user_id or vehicle.carrier_id)
    if owner_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    vehicle.status = payload.status
    vehicle.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(vehicle)
    _matching_cache_invalidate(vehicle.id)
    return _vehicle_to_dict(vehicle, db)


@router.get("/vehicles/{vehicle_id}/matching-cargos")
def get_vehicle_matching_cargos(
    vehicle_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    include_rejected: bool = Query(default=False),
    debug: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    vehicle = _require_vehicle_access(vehicle, current_user)

    cache_filters = {
        "limit": limit,
        "include_rejected": include_rejected,
        "debug": debug,
        "vehicle_updated_at": vehicle.updated_at.isoformat() if vehicle.updated_at else None,
        "vehicle_status": vehicle.status,
    }
    cache_key = _matching_cache_key(vehicle_id=vehicle.id, filters=cache_filters)
    cached_payload = _matching_cache_get(cache_key)
    if cached_payload is not None:
        cached_payload["cache_hit"] = True
        return cached_payload

    matching_payload = _find_matching_cargos(
        db=db,
        vehicle=vehicle,
        limit=limit,
        include_rejected=include_rejected,
        debug=debug,
    )
    response_payload = {
        "vehicle_id": vehicle.id,
        "total_matched": int(matching_payload["total_matched"]),
        "total_candidates": int(matching_payload["total_candidates"]),
        "total_rejected": int(matching_payload["total_rejected"]),
        "items": matching_payload["items"],
        "rejected_items": matching_payload["rejected_items"],
        "generated_at": datetime.utcnow().isoformat(),
        "cache_hit": False,
    }
    _matching_cache_set(cache_key, response_payload)
    return {
        **response_payload,
    }


@router.get("/vehicles/{vehicle_id}/matching-loads")
def get_vehicle_matching_loads(
    vehicle_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    include_rejected: bool = Query(default=False),
    debug: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Legacy alias для обратной совместимости."""
    payload = get_vehicle_matching_cargos(
        vehicle_id=vehicle_id,
        limit=limit,
        include_rejected=include_rejected,
        debug=debug,
        current_user=current_user,
        db=db,
    )
    sample_loads = [
        {
            "id": item["load_id"],
            "from_city": item["from_city"],
            "to_city": item["to_city"],
            "weight": item["weight_t"],
            "price": item["price"],
        }
        for item in payload["items"][:5]
    ]
    payload["matching_loads_count"] = payload["total_matched"]
    payload["sample_loads"] = sample_loads
    return payload
