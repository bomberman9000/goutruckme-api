from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import (
    Deal,
    DealSync,
    Document,
    DocumentSync,
    Load,
    ModerationReview,
    User,
    Vehicle,
)

router = APIRouter()
_ALLOWED_RANGES = {"day": 1, "week": 7, "month": 30}


def _normalize_role(role: Any) -> str:
    raw = role.value if hasattr(role, "value") else role
    value = str(raw or "").strip().lower()
    if value.startswith("userrole."):
        value = value.split(".", 1)[1]
    if value == "shipper":
        return "client"
    if value == "expeditor":
        return "forwarder"
    if value in {"carrier", "client", "forwarder", "admin"}:
        return value
    return "forwarder"


def _parse_range(range_value: str) -> tuple[str, datetime]:
    normalized = (range_value or "").strip().lower()
    if normalized not in _ALLOWED_RANGES:
        raise HTTPException(status_code=422, detail="range должен быть day, week или month")
    return normalized, datetime.utcnow() - timedelta(days=_ALLOWED_RANGES[normalized])


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    as_float = _safe_float(value)
    if as_float is None:
        return None
    try:
        return int(as_float)
    except (TypeError, ValueError):
        return None


def _deals_query_for_user(db: Session, user: User):
    role = _normalize_role(user.role)
    query = db.query(Deal)
    if role == "admin":
        return query
    if role == "carrier":
        return query.filter(Deal.carrier_id == user.id)
    if role == "client":
        return query.filter(Deal.shipper_id == user.id)
    return query.filter(or_(Deal.shipper_id == user.id, Deal.carrier_id == user.id))


def _loads_query_for_user(db: Session, user: User):
    role = _normalize_role(user.role)
    query = db.query(Load)
    if role == "admin":
        return query
    if role == "client":
        return query.filter(Load.user_id == user.id)
    if role == "carrier":
        return query.join(Deal, Deal.cargo_id == Load.id).filter(Deal.carrier_id == user.id).distinct()
    return (
        query.outerjoin(Deal, Deal.cargo_id == Load.id)
        .filter(
            or_(
                Load.user_id == user.id,
                Deal.shipper_id == user.id,
                Deal.carrier_id == user.id,
            )
        )
        .distinct()
    )


def _extract_user_ids_from_payload(payload: Any) -> set[int]:
    if not isinstance(payload, dict):
        return set()

    candidates: set[int] = set()

    direct_keys = {
        "user_id",
        "userId",
        "shipper_id",
        "shipperId",
        "client_id",
        "clientId",
        "carrier_id",
        "carrierId",
        "counterparty_id",
        "counterpartyId",
        "owner_id",
        "ownerId",
    }
    for key in direct_keys:
        value = payload.get(key)
        maybe_id = _safe_int(value)
        if maybe_id is not None:
            candidates.add(maybe_id)

    nested_keys = {"shipper", "client", "carrier", "counterparty", "owner", "user"}
    for key in nested_keys:
        nested = payload.get(key)
        if isinstance(nested, dict):
            for nested_id_key in ("id", "user_id", "userId"):
                maybe_id = _safe_int(nested.get(nested_id_key))
                if maybe_id is not None:
                    candidates.add(maybe_id)

    return candidates


def _extract_cargo_id_from_payload(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ("cargo_id", "cargoId", "load_id", "loadId"):
        maybe_id = _safe_int(payload.get(key))
        if maybe_id is not None:
            return maybe_id

    cargo_snapshot = payload.get("cargoSnapshot")
    if isinstance(cargo_snapshot, dict):
        maybe_id = _safe_int(cargo_snapshot.get("id"))
        if maybe_id is not None:
            return maybe_id

    return None


def _is_dealsync_visible(payload: Any, user_id: int, role: str, load_owner_map: dict[int, int]) -> bool:
    if role == "admin":
        return True

    linked_user_ids = _extract_user_ids_from_payload(payload)
    if user_id in linked_user_ids:
        return True

    cargo_id = _extract_cargo_id_from_payload(payload)
    cargo_owner_id = load_owner_map.get(cargo_id) if cargo_id is not None else None
    if cargo_owner_id == user_id and role in {"client", "forwarder"}:
        return True

    return False


def _build_visible_sync_ids(db: Session, user: User) -> tuple[set[int], set[int]]:
    role = _normalize_role(user.role)
    if role == "admin":
        all_deal_ids = {row[0] for row in db.query(DealSync.id).all()}
        all_document_ids = {row[0] for row in db.query(DocumentSync.id).all()}
        return all_deal_ids, all_document_ids

    deal_rows = db.query(DealSync.id, DealSync.payload).all()
    if not deal_rows:
        return set(), set()

    cargo_ids: set[int] = set()
    for _, payload in deal_rows:
        cargo_id = _extract_cargo_id_from_payload(payload)
        if cargo_id is not None:
            cargo_ids.add(cargo_id)

    load_owner_map: dict[int, int] = {}
    if cargo_ids:
        for load_id, owner_id in db.query(Load.id, Load.user_id).filter(Load.id.in_(cargo_ids)).all():
            if owner_id is not None:
                load_owner_map[int(load_id)] = int(owner_id)

    visible_deal_ids: set[int] = set()
    for deal_id, payload in deal_rows:
        if _is_dealsync_visible(payload, user.id, role, load_owner_map):
            visible_deal_ids.add(int(deal_id))

    if not visible_deal_ids:
        return set(), set()

    visible_document_ids = {
        int(row[0])
        for row in db.query(DocumentSync.id)
        .filter(DocumentSync.deal_server_id.in_(visible_deal_ids))
        .all()
    }
    return visible_deal_ids, visible_document_ids


def _extract_flags(flags: Any) -> list[str]:
    if flags is None:
        return []
    if isinstance(flags, dict):
        return [str(key) for key, value in flags.items() if bool(value)]
    if isinstance(flags, (list, tuple, set)):
        return [str(item) for item in flags if item not in (None, "")]
    if isinstance(flags, str) and flags.strip():
        return [flags.strip()]
    return []


@router.get("/analytics/overview")
def analytics_overview(
    range: str = Query(default="week", alias="range"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    normalized_range, since = _parse_range(range)

    loads_query = _loads_query_for_user(db, current_user)
    deals_query = _deals_query_for_user(db, current_user)

    cargos_new = loads_query.filter(and_(Load.created_at.isnot(None), Load.created_at >= since)).count()

    deals_in_range = deals_query.filter(and_(Deal.created_at.isnot(None), Deal.created_at >= since))
    deals_created = deals_in_range.count()

    closed_statuses = ["closed", "completed", "contracted"]
    deals_closed = deals_in_range.filter(
        func.lower(func.coalesce(Deal.status, "")).in_(closed_statuses)
    ).count()

    responses_new = deals_in_range.filter(Deal.carrier_message.isnot(None)).count()

    first_response_candidates = (
        deals_query.filter(and_(Deal.created_at.isnot(None), Deal.created_at >= since, Deal.cargo_id.isnot(None)))
        .with_entities(Deal.cargo_id, Deal.created_at)
        .all()
    )

    first_by_cargo: dict[int, datetime] = {}
    for cargo_id, created_at in first_response_candidates:
        if cargo_id is None or created_at is None:
            continue
        cargo_id = int(cargo_id)
        existing = first_by_cargo.get(cargo_id)
        if existing is None or created_at < existing:
            first_by_cargo[cargo_id] = created_at

    deltas_min: list[float] = []
    if first_by_cargo:
        load_rows = (
            loads_query.filter(Load.id.in_(list(first_by_cargo.keys())))
            .with_entities(Load.id, Load.created_at)
            .all()
        )
        for load_id, load_created_at in load_rows:
            if load_created_at is None:
                continue
            response_at = first_by_cargo.get(int(load_id))
            if not response_at:
                continue
            delta = (response_at - load_created_at).total_seconds() / 60.0
            if delta >= 0:
                deltas_min.append(delta)

    time_to_first_response_min = round(mean(deltas_min), 1) if deltas_min else None

    conversion = round(deals_created / max(cargos_new, 1), 4)

    return {
        "range": normalized_range,
        "kpis": {
            "cargos_new": cargos_new,
            "responses_new": responses_new,
            "deals_created": deals_created,
            "deals_closed": deals_closed,
            "conversion": conversion,
            "time_to_first_response_min": time_to_first_response_min,
        },
    }


@router.get("/analytics/money")
def analytics_money(
    range: str = Query(default="week", alias="range"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, since = _parse_range(range)
    role = _normalize_role(current_user.role)

    deals_query = _deals_query_for_user(db, current_user).filter(
        and_(Deal.created_at.isnot(None), Deal.created_at >= since)
    )

    if role == "admin":
        vehicle_rate_query = db.query(Vehicle.rate_per_km).filter(Vehicle.rate_per_km.isnot(None))
    elif role == "carrier":
        vehicle_rate_query = db.query(Vehicle.rate_per_km).filter(
            Vehicle.rate_per_km.isnot(None), Vehicle.carrier_id == current_user.id
        )
    else:
        carrier_ids = [
            row[0]
            for row in deals_query.with_entities(Deal.carrier_id).filter(Deal.carrier_id.isnot(None)).distinct().all()
        ]
        if not carrier_ids:
            vehicle_rate_query = None
        else:
            vehicle_rate_query = db.query(Vehicle.rate_per_km).filter(
                Vehicle.rate_per_km.isnot(None),
                Vehicle.carrier_id.in_(carrier_ids),
            )

    vehicle_rates = (
        [float(row[0]) for row in vehicle_rate_query.all() if row[0] is not None]
        if vehicle_rate_query is not None
        else []
    )
    rate_per_km_avg = round(mean(vehicle_rates), 2) if vehicle_rates else None

    deals_for_top = deals_query.with_entities(Deal.shipper_id, Deal.cargo_id).all()
    revenue_by_client: dict[int, float] = {}
    cargo_ids = {int(cargo_id) for _, cargo_id in deals_for_top if cargo_id is not None}

    load_price_map: dict[int, float] = {}
    if cargo_ids:
        for load_id, price in db.query(Load.id, Load.price).filter(Load.id.in_(cargo_ids)).all():
            load_price_map[int(load_id)] = float(price or 0)

    for shipper_id, cargo_id in deals_for_top:
        if shipper_id is None or cargo_id is None:
            continue
        shipper_id = int(shipper_id)
        revenue_by_client[shipper_id] = revenue_by_client.get(shipper_id, 0.0) + load_price_map.get(int(cargo_id), 0.0)

    top_clients = []
    if revenue_by_client:
        client_ids = list(revenue_by_client.keys())
        user_rows = (
            db.query(User.id, User.organization_name, User.company, User.fullname, User.phone)
            .filter(User.id.in_(client_ids))
            .all()
        )
        names_by_id = {
            int(user_id): (organization_name or company or fullname or phone or f"user_{user_id}")
            for user_id, organization_name, company, fullname, phone in user_rows
        }
        top_clients = [
            {
                "id": client_id,
                "name": names_by_id.get(client_id, f"user_{client_id}"),
                "revenue": round(revenue, 2),
            }
            for client_id, revenue in sorted(
                revenue_by_client.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
        ]

    margin_avg = None
    top_routes: list[dict[str, Any]] = []
    if hasattr(Deal, "client_price") and hasattr(Deal, "carrier_price"):
        margin_values = []
        for deal in deals_query.all():
            client_price = _safe_float(getattr(deal, "client_price", None))
            carrier_price = _safe_float(getattr(deal, "carrier_price", None))
            if client_price is None or carrier_price is None:
                continue
            margin_values.append(client_price - carrier_price)

        if margin_values:
            margin_avg = round(mean(margin_values), 2)

            route_sums: dict[tuple[str, str], list[float]] = {}
            for deal in deals_query.all():
                client_price = _safe_float(getattr(deal, "client_price", None))
                carrier_price = _safe_float(getattr(deal, "carrier_price", None))
                if client_price is None or carrier_price is None:
                    continue
                load = db.query(Load).filter(Load.id == deal.cargo_id).first()
                if not load:
                    continue
                key = (load.from_city or "", load.to_city or "")
                route_sums.setdefault(key, []).append(client_price - carrier_price)

            top_routes = [
                {
                    "from_city": from_city,
                    "to_city": to_city,
                    "margin": round(mean(values), 2),
                }
                for (from_city, to_city), values in sorted(
                    route_sums.items(),
                    key=lambda item: mean(item[1]),
                    reverse=True,
                )[:5]
            ]

    return {
        "rate_per_km_avg": rate_per_km_avg,
        "margin_avg": margin_avg,
        "top_clients": top_clients,
        "top_routes": top_routes,
    }


@router.get("/analytics/risk")
def analytics_risk(
    range: str = Query(default="week", alias="range"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, since = _parse_range(range)
    role = _normalize_role(current_user.role)

    rows = (
        db.query(ModerationReview)
        .filter(and_(ModerationReview.created_at.isnot(None), ModerationReview.created_at >= since))
        .order_by(ModerationReview.created_at.desc())
        .all()
    )

    if role != "admin":
        visible_deal_ids, visible_document_ids = _build_visible_sync_ids(db, current_user)
        rows = [
            row
            for row in rows
            if (
                (row.entity_type == "deal" and row.entity_id in visible_deal_ids)
                or (row.entity_type == "document" and row.entity_id in visible_document_ids)
            )
        ]

    counts = {"low": 0, "medium": 0, "high": 0}
    flags_counter: Counter[str] = Counter()

    for row in rows:
        level = (row.risk_level or "").strip().lower()
        if level in counts:
            counts[level] += 1
        for flag in _extract_flags(row.flags):
            flags_counter[flag] += 1

    items = [
        {
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "risk_level": row.risk_level,
            "comment": row.comment,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows[:20]
    ]

    return {
        "counts": counts,
        "top_flags": [
            {"flag": flag, "count": count}
            for flag, count in flags_counter.most_common(10)
        ],
        "items": items,
    }


@router.get("/analytics/data_quality")
def analytics_data_quality(
    range: str = Query(default="week", alias="range"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, since = _parse_range(range)
    role = _normalize_role(current_user.role)

    if role == "admin":
        duplicate_companies = db.query(User.organization_name).filter(
            and_(User.organization_name.isnot(None), User.organization_name != "")
        ).group_by(User.organization_name).having(func.count(User.id) > 1).count()
    else:
        duplicate_companies = 0

    loads_query = _loads_query_for_user(db, current_user).filter(
        and_(Load.created_at.isnot(None), Load.created_at >= since)
    )

    cargos_missing_city = loads_query.filter(
        or_(
            Load.from_city.is_(None),
            Load.from_city == "",
            Load.to_city.is_(None),
            Load.to_city == "",
        )
    ).count()

    cargos_missing_price = loads_query.filter(or_(Load.price.is_(None), Load.price <= 0)).count()

    deals_query = _deals_query_for_user(db, current_user).filter(
        and_(Deal.created_at.isnot(None), Deal.created_at >= since)
    )
    deals_missing_parties = deals_query.filter(
        or_(Deal.shipper_id.is_(None), Deal.carrier_id.is_(None))
    ).count()

    duplicate_documents = 0
    if role == "admin":
        duplicate_hashes = (
            db.query(DocumentSync.file_hash, func.count(DocumentSync.id))
            .filter(
                and_(
                    DocumentSync.file_hash.isnot(None),
                    DocumentSync.file_hash != "",
                    DocumentSync.created_at.isnot(None),
                    DocumentSync.created_at >= since,
                )
            )
            .group_by(DocumentSync.file_hash)
            .having(func.count(DocumentSync.id) > 1)
            .all()
        )
        duplicate_documents += int(sum(count - 1 for _, count in duplicate_hashes))

        duplicate_paths = (
            db.query(Document.pdf_path, func.count(Document.id))
            .filter(
                and_(
                    Document.pdf_path.isnot(None),
                    Document.pdf_path != "",
                    Document.created_at.isnot(None),
                    Document.created_at >= since,
                )
            )
            .group_by(Document.pdf_path)
            .having(func.count(Document.id) > 1)
            .all()
        )
        duplicate_documents += int(sum(count - 1 for _, count in duplicate_paths))
    else:
        _, visible_document_ids = _build_visible_sync_ids(db, current_user)
        if visible_document_ids:
            duplicate_hashes = (
                db.query(DocumentSync.file_hash, func.count(DocumentSync.id))
                .filter(
                    and_(
                        DocumentSync.id.in_(visible_document_ids),
                        DocumentSync.file_hash.isnot(None),
                        DocumentSync.file_hash != "",
                        DocumentSync.created_at.isnot(None),
                        DocumentSync.created_at >= since,
                    )
                )
                .group_by(DocumentSync.file_hash)
                .having(func.count(DocumentSync.id) > 1)
                .all()
            )
            duplicate_documents += int(sum(count - 1 for _, count in duplicate_hashes))

    return {
        "duplicate_companies": int(duplicate_companies or 0),
        "missing_fields": {
            "cargos_missing_city": int(cargos_missing_city or 0),
            "cargos_missing_price": int(cargos_missing_price or 0),
            "deals_missing_parties": int(deals_missing_parties or 0),
        },
        "duplicate_documents": int(duplicate_documents or 0),
    }
