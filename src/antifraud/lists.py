from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.antifraud.normalize import norm_inn, norm_name, norm_phone
from src.core.models import CounterpartyList


async def add_to_list(
    db: AsyncSession,
    *,
    list_type: str,
    inn: str | None = None,
    phone: str | None = None,
    name: str | None = None,
    note: str | None = None,
) -> CounterpartyList:
    normalized_type = (list_type or "").strip().lower()
    if normalized_type not in {"white", "black"}:
        raise ValueError("list_type must be 'white' or 'black'")

    normalized_inn = norm_inn(inn)
    normalized_phone = norm_phone(phone)
    normalized_name = norm_name(name)

    if not any([normalized_inn, normalized_phone, normalized_name]):
        raise ValueError("At least one of inn/phone/name must be provided")

    filters = []
    if normalized_inn:
        filters.append(CounterpartyList.inn == normalized_inn)
    if normalized_phone:
        filters.append(CounterpartyList.phone == normalized_phone)
    if normalized_name:
        filters.append(CounterpartyList.name == normalized_name)

    if filters:
        result = await db.execute(
            select(CounterpartyList).where(
                CounterpartyList.list_type == normalized_type,
                or_(*filters),
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

    row = CounterpartyList(
        list_type=normalized_type,
        inn=normalized_inn,
        phone=normalized_phone,
        name=normalized_name,
        note=(note or "").strip() or None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def check_lists(
    db: AsyncSession,
    *,
    inn: str | None,
    phone: str | None,
    name: str | None,
) -> dict[str, Any]:
    normalized_inn = norm_inn(inn)
    normalized_phone = norm_phone(phone)
    normalized_name = norm_name(name)

    filters = []
    if normalized_inn:
        filters.append(CounterpartyList.inn == normalized_inn)
    if normalized_phone:
        filters.append(CounterpartyList.phone == normalized_phone)
    if normalized_name:
        filters.append(CounterpartyList.name == normalized_name)

    if not filters:
        return {
            "whitelist_match": False,
            "blacklist_match": False,
            "matched_fields": [],
            "entries": [],
        }

    result = await db.execute(select(CounterpartyList).where(or_(*filters)))
    rows = list(result.scalars().all())

    entries = []
    whitelist_match = False
    blacklist_match = False
    matched_fields: set[str] = set()

    for row in rows:
        if row.list_type == "white":
            whitelist_match = True
        if row.list_type == "black":
            blacklist_match = True

        if normalized_inn and row.inn == normalized_inn:
            matched_fields.add("inn")
        if normalized_phone and row.phone == normalized_phone:
            matched_fields.add("phone")
        if normalized_name and row.name == normalized_name:
            matched_fields.add("name")

        entries.append(
            {
                "list_type": row.list_type,
                "inn": row.inn,
                "phone": row.phone,
                "name": row.name,
                "note": row.note,
            }
        )

    return {
        "whitelist_match": whitelist_match,
        "blacklist_match": blacklist_match,
        "matched_fields": sorted(matched_fields),
        "entries": entries,
    }
