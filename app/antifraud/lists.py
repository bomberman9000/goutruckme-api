from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.antifraud.normalize import norm_inn, norm_name, norm_phone
from app.models.models import CounterpartyList


_ALLOWED_LIST_TYPES = {"white", "black"}


def _normalize_inputs(
    *,
    inn: str | None = None,
    phone: str | None = None,
    name: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    normalized_inn = norm_inn(inn)
    normalized_phone = norm_phone(phone)
    normalized_name = norm_name(name) or None
    return normalized_inn, normalized_phone, normalized_name


async def add_to_list(
    db: Session,
    list_type: str,
    inn: str | None = None,
    phone: str | None = None,
    name: str | None = None,
    note: str | None = None,
) -> CounterpartyList:
    normalized_type = str(list_type or "").strip().lower()
    if normalized_type not in _ALLOWED_LIST_TYPES:
        raise ValueError("list_type must be 'white' or 'black'")

    normalized_inn, normalized_phone, normalized_name = _normalize_inputs(
        inn=inn,
        phone=phone,
        name=name,
    )
    if not any([normalized_inn, normalized_phone, normalized_name]):
        raise ValueError("at least one identifier is required: inn/phone/name")

    query = db.query(CounterpartyList).filter(CounterpartyList.list_type == normalized_type)
    existing_filters = []
    if normalized_inn:
        existing_filters.append(CounterpartyList.inn == normalized_inn)
    if normalized_phone:
        existing_filters.append(CounterpartyList.phone == normalized_phone)
    if normalized_name:
        existing_filters.append(CounterpartyList.name == normalized_name)

    existing = query.filter(or_(*existing_filters)).first() if existing_filters else None
    if existing:
        if normalized_inn:
            existing.inn = normalized_inn
        if normalized_phone:
            existing.phone = normalized_phone
        if normalized_name:
            existing.name = normalized_name
        if note is not None:
            existing.note = str(note)
        db.commit()
        db.refresh(existing)
        return existing

    row = CounterpartyList(
        list_type=normalized_type,
        inn=normalized_inn,
        phone=normalized_phone,
        name=normalized_name,
        note=str(note) if note is not None else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


async def check_lists(
    db: Session,
    inn: str | None,
    phone: str | None,
    name: str | None,
) -> dict[str, Any]:
    normalized_inn, normalized_phone, normalized_name = _normalize_inputs(
        inn=inn,
        phone=phone,
        name=name,
    )

    result: dict[str, Any] = {
        "whitelist_match": False,
        "blacklist_match": False,
        "matched_fields": [],
        "entries": [],
    }

    filters = []
    if normalized_inn:
        filters.append(CounterpartyList.inn == normalized_inn)
    if normalized_phone:
        filters.append(CounterpartyList.phone == normalized_phone)
    if normalized_name:
        filters.append(CounterpartyList.name == normalized_name)

    if not filters:
        return result

    rows = db.query(CounterpartyList).filter(or_(*filters)).all()
    matched_fields: set[str] = set()

    for row in rows:
        local_matches: list[str] = []
        if normalized_inn and row.inn == normalized_inn:
            local_matches.append("inn")
        if normalized_phone and row.phone == normalized_phone:
            local_matches.append("phone")
        if normalized_name and row.name == normalized_name:
            local_matches.append("name")

        if not local_matches:
            continue

        if row.list_type == "white":
            result["whitelist_match"] = True
        if row.list_type == "black":
            result["blacklist_match"] = True

        matched_fields.update(local_matches)
        result["entries"].append(
            {
                "list_type": row.list_type,
                "inn": row.inn,
                "phone": row.phone,
                "name": row.name,
                "note": row.note,
            }
        )

    result["matched_fields"] = sorted(matched_fields)
    return result
