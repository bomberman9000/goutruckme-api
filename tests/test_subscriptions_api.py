from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import subscriptions as subscriptions_api
from src.core.config import settings
from datetime import datetime

from src.core.models import ParserIngestEvent, RouteSubscription


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _FakeSession:
    def __init__(self):
        self.subscriptions: list[RouteSubscription] = []
        self.events: list[ParserIngestEvent] = []
        self._pending: list[object] = []
        self._id = 0

    async def execute(self, _stmt):
        sql = str(_stmt)
        if "FROM route_subscriptions" in sql:
            return _FakeExecuteResult(self.subscriptions)
        if "FROM parser_ingest_events" in sql:
            return _FakeExecuteResult(self.events)
        return _FakeExecuteResult([])

    async def get(self, model, key):
        if model is RouteSubscription:
            for item in self.subscriptions:
                if int(item.id) == int(key):
                    return item
        return None

    def add(self, obj):
        self._pending.append(obj)

    async def commit(self):
        for obj in list(self._pending):
            if isinstance(obj, RouteSubscription) and getattr(obj, "id", None) is None:
                self._id += 1
                obj.id = self._id
                self.subscriptions.append(obj)
        self._pending.clear()

    async def refresh(self, _obj):
        return None

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


def test_create_list_and_delete_subscription():
    fake_session = _FakeSession()
    app = FastAPI()
    app.include_router(subscriptions_api.router)
    subscriptions_api.async_session = lambda: fake_session  # type: ignore[assignment]
    client = TestClient(app)

    created = client.post(
        "/api/v1/subscriptions",
        headers=_build_tma_header(777),
        json={"from_city": "Москва", "to_city": "Казань", "body_type": "тент"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["item"]["id"] == 1
    assert body["item"]["match_count"] == 0

    fake_session.events = [
        ParserIngestEvent(
            id=10,
            stream_entry_id="10-0",
            chat_id="chat",
            message_id=10,
            source="test",
            from_city="Москва",
            to_city="Казань",
            body_type="тент",
            status="synced",
            is_spam=False,
            raw_text="x",
            created_at=datetime.utcnow(),
        )
    ]

    duplicate = client.post(
        "/api/v1/subscriptions",
        headers=_build_tma_header(777),
        json={"from_city": "Москва", "to_city": "Казань", "body_type": "тент"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["item"]["id"] == 1
    assert len(fake_session.subscriptions) == 1

    listed = client.get("/api/v1/subscriptions", headers=_build_tma_header(777))
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["match_count"] == 1

    deleted = client.delete("/api/v1/subscriptions/1", headers=_build_tma_header(777))
    assert deleted.status_code == 200
    assert fake_session.subscriptions[0].is_active is False
