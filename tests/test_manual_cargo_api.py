from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import cargos as cargos_api
from src.core.config import settings
from src.core.models import Cargo, ParserIngestEvent, User


class _FakeSession:
    def __init__(self, users: dict[int, object] | None = None):
        self.users = users or {}
        self.cargos: list[Cargo] = []
        self.events: list[ParserIngestEvent] = []
        self._pending: list[object] = []
        self._cargo_id = 0
        self._event_id = 0

    async def get(self, model, key):
        if model is User:
            return self.users.get(int(key))
        return None

    def add(self, obj):
        self._pending.append(obj)

    async def flush(self):
        for obj in list(self._pending):
            if isinstance(obj, Cargo) and getattr(obj, "id", None) is None:
                self._cargo_id += 1
                obj.id = self._cargo_id
                self.cargos.append(obj)
            elif isinstance(obj, ParserIngestEvent) and getattr(obj, "id", None) is None:
                self._event_id += 1
                obj.id = self._event_id
                self.events.append(obj)
            elif isinstance(obj, User):
                self.users[int(obj.id)] = obj
        self._pending.clear()

    async def commit(self):
        await self.flush()

    async def refresh(self, _obj):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _build_tma_header(user_id: int) -> dict[str, str]:
    payload_user = json.dumps(
        {"id": user_id, "first_name": "Alex", "last_name": "Logist"},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    pairs = {
        "auth_date": str(int(time.time())),
        "user": payload_user,
    }
    data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    sign = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    init_data = urlencode({**pairs, "hash": sign})
    return {"Authorization": f"tma {init_data}"}


def test_create_manual_cargo_creates_cargo_and_feed_event(monkeypatch):
    fake_session = _FakeSession()

    app = FastAPI()
    app.include_router(cargos_api.router)

    cargos_api.async_session = lambda: fake_session  # type: ignore[assignment]

    async def _clear_cached(_prefix: str) -> None:
        return None

    monkeypatch.setattr(cargos_api, "clear_cached", _clear_cached)

    client = TestClient(app)
    response = client.post(
        "/api/v1/cargos/manual",
        headers=_build_tma_header(555),
        json={
            "origin": "Москва",
            "destination": "Казань",
            "body_type": "тент",
            "weight": 20,
            "price": 120000,
            "load_date": "2026-02-27",
            "description": "Тестовый груз",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["cargo_id"] == 1
    assert body["feed_id"] == 1

    assert len(fake_session.cargos) == 1
    assert len(fake_session.events) == 1

    cargo = fake_session.cargos[0]
    event = fake_session.events[0]
    user = fake_session.users[555]

    assert cargo.owner_id == 555
    assert cargo.from_city == "Москва"
    assert cargo.to_city == "Казань"
    assert cargo.price == 120000

    assert event.source == "manual_client"
    assert event.status == "synced"
    assert event.from_city == "Москва"
    assert event.to_city == "Казань"
    assert event.rate_rub == 120000
    assert event.trust_verdict == "yellow"
    assert event.details_json is not None

    assert user.full_name == "Alex Logist"
