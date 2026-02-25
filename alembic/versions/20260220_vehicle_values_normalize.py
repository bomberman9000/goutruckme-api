"""normalize legacy vehicle kind/options/loading values

Revision ID: 20260220_vehicle_values_normalize
Revises: 20260220_vehicles_v2_matching
Create Date: 2026-02-20
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260220_vehicle_values_normalize"
down_revision: Union[str, None] = "20260220_vehicles_v2_matching"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ALLOWED_KINDS = {
    "EUROFURA_TENT_20T",
    "JUMBO",
    "REFRIGERATOR",
    "ISOTHERM",
    "DUMP_TRUCK",
    "TANKER",
    "CONTAINER_CARRIER",
    "FLATBED",
    "LOWBOY_TRAL",
    "VAN_UP_TO_3_5T",
    "CAR_CARRIER",
    "TIMBER_TRUCK",
    "MANIPULATOR",
}

ALLOWED_OPTIONS = {
    "liftgate",
    "conics",
    "adr",
    "reefer_unit",
    "straps",
    "tarpaulin",
    "pallet_jack",
    "crane",
    "oversize_ok",
    "city_access",
}

ALLOWED_LOADING = {"top", "side", "back"}

VEHICLE_KIND_MAP = {
    "еврофура": "EUROFURA_TENT_20T",
    "еврофура (тент) 20т": "EUROFURA_TENT_20T",
    "тент": "EUROFURA_TENT_20T",
    "jumbо": "JUMBO",
    "jumbo": "JUMBO",
    "реф": "REFRIGERATOR",
    "рефрижератор": "REFRIGERATOR",
    "изотерм": "ISOTHERM",
    "самосвал": "DUMP_TRUCK",
    "цистерна": "TANKER",
    "контейнеровоз": "CONTAINER_CARRIER",
    "платформа": "FLATBED",
    "борт": "FLATBED",
    "трал": "LOWBOY_TRAL",
    "низкорамник": "LOWBOY_TRAL",
    "фургон": "VAN_UP_TO_3_5T",
    "малотоннаж": "VAN_UP_TO_3_5T",
    "автовоз": "CAR_CARRIER",
    "лесовоз": "TIMBER_TRUCK",
    "сортиментовоз": "TIMBER_TRUCK",
    "манипулятор": "MANIPULATOR",
}

BODY_TO_KIND_MAP = {
    "тент": "EUROFURA_TENT_20T",
    "реф": "REFRIGERATOR",
    "изотерм": "ISOTHERM",
    "самосвал": "DUMP_TRUCK",
    "цистерна": "TANKER",
    "контейнеровоз": "CONTAINER_CARRIER",
    "платформа": "FLATBED",
    "коники": "TIMBER_TRUCK",
    "трал": "LOWBOY_TRAL",
    "фургон": "VAN_UP_TO_3_5T",
    "автовоз": "CAR_CARRIER",
    "лесовоз": "TIMBER_TRUCK",
    "манипулятор": "MANIPULATOR",
}

OPTION_MAP = {
    "гидроборт": "liftgate",
    "liftgate": "liftgate",
    "коники": "conics",
    "conics": "conics",
    "adr": "adr",
    "опасные": "adr",
    "холодильная установка": "reefer_unit",
    "reefer": "reefer_unit",
    "reefer_unit": "reefer_unit",
    "ремни": "straps",
    "straps": "straps",
    "тент": "tarpaulin",
    "tarpaulin": "tarpaulin",
    "рокла": "pallet_jack",
    "pallet_jack": "pallet_jack",
    "кран": "crane",
    "crane": "crane",
    "негабарит": "oversize_ok",
    "oversize_ok": "oversize_ok",
    "пропуск": "city_access",
    "city_access": "city_access",
}

LOADING_MAP = {
    "top": "top",
    "верх": "top",
    "верхняя": "top",
    "side": "side",
    "бок": "side",
    "боковая": "side",
    "back": "back",
    "rear": "back",
    "зад": "back",
    "задняя": "back",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _to_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, set):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    return []


def _normalize_kind(kind_value: Any, body_type_value: Any) -> str:
    kind_raw = str(kind_value or "").strip()
    if kind_raw:
        upper = kind_raw.upper()
        if upper in ALLOWED_KINDS:
            return upper
        mapped = VEHICLE_KIND_MAP.get(_norm(kind_raw))
        if mapped:
            return mapped
    body_mapped = BODY_TO_KIND_MAP.get(_norm(body_type_value))
    if body_mapped:
        return body_mapped
    return "EUROFURA_TENT_20T"


def _normalize_options(options_value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in _to_list(options_value):
        mapped = OPTION_MAP.get(_norm(item))
        if not mapped or mapped not in ALLOWED_OPTIONS:
            continue
        if mapped in seen:
            continue
        seen.add(mapped)
        result.append(mapped)
    return result


def _normalize_loading(loading_value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in _to_list(loading_value):
        mapped = LOADING_MAP.get(_norm(item))
        if not mapped or mapped not in ALLOWED_LOADING:
            continue
        if mapped in seen:
            continue
        seen.add(mapped)
        result.append(mapped)
    return result


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "vehicles" not in inspector.get_table_names():
        return

    vehicle_table = sa.Table("vehicles", sa.MetaData(), autoload_with=bind)
    column_names = {col.name for col in vehicle_table.columns}
    required_cols = {"id", "vehicle_kind", "body_type", "options", "loading_types"}
    if not required_cols.issubset(column_names):
        return

    rows = bind.execute(
        sa.select(
            vehicle_table.c.id,
            vehicle_table.c.vehicle_kind,
            vehicle_table.c.body_type,
            vehicle_table.c.options,
            vehicle_table.c.loading_types,
        )
    ).fetchall()

    for row in rows:
        normalized_kind = _normalize_kind(row.vehicle_kind, row.body_type)
        normalized_options = _normalize_options(row.options)
        normalized_loading = _normalize_loading(row.loading_types)

        bind.execute(
            vehicle_table.update()
            .where(vehicle_table.c.id == row.id)
            .values(
                vehicle_kind=normalized_kind,
                options=normalized_options,
                loading_types=normalized_loading,
            )
        )


def downgrade() -> None:
    # Обратное преобразование в свободные текстовые значения не выполняем.
    pass

