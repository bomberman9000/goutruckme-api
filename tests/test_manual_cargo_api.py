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

from src.api import cargos as cargos_api
from src.core.config import settings
from src.core import geo as geo_service
from src.core.models import Cargo, CargoStatus, ParserIngestEvent, User


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
        if model is Cargo:
            for cargo in self.cargos:
                if int(cargo.id) == int(key):
                    return cargo
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM cargos" in sql:
            rows = list(self.cargos)
        elif "FROM parser_ingest_events" in sql:
            rows = list(self.events)
        else:
            rows = []
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

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


def test_get_my_cargos_returns_only_owner_items(monkeypatch):
    fake_session = _FakeSession(users={555: User(id=555, full_name="Alex", username=None)})
    fake_session.cargos = [
        Cargo(
            id=1,
            owner_id=555,
            from_city="Москва",
            to_city="Казань",
            cargo_type="тент",
            weight=20,
            price=120000,
            load_date=datetime(2026, 2, 27),
            status=CargoStatus.NEW,
        ),
        Cargo(
            id=2,
            owner_id=777,
            from_city="Тула",
            to_city="Омск",
            cargo_type="реф",
            weight=10,
            price=80000,
            load_date=datetime(2026, 2, 28),
            status=CargoStatus.NEW,
        ),
    ]
    fake_session.events = [
        ParserIngestEvent(
            id=11,
            stream_entry_id="manual-1",
            chat_id="user:555",
            message_id=0,
            source="manual_client",
            from_city="Москва",
            to_city="Казань",
            raw_text="x",
            status="synced",
            is_spam=False,
            details_json='{"cargo_id": 1}',
        )
    ]

    app = FastAPI()
    app.include_router(cargos_api.router)
    cargos_api.async_session = lambda: fake_session  # type: ignore[assignment]
    client = TestClient(app)

    response = client.get("/api/v1/cargos/my", headers=_build_tma_header(555))
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == 1
    assert body["items"][0]["feed_id"] == 11
    assert body["items"][0]["is_published"] is True


def test_archive_my_cargo_marks_feed_event_ignored(monkeypatch):
    fake_session = _FakeSession(users={555: User(id=555, full_name="Alex", username=None)})
    fake_session.cargos = [
        Cargo(
            id=1,
            owner_id=555,
            from_city="Москва",
            to_city="Казань",
            cargo_type="тент",
            weight=20,
            price=120000,
            load_date=datetime(2026, 2, 27),
            status=CargoStatus.NEW,
        )
    ]
    fake_session.events = [
        ParserIngestEvent(
            id=11,
            stream_entry_id="manual-1",
            chat_id="user:555",
            message_id=0,
            source="manual_client",
            from_city="Москва",
            to_city="Казань",
            raw_text="x",
            status="synced",
            is_spam=False,
            details_json='{"cargo_id": 1}',
        )
    ]

    app = FastAPI()
    app.include_router(cargos_api.router)
    cargos_api.async_session = lambda: fake_session  # type: ignore[assignment]

    async def _clear_cached(_prefix: str) -> None:
        return None

    monkeypatch.setattr(cargos_api, "clear_cached", _clear_cached)

    client = TestClient(app)
    response = client.post("/api/v1/cargos/1/archive", headers=_build_tma_header(555))
    assert response.status_code == 200
    assert fake_session.cargos[0].status == CargoStatus.ARCHIVED
    assert fake_session.events[0].status == "ignored"


def test_update_my_cargo_updates_cargo_and_feed_event(monkeypatch):
    fake_session = _FakeSession(users={555: User(id=555, full_name="Alex", username=None)})
    fake_session.cargos = [
        Cargo(
            id=1,
            owner_id=555,
            from_city="Москва",
            to_city="Казань",
            cargo_type="тент",
            weight=20,
            price=120000,
            load_date=datetime(2026, 2, 27),
            status=CargoStatus.NEW,
        )
    ]
    fake_session.events = [
        ParserIngestEvent(
            id=11,
            stream_entry_id="manual-1",
            chat_id="user:555",
            message_id=0,
            source="manual_client",
            from_city="Москва",
            to_city="Казань",
            body_type="тент",
            rate_rub=120000,
            weight_t=20,
            raw_text="x",
            status="synced",
            is_spam=False,
            details_json='{"cargo_id": 1}',
        )
    ]

    app = FastAPI()
    app.include_router(cargos_api.router)
    cargos_api.async_session = lambda: fake_session  # type: ignore[assignment]

    async def _clear_cached(_prefix: str) -> None:
        return None

    monkeypatch.setattr(cargos_api, "clear_cached", _clear_cached)
    monkeypatch.setattr(geo_service, "city_coords", lambda _c: None)

    client = TestClient(app)
    response = client.patch(
        "/api/v1/cargos/1",
        headers=_build_tma_header(555),
        json={"destination": "Самара", "price": 150000, "payment_terms": "безнал"},
    )
    assert response.status_code == 200
    assert fake_session.cargos[0].to_city == "Самара"
    assert fake_session.cargos[0].price == 150000
    assert fake_session.events[0].to_city == "Самара"
    assert fake_session.events[0].rate_rub == 150000
    assert fake_session.events[0].payment_terms == "безнал"
