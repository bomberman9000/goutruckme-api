from __future__ import annotations

from fastapi import APIRouter

from app.dicts.cargos import CARGO_KINDS
from app.dicts.vehicles import ADR_CLASSES, LOADING_TYPES, VEHICLE_KINDS, VEHICLE_OPTIONS

router = APIRouter()


@router.get("/dicts/vehicles")
def get_vehicles_dicts() -> dict:
    return {
        "vehicle_kinds": VEHICLE_KINDS,
        "loading_types": LOADING_TYPES,
        "vehicle_options": VEHICLE_OPTIONS,
        "adr_classes": ADR_CLASSES,
        "cargo_kinds": CARGO_KINDS,
    }
