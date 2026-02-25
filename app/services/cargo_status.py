from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models.models import CargoStatus, Load


ACTIVE_STATUSES = (CargoStatus.active.value, "open")
EXPIRED_STATUSES = (CargoStatus.expired.value,)
CLOSED_STATUSES = (CargoStatus.closed.value, "covered")
CANCELLED_STATUSES = (CargoStatus.cancelled.value,)

_STATUS_ALIASES = {
    "open": CargoStatus.active.value,
    "active": CargoStatus.active.value,
    "expired": CargoStatus.expired.value,
    "closed": CargoStatus.closed.value,
    "covered": CargoStatus.closed.value,
    "cancelled": CargoStatus.cancelled.value,
}


def normalize_cargo_status(value: Any, *, default: str = CargoStatus.active.value) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw == "all":
        return "all"
    return _STATUS_ALIASES.get(raw, default)


def is_active_status(value: Any) -> bool:
    return normalize_cargo_status(value, default="") == CargoStatus.active.value


def is_expired_status(value: Any) -> bool:
    return normalize_cargo_status(value, default="") == CargoStatus.expired.value


def is_terminal_status(value: Any) -> bool:
    status = normalize_cargo_status(value, default="")
    return status in {CargoStatus.expired.value, CargoStatus.closed.value, CargoStatus.cancelled.value}


def cargo_loading_date(load: Load) -> date | None:
    direct = getattr(load, "loading_date", None)
    if isinstance(direct, date):
        return direct

    created_at = getattr(load, "created_at", None)
    if isinstance(created_at, datetime):
        return created_at.date()
    if isinstance(created_at, date):
        return created_at
    return None


def apply_cargo_status_filter(query: Query, status: str | None, *, default: str = CargoStatus.active.value) -> Query:
    normalized = normalize_cargo_status(status, default=default)
    if normalized == "all":
        return query
    if normalized == CargoStatus.active.value:
        return query.filter(or_(Load.status.is_(None), Load.status.in_(ACTIVE_STATUSES)))
    if normalized == CargoStatus.expired.value:
        return query.filter(Load.status.in_(EXPIRED_STATUSES))
    if normalized == CargoStatus.closed.value:
        return query.filter(Load.status.in_(CLOSED_STATUSES))
    if normalized == CargoStatus.cancelled.value:
        return query.filter(Load.status.in_(CANCELLED_STATUSES))
    return query.filter(or_(Load.status.is_(None), Load.status.in_(ACTIVE_STATUSES)))


def expire_outdated_cargos(db: Session) -> int:
    today = date.today()
    updated = (
        db.query(Load)
        .filter(Load.status.in_(ACTIVE_STATUSES))
        .filter(Load.loading_date.isnot(None))
        .filter(Load.loading_date < today)
        .update({Load.status: CargoStatus.expired.value}, synchronize_session=False)
    )
    if updated:
        db.commit()
    return int(updated or 0)
