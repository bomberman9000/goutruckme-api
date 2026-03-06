from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import trucks as trucks_api
from src.core.config import settings
from src.core.matching import TruckMatch
from src.core.models import AvailableTruck, User


def _build_tma_header(user_id: int) -> dict[str, str]:
    payload_user = json.dumps({"id": user_id}, separators=(",", ":"), ensure_ascii=False)
    pairs = {"auth_date": str(int(time.time())), "user": payload_user}
    data_check = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    sign = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    init_data = urlencode({**pairs, "hash": sign})
    return {"Authorization": f"tma {init_data}"}


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(
        self,
        *,
        user: User | None = None,
        unlocked_ids: list[int] | None = None,
        trucks: list[AvailableTruck] | None = None,
    ):
        self.user = user
        self.unlocked_ids = unlocked_ids or []
        self.trucks = trucks or []

    async def get(self, model, key):
        if model is User:
            return self.user
        return None

    async def execute(self, stmt):
        if stmt.column_descriptions and stmt.column_descriptions[0].get("entity") is AvailableTruck:
            return _FakeExecuteResult(self.trucks)
        return _FakeExecuteResult(self.unlocked_ids)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def test_truck_search_api_masks_contacts_for_non_premium(monkeypatch):
    app = FastAPI()
    app.include_router(trucks_api.router)

    async def _match_trucks(*args, **kwargs):
        return [
            TruckMatch(
                id=147,
                source="avito:trucks",
                truck_type="манипулятор",
                capacity_tons=3.0,
                base_city="Тюмень",
                base_region=None,
                routes="Тюмень - Екатеринбург",
                phone="+79990000000",
                avito_url="https://example.com/1",
                raw_text="x",
            )
        ]

    monkeypatch.setattr(trucks_api, "async_session", lambda: _FakeSession(user=User(id=555, full_name="Test")))  # type: ignore[assignment]
    monkeypatch.setattr(trucks_api, "match_trucks", _match_trucks)

    client = TestClient(app)
    response = client.post(
        "/api/v1/trucks/search",
        headers=_build_tma_header(555),
        json={"raw_text": "ищу машину из Тюмени в Екатеринбург 3 тонны манипулятор"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["can_view_contact"] is False
    assert body["items"][0]["phone"] is None


def test_truck_search_api_returns_contact_for_premium(monkeypatch):
    app = FastAPI()
    app.include_router(trucks_api.router)

    async def _match_trucks(*args, **kwargs):
        return [
            TruckMatch(
                id=147,
                source="avito:trucks",
                truck_type="манипулятор",
                capacity_tons=3.0,
                base_city="Тюмень",
                base_region=None,
                routes="Тюмень - Екатеринбург",
                phone="+79990000000",
                avito_url="https://example.com/1",
                raw_text="x",
            )
        ]

    premium_user = User(
        id=555,
        full_name="Premium",
        is_premium=True,
        premium_until=datetime.now() + timedelta(days=1),
    )
    monkeypatch.setattr(trucks_api, "async_session", lambda: _FakeSession(user=premium_user))  # type: ignore[assignment]
    monkeypatch.setattr(trucks_api, "match_trucks", _match_trucks)

    client = TestClient(app)
    response = client.post(
        "/api/v1/trucks/search",
        headers=_build_tma_header(555),
        json={"raw_text": "ищу машину из Тюмени в Екатеринбург 3 тонны манипулятор"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["can_view_contact"] is True
    assert body["items"][0]["phone"] == "+79990000000"


def test_recent_trucks_api_returns_teaser_list(monkeypatch):
    app = FastAPI()
    app.include_router(trucks_api.router)

    trucks = [
        AvailableTruck(
            id=147,
            source="avito:trucks",
            external_id="a-147",
            truck_type="манипулятор",
            capacity_tons=3.0,
            base_city="Тюмень",
            routes="Тюмень - Екатеринбург",
            phone="+79990000000",
            avito_url="https://example.com/1",
            raw_text="x",
        )
    ]

    monkeypatch.setattr(
        trucks_api,
        "async_session",
        lambda: _FakeSession(user=User(id=555, full_name="Test"), trucks=trucks),
    )  # type: ignore[assignment]

    client = TestClient(app)
    response = client.get(
        "/api/v1/trucks/recent?limit=5",
        headers=_build_tma_header(555),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == 147
    assert body["items"][0]["can_view_contact"] is False
    assert body["items"][0]["phone"] is None
