from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import fleet as fleet_api
from src.core.config import settings
from src.core.models import UserVehicle


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _stmt):
        return _FakeResult(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeGeo:
    async def get_city_data(self, city_name: str):
        coords = {
            "Москва": SimpleNamespace(name="Москва", lat=55.75, lon=37.61),
            "Казань": SimpleNamespace(name="Казань", lat=55.79, lon=49.12),
        }
        return coords.get(city_name)


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


def test_vehicle_map_endpoint(monkeypatch):
    app = FastAPI()
    app.include_router(fleet_api.router)
    rows = [
        UserVehicle(id=1, user_id=101, body_type="тент", capacity_tons=20.0, location_city="Москва", is_available=True),
        UserVehicle(id=2, user_id=102, body_type="реф", capacity_tons=10.0, location_city="Казань", is_available=False),
        UserVehicle(id=3, user_id=103, body_type="борт", capacity_tons=5.0, location_city="Неизвестно", is_available=True),
    ]
    fleet_api.async_session = lambda: _FakeSession(rows)  # type: ignore[assignment]
    monkeypatch.setattr(fleet_api, "get_geo_service", lambda: _FakeGeo())

    client = TestClient(app)
    response = client.get("/api/v1/fleet/vehicles/map", headers=_build_tma_header(555))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["items"][0]["status"] == "available"
    assert body["items"][1]["status"] == "in_work"
    assert body["items"][0]["lat"] == 55.75
