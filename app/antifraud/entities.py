from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.antifraud.normalize import norm_inn, norm_name, norm_phone
from app.models.models import FraudEdge, FraudEntity


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DIGITS_RE = re.compile(r"\D")

_ENTITY_WEIGHT = {
    "inn": 8,
    "phone": 6,
    "email": 4,
    "card": 9,
    "bank_account": 8,
    "ip": 5,
    "device": 5,
    "name": 2,
}


def _norm_email(value: str | None) -> str | None:
    email = str(value or "").strip().lower()
    if not email or not _EMAIL_RE.match(email):
        return None
    return email


def _norm_card(value: str | None) -> str | None:
    digits = _DIGITS_RE.sub("", str(value or ""))
    if len(digits) < 12:
        return None
    if len(digits) > 19:
        digits = digits[-19:]
    return digits


def _norm_bank_account(value: str | None) -> str | None:
    digits = _DIGITS_RE.sub("", str(value or ""))
    if len(digits) < 10:
        return None
    if len(digits) > 32:
        digits = digits[-32:]
    return digits


def _norm_ip(value: str | None) -> str | None:
    ip = str(value or "").strip().lower()
    return ip or None


def _norm_device(value: str | None) -> str | None:
    device = str(value or "").strip().lower()
    return device or None


def _append_entity(items: list[dict[str, Any]], entity_type: str, value: str | None, evidence: dict[str, Any]) -> None:
    if not value:
        return
    items.append(
        {
            "type": entity_type,
            "value": value,
            "weight": int(_ENTITY_WEIGHT.get(entity_type, 1)),
            "evidence": evidence,
        }
    )


def extract_entities_from_deal(deal: dict[str, Any]) -> list[dict[str, Any]]:
    payload = deal if isinstance(deal, dict) else {}
    counterparty = payload.get("counterparty") if isinstance(payload.get("counterparty"), dict) else {}
    payment = payload.get("payment") if isinstance(payload.get("payment"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

    deal_id = payload.get("id")
    evidence = {"deal_id": deal_id}

    items: list[dict[str, Any]] = []
    _append_entity(items, "inn", norm_inn(counterparty.get("inn")), evidence)
    _append_entity(items, "phone", norm_phone(counterparty.get("phone")), evidence)
    _append_entity(items, "email", _norm_email(counterparty.get("email")), evidence)
    _append_entity(items, "name", norm_name(counterparty.get("name")) or None, evidence)

    _append_entity(items, "card", _norm_card(payment.get("card")), evidence)
    _append_entity(items, "bank_account", _norm_bank_account(payment.get("bank_account")), evidence)

    _append_entity(items, "ip", _norm_ip(metadata.get("ip") or payload.get("ip")), evidence)
    _append_entity(items, "device", _norm_device(metadata.get("device") or payload.get("device")), evidence)

    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (str(item["type"]), str(item["value"]))
        existing = deduped.get(key)
        if not existing or int(item.get("weight") or 0) > int(existing.get("weight") or 0):
            deduped[key] = item

    return list(deduped.values())


async def upsert_entities(db: Session, extracted: list[dict[str, Any]]) -> list[FraudEntity]:
    result: list[FraudEntity] = []
    for item in extracted:
        entity_type = str(item.get("type") or "").strip()
        entity_value = str(item.get("value") or "").strip()
        if not entity_type or not entity_value:
            continue

        row = (
            db.query(FraudEntity)
            .filter(
                FraudEntity.entity_type == entity_type,
                FraudEntity.entity_value == entity_value,
            )
            .first()
        )
        if not row:
            row = FraudEntity(entity_type=entity_type, entity_value=entity_value)
            db.add(row)
            db.flush()

        result.append(row)

    db.commit()
    for row in result:
        db.refresh(row)
    return result


async def _upsert_deal_entity(db: Session, deal_id: int) -> FraudEntity:
    row = (
        db.query(FraudEntity)
        .filter(
            FraudEntity.entity_type == "deal",
            FraudEntity.entity_value == str(int(deal_id)),
        )
        .first()
    )
    if row:
        return row

    row = FraudEntity(entity_type="deal", entity_value=str(int(deal_id)))
    db.add(row)
    db.flush()
    db.commit()
    db.refresh(row)
    return row


async def link_entities_for_deal(db: Session, deal_id: int, entities: list[FraudEntity]) -> FraudEntity:
    deal_node = await _upsert_deal_entity(db, int(deal_id))

    for entity in entities:
        if entity.id == deal_node.id:
            continue

        existing = (
            db.query(FraudEdge)
            .filter(
                FraudEdge.src_entity_id == deal_node.id,
                FraudEdge.dst_entity_id == entity.id,
                FraudEdge.edge_type == "deal_link",
            )
            .first()
        )

        evidence = {"deal_id": int(deal_id), "entity_type": entity.entity_type, "entity_value": entity.entity_value}
        weight = int(_ENTITY_WEIGHT.get(entity.entity_type, 1))

        if existing:
            existing.weight = max(int(existing.weight or 1), weight)
            existing.evidence = evidence
            continue

        db.add(
            FraudEdge(
                src_entity_id=deal_node.id,
                dst_entity_id=entity.id,
                edge_type="deal_link",
                weight=weight,
                evidence=evidence,
            )
        )

    db.commit()
    return deal_node
