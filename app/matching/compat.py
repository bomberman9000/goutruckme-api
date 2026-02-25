from __future__ import annotations

import re
from typing import Any

from app.dicts.cargos import CARGO_KINDS
from app.dicts.vehicles import (
    BODY_COMPATIBILITY,
    BODY_TYPE_ALIASES,
    LEGACY_BODY_KIND,
    LOADING_OPTION_TO_TYPE,
    LOADING_TYPE_ALIASES,
    LOADING_TYPES,
    TEMP_VEHICLE_KINDS,
    VEHICLE_KINDS,
    VEHICLE_OPTIONS,
)


ADR_CLASS_RE = re.compile(r"^[1-9](?:\.[0-9])?$")
CARGO_KIND_ALIASES = {
    "general": "GENERAL",
    "palletized": "PALLETIZED",
    "food": "FOOD",
    "pharma": "PHARMA",
    "bulk": "BULK",
    "liquid": "LIQUID",
    "gas": "LIQUID",
    "container": "CONTAINER",
    "oversize": "OVERSIZE",
    "cars": "CARS",
    "timber": "TIMBER",
    "equipment": "EQUIPMENT",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw = [item.strip() for item in values.replace(";", ",").split(",") if item.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw = [str(item).strip() for item in values if str(item).strip()]
    else:
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = _norm(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _normalize_adr_classes(values: Any) -> list[str]:
    normalized = _normalize_list(values)
    result: list[str] = []
    seen: set[str] = set()
    for item in normalized:
        candidate = item.replace("class", "").replace(",", ".").replace(" ", "").strip(".")
        if not ADR_CLASS_RE.match(candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def _normalize_cargo_kind(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    upper = raw.upper()
    if upper in CARGO_KINDS:
        return upper
    alias = CARGO_KIND_ALIASES.get(_norm(raw))
    if alias:
        return alias
    return upper


def canonical_body_type(body_type: Any, *, vehicle_kind: str | None = None) -> str:
    body = BODY_TYPE_ALIASES.get(_norm(body_type), "")
    if body:
        return body
    if vehicle_kind and vehicle_kind in VEHICLE_KINDS:
        return str(VEHICLE_KINDS[vehicle_kind].get("body_type") or "")
    return ""


def resolve_vehicle_kind(vehicle_kind: Any, body_type: Any) -> str:
    normalized_kind = str(vehicle_kind or "").strip().upper()
    if normalized_kind in VEHICLE_KINDS:
        return normalized_kind

    body = canonical_body_type(body_type)
    mapped = LEGACY_BODY_KIND.get(body)
    return mapped or normalized_kind


def _body_match(vehicle_body: str, required_body: str) -> bool:
    if not required_body:
        return True
    compatible = BODY_COMPATIBILITY.get(vehicle_body)
    if not compatible:
        return vehicle_body == required_body
    return required_body in compatible


def _kind_matrix_check(
    *,
    vehicle_kind: str,
    vehicle_options: set[str],
    cargo_kind: str,
    cargo: Any,
) -> tuple[bool, str]:
    if not cargo_kind:
        return True, ""

    if cargo_kind == "BULK" or bool(getattr(cargo, "needs_dump", False)):
        if vehicle_kind != "DUMP_TRUCK":
            return False, "Сыпучий груз требует самосвал"
        return True, "Подходит для сыпучего груза (самосвал)"

    if vehicle_kind == "DUMP_TRUCK" and cargo_kind != "BULK" and not bool(getattr(cargo, "needs_dump", False)):
        return False, "Самосвал в MVP подбирается только для сыпучих грузов"

    if cargo_kind == "LIQUID":
        if vehicle_kind != "TANKER":
            return False, "Наливной/газовый груз требует цистерну"
        return True, "Подходит для наливного груза (цистерна)"

    if cargo_kind == "CONTAINER" or bool(getattr(cargo, "is_container", False)):
        if vehicle_kind != "CONTAINER_CARRIER":
            return False, "Контейнерный груз требует контейнеровоз"
        return True, "Подходит для контейнера"

    if cargo_kind == "CARS":
        if vehicle_kind != "CAR_CARRIER":
            return False, "Груз 'авто' требует автовоз"
        return True, "Подходит для перевозки авто"

    if cargo_kind == "TIMBER":
        if vehicle_kind == "TIMBER_TRUCK":
            return True, "Подходит для лесоматериалов (лесовоз)"
        if vehicle_kind == "FLATBED" and "conics" in vehicle_options:
            return True, "Платформа с кониками подходит для лесоматериалов"
        return False, "Лесоматериалы требуют лесовоз или платформу с кониками"

    if cargo_kind == "OVERSIZE":
        if vehicle_kind != "LOWBOY_TRAL":
            return False, "Негабарит требует трал"
        return True, "Подходит для негабарита (трал)"

    return True, ""


def _option_label(option_key: str) -> str:
    return VEHICLE_OPTIONS.get(option_key, option_key)


def check_compat(vehicle: Any, cargo: Any) -> dict[str, Any]:
    reasons: list[str] = []
    blockers: list[str] = []

    vehicle_kind = resolve_vehicle_kind(getattr(vehicle, "vehicle_kind", None), getattr(vehicle, "body_type", None))
    vehicle_body = canonical_body_type(getattr(vehicle, "body_type", None), vehicle_kind=vehicle_kind)

    vehicle_options = set(_normalize_list(getattr(vehicle, "options", None)))
    vehicle_loading_types = {
        LOADING_TYPE_ALIASES.get(_norm(item), _norm(item))
        for item in _normalize_list(getattr(vehicle, "loading_types", None))
    }
    vehicle_loading_types = {item for item in vehicle_loading_types if item in LOADING_TYPES}
    vehicle_adr_classes = set(_normalize_adr_classes(getattr(vehicle, "adr_classes", None)))

    payload_tons = _to_float(getattr(vehicle, "payload_tons", None))
    if payload_tons is None:
        payload_tons = _to_float(getattr(vehicle, "capacity_tons", None))
    volume_m3 = _to_float(getattr(vehicle, "volume_m3", None))

    load_weight = _to_float(getattr(cargo, "weight_t", None))
    if load_weight is None:
        load_weight = _to_float(getattr(cargo, "weight", None))
    load_weight = load_weight or 0.0

    load_volume = _to_float(getattr(cargo, "volume_m3", None))
    if load_volume is None:
        load_volume = _to_float(getattr(cargo, "volume", None))
    load_volume = load_volume or 0.0

    if payload_tons is not None and load_weight > payload_tons + 1e-6:
        blockers.append(f"Превышение по весу: {load_weight:.2f}т > {payload_tons:.2f}т")
    else:
        reasons.append(
            f"Влезает по весу: {load_weight:.2f}т из {payload_tons:.2f}т"
            if payload_tons
            else f"Вес груза: {load_weight:.2f}т"
        )

    if volume_m3 is not None and load_volume > volume_m3 + 1e-6:
        blockers.append(f"Превышение по объёму: {load_volume:.2f}м³ > {volume_m3:.2f}м³")
    else:
        reasons.append(
            f"Влезает по объёму: {load_volume:.2f}м³ из {volume_m3:.2f}м³"
            if volume_m3
            else f"Объём груза: {load_volume:.2f}м³"
        )

    required_vehicle_kinds = {item.upper() for item in _normalize_list(getattr(cargo, "required_vehicle_kinds", None))}
    if required_vehicle_kinds:
        if vehicle_kind not in required_vehicle_kinds:
            blockers.append(f"Требуется тип ТС: {', '.join(sorted(required_vehicle_kinds))}")
        else:
            reasons.append("Тип ТС соответствует требованию груза")

    required_body_type = canonical_body_type(getattr(cargo, "required_body_type", None))
    if required_body_type:
        if not _body_match(vehicle_body, required_body_type):
            blockers.append(f"Несовместимый кузов: нужен {required_body_type}")
        else:
            reasons.append(f"Подходит по типу кузова: {required_body_type}")

    cargo_kind = _normalize_cargo_kind(getattr(cargo, "cargo_kind", None))
    matrix_ok, matrix_reason = _kind_matrix_check(
        vehicle_kind=vehicle_kind,
        vehicle_options=vehicle_options,
        cargo_kind=cargo_kind,
        cargo=cargo,
    )
    if not matrix_ok:
        blockers.append(matrix_reason)
    elif matrix_reason:
        reasons.append(matrix_reason)

    if bool(getattr(cargo, "needs_crane", False)) or bool(getattr(cargo, "loading_no_forklift", False)):
        if vehicle_kind != "MANIPULATOR" and "crane" not in vehicle_options:
            blockers.append("Груз требует кран/манипулятор")
        else:
            reasons.append("Подходит по крановой погрузке")

    if cargo_kind == "OVERSIZE" or bool(getattr(cargo, "oversize", False)) or (load_weight > 20.0):
        if vehicle_kind != "LOWBOY_TRAL" and "oversize_ok" not in vehicle_options:
            blockers.append("Негабарит/тяжеловес требует трал или oversize_ok")
        else:
            reasons.append("Подходит для негабарита/тяжеловеса")

    required_loading = LOADING_TYPE_ALIASES.get(_norm(getattr(cargo, "loading_type", None)), "")
    if required_loading:
        if required_loading not in vehicle_loading_types:
            blockers.append(f"Нет требуемого типа погрузки: {LOADING_TYPES.get(required_loading, required_loading)}")
        else:
            reasons.append(f"Погрузка: {LOADING_TYPES.get(required_loading, required_loading)}")

    required_options = set(_normalize_list(getattr(cargo, "required_options", None)))
    for opt in sorted(required_options):
        loading_from_option = LOADING_OPTION_TO_TYPE.get(opt)
        if loading_from_option:
            if loading_from_option not in vehicle_loading_types:
                blockers.append(f"Нет требуемой погрузки: {LOADING_TYPES.get(loading_from_option, loading_from_option)}")
            else:
                reasons.append(f"Есть требуемая погрузка: {LOADING_TYPES.get(loading_from_option, loading_from_option)}")
            continue
        if opt == "crane":
            if vehicle_kind != "MANIPULATOR" and "crane" not in vehicle_options:
                blockers.append("Требуется опция: кран")
            else:
                reasons.append("Есть кран/манипулятор")
            continue
        if opt not in vehicle_options:
            blockers.append(f"Не хватает опции: {_option_label(opt)}")
        else:
            reasons.append(f"Есть опция: {_option_label(opt)}")

    cargo_adr_classes = set(_normalize_adr_classes(getattr(cargo, "adr_classes", None)))
    adr_class_single = _norm(getattr(cargo, "adr_class", None))
    if not cargo_adr_classes and adr_class_single:
        cargo_adr_classes = set(_normalize_adr_classes([adr_class_single]))
    if cargo_adr_classes:
        if "adr" not in vehicle_options:
            blockers.append("Груз ADR: у машины нет ADR")
        elif vehicle_adr_classes:
            if not cargo_adr_classes.issubset(vehicle_adr_classes):
                blockers.append(
                    f"ADR классы не поддерживаются полностью: нужно {', '.join(sorted(cargo_adr_classes))}"
                )
            else:
                reasons.append(f"ADR классы поддержаны: {', '.join(sorted(cargo_adr_classes))}")
        else:
            reasons.append("ADR включён (MVP без детальных классов)")

    temp_min = _to_float(getattr(cargo, "temp_min", None))
    temp_max = _to_float(getattr(cargo, "temp_max", None))
    temp_required = bool(getattr(cargo, "temp_required", False)) or temp_min is not None or temp_max is not None
    if temp_required:
        if vehicle_kind != "REFRIGERATOR":
            blockers.append("Температурный груз в MVP требует Рефрижератор")
        else:
            vehicle_temp_min = _to_float(getattr(vehicle, "temp_min", None))
            vehicle_temp_max = _to_float(getattr(vehicle, "temp_max", None))
            if temp_min is not None and (vehicle_temp_min is None or vehicle_temp_min > temp_min):
                blockers.append(f"Не покрывается нижняя температура: нужно {temp_min}°C")
            if temp_max is not None and (vehicle_temp_max is None or vehicle_temp_max < temp_max):
                blockers.append(f"Не покрывается верхняя температура: нужно {temp_max}°C")
            if not blockers:
                reasons.append("Температурный режим совместим")
    elif vehicle_kind in TEMP_VEHICLE_KINDS:
        reasons.append("Температурный транспорт (может везти обычный груз)")

    crew_required = bool(getattr(cargo, "crew_required", False))
    crew_size = _to_int(getattr(vehicle, "crew_size", None), default=1)
    if crew_required and crew_size < 2:
        blockers.append("Требуется экипаж 2 водителя")
    elif crew_required and crew_size >= 2:
        reasons.append("Экипаж 2+ подтверждён")

    fill_weight_ratio = 0.0
    if payload_tons and payload_tons > 0:
        fill_weight_ratio = min(1.0, max(0.0, load_weight / payload_tons))
    fill_volume_ratio = 0.0
    if volume_m3 and volume_m3 > 0:
        fill_volume_ratio = min(1.0, max(0.0, load_volume / volume_m3))

    if vehicle_kind == "JUMBO" and load_volume > 0 and load_weight > 0:
        density = load_weight / max(load_volume, 1e-6)
        if load_volume >= 40 and density < 0.2:
            reasons.append("Jumbo бонус: объёмный и лёгкий груз")

    if not blockers:
        reasons.append("Совместимость подтверждена")

    return {
        "ok": len(blockers) == 0,
        "reasons": reasons,
        "blockers": blockers,
        "fill_weight_ratio": fill_weight_ratio,
        "fill_volume_ratio": fill_volume_ratio,
        "vehicle_kind": vehicle_kind,
        "vehicle_body": vehicle_body,
        "required_vehicle_kinds": sorted(required_vehicle_kinds),
        "required_options": sorted(required_options),
        "required_adr_classes": sorted(cargo_adr_classes),
        "required_loading_type": required_loading or None,
        "cargo_kind": cargo_kind or None,
    }
