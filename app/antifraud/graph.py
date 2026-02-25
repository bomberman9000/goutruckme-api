from __future__ import annotations

import hashlib
import math
import time
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.models import CounterpartyList, FraudComponent, FraudEdge, FraudEntity, FraudEntityComponent, FraudSignal


_COMPONENT_CACHE_TTL_SEC = 600

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


class ComponentCache:
    def __init__(self) -> None:
        self._storage: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, component_key: str) -> dict[str, Any] | None:
        payload = self._storage.get(component_key)
        if not payload:
            return None

        expires_at, value = payload
        if expires_at <= time.time():
            self._storage.pop(component_key, None)
            return None

        return dict(value)

    def set(self, component_key: str, value: dict[str, Any], ttl_sec: int = _COMPONENT_CACHE_TTL_SEC) -> None:
        self._storage[component_key] = (time.time() + max(int(ttl_sec), 1), dict(value))

    def clear(self) -> None:
        self._storage.clear()

    def size(self) -> int:
        return len(self._storage)


component_cache = ComponentCache()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _component_key(entity_ids: set[int]) -> str:
    sorted_ids = sorted(int(item) for item in entity_ids if int(item) > 0)
    prefix = ",".join(str(item) for item in sorted_ids[:200])
    raw = f"{prefix}|size={len(sorted_ids)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _neighbors(db: Session, entity_id: int) -> set[int]:
    rows = (
        db.query(FraudEdge)
        .filter(or_(FraudEdge.src_entity_id == int(entity_id), FraudEdge.dst_entity_id == int(entity_id)))
        .all()
    )

    result: set[int] = set()
    for row in rows:
        src = int(row.src_entity_id)
        dst = int(row.dst_entity_id)
        if src == int(entity_id) and dst > 0:
            result.add(dst)
        elif dst == int(entity_id) and src > 0:
            result.add(src)
    return result


def _compute_component_stats(db: Session, entity_ids: set[int]) -> dict[str, int]:
    if not entity_ids:
        return {"size": 0, "risk_score": 0, "high_risk_nodes": 0}

    rows = db.query(FraudEntity).filter(FraudEntity.id.in_(list(entity_ids))).all()
    by_id = {int(row.id): row for row in rows}

    signals = (
        db.query(FraudSignal.entity_id, FraudSignal.severity)
        .filter(FraudSignal.entity_id.in_(list(entity_ids)))
        .all()
    )
    signal_sum: dict[int, int] = {}
    for entity_id, severity in signals:
        key = int(entity_id or 0)
        if key <= 0:
            continue
        signal_sum[key] = signal_sum.get(key, 0) + _to_int(severity, 0)

    blacklist = db.query(CounterpartyList).filter(CounterpartyList.list_type == "black").all()
    black_inn = {str(row.inn or "").strip() for row in blacklist if row.inn}
    black_phone = {str(row.phone or "").strip() for row in blacklist if row.phone}
    black_name = {str(row.name or "").strip() for row in blacklist if row.name}

    high_risk_nodes = 0
    risk_raw_sum = 0

    for entity_id in entity_ids:
        row = by_id.get(int(entity_id))
        if not row:
            continue

        base_risk = _to_int(_BASE_ENTITY_RISK.get(str(row.entity_type), 1), 1)
        signal_risk = _to_int(signal_sum.get(int(entity_id), 0), 0) * 2

        blacklist_bonus = 0
        if row.entity_type == "inn" and row.entity_value in black_inn:
            blacklist_bonus = 30
        elif row.entity_type == "phone" and row.entity_value in black_phone:
            blacklist_bonus = 30
        elif row.entity_type == "name" and row.entity_value in black_name:
            blacklist_bonus = 20

        node_risk = min(base_risk + signal_risk + blacklist_bonus, 100)
        if node_risk >= 70:
            high_risk_nodes += 1

        risk_raw_sum += base_risk + signal_risk + blacklist_bonus

    component_size = len(entity_ids)
    risk_score = min(max(_to_int(risk_raw_sum, 0), 0), 100)
    return {
        "size": component_size,
        "risk_score": risk_score,
        "high_risk_nodes": high_risk_nodes,
    }


async def rebuild_components_incremental(db: Session, starting_entity_ids: list[int]) -> None:
    start_ids = [int(item) for item in starting_entity_ids if int(item) > 0]
    if not start_ids:
        return

    visited: set[int] = set()

    for start in start_ids:
        if start in visited:
            continue

        component_ids: set[int] = set()
        stack = [start]
        while stack:
            entity_id = int(stack.pop())
            if entity_id in component_ids:
                continue

            component_ids.add(entity_id)
            visited.add(entity_id)
            for neighbor in _neighbors(db, entity_id):
                if neighbor not in component_ids:
                    stack.append(neighbor)

        if not component_ids:
            continue

        component_key = _component_key(component_ids)
        stats = _compute_component_stats(db, component_ids)

        row = db.query(FraudComponent).filter(FraudComponent.component_key == component_key).first()
        if not row:
            row = FraudComponent(component_key=component_key)
            db.add(row)
            db.flush()

        row.size = int(stats["size"])
        row.risk_score = int(stats["risk_score"])
        row.high_risk_nodes = int(stats["high_risk_nodes"])
        row.updated_at = datetime.utcnow()

        mappings = (
            db.query(FraudEntityComponent)
            .filter(FraudEntityComponent.entity_id.in_(list(component_ids)))
            .all()
        )
        map_by_entity = {int(item.entity_id): item for item in mappings}

        for entity_id in component_ids:
            mapping = map_by_entity.get(int(entity_id))
            if not mapping:
                mapping = FraudEntityComponent(entity_id=int(entity_id), component_id=int(row.id))
                db.add(mapping)
            mapping.component_id = int(row.id)
            mapping.updated_at = datetime.utcnow()

        snapshot = {
            "component_key": component_key,
            "size": int(row.size or 0),
            "risk_score": int(row.risk_score or 0),
            "high_risk_nodes": int(row.high_risk_nodes or 0),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        component_cache.set(component_key, snapshot)

    db.commit()


async def rebuild_components_full(db: Session) -> dict[str, Any]:
    entity_ids = [int(row.id) for row in db.query(FraudEntity.id).all() if int(row.id) > 0]
    await rebuild_components_incremental(db, entity_ids)

    total_components = int(db.query(FraudComponent.id).count() or 0)
    total_entities = len(entity_ids)
    return {
        "entities_total": total_entities,
        "components_total": total_components,
    }


def get_component_cached_snapshot(component_key: str) -> dict[str, Any] | None:
    return component_cache.get(component_key)
