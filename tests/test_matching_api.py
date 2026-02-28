from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import match as match_api
from src.core.config import settings
from src.core.models import Cargo, CargoStatus, UserVehicle


class _FakeSession:
    def __init__(self, vehicle: UserVehicle | None = None, cargo: Cargo | None = None):
        self.vehicle = vehicle
        self.cargo = cargo

    async def scalar(self, stmt):
        sql = str(stmt)
        if "FROM user_vehicles" in sql:
            return self.vehicle
        if "FROM cargos" in sql:
            return self.cargo
        return None

    async def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _build_tma_header(user_id: int) -> dict[str, str]:
    payload_user = json.dumps({"id": user_id}, separators=(",", ":"), ensure_ascii=False)
    pairs = {
        "auth_date": str(int(time.time())),
        "user": payload_user,
    }
    data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    sign = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    init_data = urlencode({**pairs, "hash": sign})
    return {"Authorization": f"tma {init_data}"}


def test_vehicle_match_endpoint(monkeypatch):
    app = FastAPI()
    app.include_router(match_api.router)
    vehicle = UserVehicle(id=7, user_id=555, body_type="тент", capacity_tons=20, location_city="Москва", is_available=True)
    match_api.async_session = lambda: _FakeSession(vehicle=vehicle)  # type: ignore[assignment]

    async def _matches(_session, _vehicle, *, limit=10):
        return [
            SimpleNamespace(
                id=101,
                from_city="Москва",
                to_city="Казань",
                body_type="тент",
                weight_t=20.0,
                rate_rub=100000,
                rate_per_km=50.0,
                load_date="2026-02-28",
                is_hot_deal=False,
                freshness="5м",
                match_score=92,
                distance_to_pickup_km=12,
                match_reasons=["подходит по кузову"],
                verified_payment=True,
            )
        ]

    monkeypatch.setattr(match_api, "find_matches_for_vehicle", _matches)
    client = TestClient(app)
    response = client.get("/api/v1/match/vehicle/7", headers=_build_tma_header(555))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["matched"][0]["match_score"] == 92


def test_cargo_match_endpoint(monkeypatch):
    app = FastAPI()
    app.include_router(match_api.router)
    cargo = Cargo(
        id=9,
        owner_id=555,
        from_city="Москва",
        to_city="Казань",
        cargo_type="тент",
        weight=20,
        price=120000,
        load_date=datetime.utcnow(),
        status=CargoStatus.NEW,
    )
    match_api.async_session = lambda: _FakeSession(cargo=cargo)  # type: ignore[assignment]

    async def _matches(_session, _cargo, *, limit=10):
        return [
            SimpleNamespace(
                vehicle_id=1,
                body_type="тент",
                capacity_tons=20.0,
                location_city="Москва",
                is_available=True,
                plate_number="A111AA",
                match_score=88,
                distance_to_pickup_km=18,
                match_reasons=["машина рядом"],
            )
        ]

    monkeypatch.setattr(match_api, "find_matches_for_cargo", _matches)
    client = TestClient(app)
    response = client.get("/api/v1/match/cargo/9", headers=_build_tma_header(555))

    assert response.status_code == 200
    assert response.json()["matched"][0]["vehicle_id"] == 1


def test_match_summary_endpoint(monkeypatch):
    app = FastAPI()
    app.include_router(match_api.router)
    match_api.async_session = lambda: _FakeSession()  # type: ignore[assignment]

    async def _summary(_session, _user_id):
        return SimpleNamespace(
            vehicle_match_count=3,
            cargo_match_count=2,
            best_vehicle_match_score=95,
            best_cargo_match_score=90,
        )

    monkeypatch.setattr(match_api, "build_match_summary", _summary)
    client = TestClient(app)
    response = client.get("/api/v1/match/summary", headers=_build_tma_header(555))

    assert response.status_code == 200
    body = response.json()
    assert body["vehicle_match_count"] == 3
    assert body["best_cargo_match_score"] == 90
