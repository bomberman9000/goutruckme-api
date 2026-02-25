from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.antifraud.normalize import norm_inn, norm_name, norm_phone
from app.models.models import CounterpartyList, FraudComponent, FraudEdge, FraudEntity, FraudEntityComponent, FraudSignal


_BASE_ENTITY_RISK = {
    "inn": 1,
    "phone": 2,
    "email": 2,
    "card": 4,
    "bank_account": 4,
    "ip": 3,
    "device": 3,
    "name": 1,
    "deal": 0,
}


def _norm_email(value: str | None) -> str | None:
    email = str(value or "").strip().lower()
    return email or None


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _neighbors_with_weight(db: Session, entity_id: int) -> list[tuple[int, int]]:
    rows = (
        db.query(FraudEdge)
        .filter(or_(FraudEdge.src_entity_id == int(entity_id), FraudEdge.dst_entity_id == int(entity_id)))
        .all()
    )

    out: list[tuple[int, int]] = []
    for row in rows:
        src = int(row.src_entity_id)
        dst = int(row.dst_entity_id)
        weight = max(_to_int(row.weight, 1), 1)
        if src == int(entity_id) and dst > 0:
            out.append((dst, weight))
        elif dst == int(entity_id) and src > 0:
            out.append((src, weight))
    return out


def _entity_signal_risk(db: Session, entity_id: int) -> int:
    rows = db.query(FraudSignal.severity).filter(FraudSignal.entity_id == int(entity_id)).all()
    total = sum(_to_int(item[0], 0) for item in rows)
    return min(total * 2, 60)


def _calc_entity_risk(db: Session, entity: FraudEntity) -> float:
    base_risk = float(_BASE_ENTITY_RISK.get(str(entity.entity_type), 1))
    signal_risk = float(_entity_signal_risk(db, int(entity.id)))

    propagation = 0.0
    visited: set[int] = {int(entity.id)}
    frontier: list[tuple[int, int, int]] = [(int(entity.id), 0, 10)]  # (node, hop, incoming_weight)

    while frontier:
        current_id, hop, incoming_weight = frontier.pop(0)
        if hop >= 2:
            continue

        for neighbor_id, edge_weight in _neighbors_with_weight(db, current_id):
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)

            next_hop = hop + 1
            neighbor_signal = float(_entity_signal_risk(db, neighbor_id))
            hop_factor = 1.0 / float(max(next_hop, 1))
            edge_factor = float(max(edge_weight, incoming_weight, 1)) / 10.0
            propagation += neighbor_signal * hop_factor * edge_factor

            frontier.append((neighbor_id, next_hop, edge_weight))

    total = base_risk + signal_risk + propagation
    return max(0.0, min(total, 100.0))


async def get_counterparty_network_risk(
    db: Session,
    inn: str | None,
    phone: str | None,
    email: str | None,
    name: str | None,
) -> dict[str, Any]:
    candidates: list[tuple[str, str]] = []

    normalized_inn = norm_inn(inn)
    normalized_phone = norm_phone(phone)
    normalized_email = _norm_email(email)
    normalized_name = norm_name(name) or None

    if normalized_inn:
        candidates.append(("inn", normalized_inn))
    if normalized_phone:
        candidates.append(("phone", normalized_phone))
    if normalized_email:
        candidates.append(("email", normalized_email))
    if normalized_name:
        candidates.append(("name", normalized_name))

    if not candidates:
        return {
            "entity_risks": [],
            "component_key": None,
            "component_risk": 0,
            "connected_blacklist": False,
            "top_signals": [],
        }

    filters = [
        (FraudEntity.entity_type == entity_type) & (FraudEntity.entity_value == entity_value)
        for entity_type, entity_value in candidates
    ]
    entities = db.query(FraudEntity).filter(or_(*filters)).all()

    if not entities:
        return {
            "entity_risks": [],
            "component_key": None,
            "component_risk": 0,
            "connected_blacklist": False,
            "top_signals": [],
        }

    entity_risks: list[dict[str, Any]] = []
    for entity in entities:
        entity_risks.append(
            {
                "type": entity.entity_type,
                "value": entity.entity_value,
                "risk": round(_calc_entity_risk(db, entity), 3),
            }
        )

    mapping_rows = (
        db.query(FraudEntityComponent, FraudComponent)
        .join(FraudComponent, FraudEntityComponent.component_id == FraudComponent.id)
        .filter(FraudEntityComponent.entity_id.in_([int(item.id) for item in entities]))
        .all()
    )

    component_key: str | None = None
    component_risk = 0
    component_entity_ids: list[int] = [int(item.id) for item in entities]

    if mapping_rows:
        # pick highest risk component if multiple
        best = max(mapping_rows, key=lambda pair: int(pair[1].risk_score or 0))
        component_row = best[1]
        component_key = component_row.component_key
        component_risk = int(component_row.risk_score or 0)

        component_entity_ids = [
            int(row.entity_id)
            for row in db.query(FraudEntityComponent.entity_id)
            .filter(FraudEntityComponent.component_id == int(component_row.id))
            .all()
        ]

    component_entities = db.query(FraudEntity).filter(FraudEntity.id.in_(component_entity_ids)).all()
    component_inn = {row.entity_value for row in component_entities if row.entity_type == "inn"}
    component_phone = {row.entity_value for row in component_entities if row.entity_type == "phone"}
    component_name = {row.entity_value for row in component_entities if row.entity_type == "name"}

    blacklist = db.query(CounterpartyList).filter(CounterpartyList.list_type == "black").all()
    black_inn = {str(row.inn or "").strip() for row in blacklist if row.inn}
    black_phone = {str(row.phone or "").strip() for row in blacklist if row.phone}
    black_name = {str(row.name or "").strip() for row in blacklist if row.name}

    connected_blacklist = bool(
        component_inn.intersection(black_inn)
        or component_phone.intersection(black_phone)
        or component_name.intersection(black_name)
    )

    signal_rows = (
        db.query(FraudSignal)
        .filter(FraudSignal.entity_id.in_(component_entity_ids))
        .order_by(FraudSignal.severity.desc(), FraudSignal.created_at.desc())
        .limit(5)
        .all()
    )
    top_signals = [
        {
            "signal_type": row.signal_type,
            "severity": int(row.severity or 0),
            "payload": row.payload or {},
        }
        for row in signal_rows
    ]

    entity_risks.sort(key=lambda item: float(item.get("risk") or 0.0), reverse=True)

    return {
        "entity_risks": entity_risks,
        "component_key": component_key,
        "component_risk": component_risk,
        "connected_blacklist": connected_blacklist,
        "top_signals": top_signals,
    }
