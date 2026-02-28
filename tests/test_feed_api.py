from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import feed as feed_api
from src.core.models import CallLog, ParserIngestEvent, User
from src.core.config import settings


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
    def __init__(self, rows, users, call_logs):
        self._rows = rows
        self._users = users
        self._call_logs = call_logs

    async def execute(self, _stmt):
        return _FakeExecuteResult(self._rows)

    async def get(self, model, key):
        if model is User:
            return self._users.get(int(key))
        if model is ParserIngestEvent:
            for row in self._rows:
                if int(row.id) == int(key):
                    return row
            return None
        return None

    def add(self, obj):
        if isinstance(obj, CallLog):
            obj.id = len(self._call_logs) + 1
            if not getattr(obj, "created_at", None):
                obj.created_at = datetime.now(UTC)
            self._call_logs.append(obj)

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _build_client(rows, *, users=None, call_logs=None) -> TestClient:
    app = FastAPI()
    app.include_router(feed_api.router)
    users = users or {}
    call_logs = call_logs if call_logs is not None else []
    feed_api.async_session = lambda: _FakeSession(rows, users, call_logs)  # type: ignore[assignment]
    app.state.call_logs = call_logs
    return TestClient(app)


def _build_tma_header(user_id: int, *, bot_token: str | None = None) -> dict[str, str]:
    token = bot_token or settings.bot_token
    payload_user = json.dumps({"id": user_id}, separators=(",", ":"), ensure_ascii=False)
    pairs = {
        "auth_date": str(int(time.time())),
        "user": payload_user,
    }
    data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    sign = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    init_data = urlencode({**pairs, "hash": sign})
    return {"Authorization": f"tma {init_data}"}


def _row(row_id: int, verdict: str = "green"):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=row_id,
        stream_entry_id=f"{row_id}-0",
        from_city="Москва",
        to_city="Казань",
        body_type="тент",
        rate_rub=100000,
        weight_t=20.0,
        phone="+79991112233",
        trust_score=72,
        trust_verdict=verdict,
        trust_comment="ok",
        provider="stub",
        status="synced",
        created_at=now,
        load_date=None,
        load_time=None,
        cargo_description=None,
        payment_terms=None,
        is_direct_customer=None,
        dimensions=None,
        is_hot_deal=False,
        suggested_response=None,
        phone_blacklisted=False,
        inn=None,
        from_lat=None,
        from_lon=None,
        to_lat=None,
        to_lon=None,
        details_json=None,
    )


def test_feed_pagination_cursor():
    client = _build_client([_row(9), _row(8), _row(7)])
    response = client.get("/api/v1/feed?limit=2")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"] == 8


def test_feed_accepts_invalid_verdict_values():
    client = _build_client([])
    response = client.get("/api/v1/feed?verdict=invalid")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_feed_masks_phone_without_premium():
    client = _build_client([_row(5)])
    response = client.get("/api/v1/feed")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["phone_masked"] is True
    assert item["can_view_contact"] is False
    assert item["phone"].endswith("****")


def test_feed_shows_phone_with_premium():
    premium_user = SimpleNamespace(
        id=101,
        is_premium=True,
        premium_until=datetime.now() + timedelta(days=1),
    )
    client = _build_client([_row(6)], users={101: premium_user})
    response = client.get("/api/v1/feed", headers=_build_tma_header(101))
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["phone_masked"] is False
    assert item["can_view_contact"] is True
    assert item["phone"] == "+79991112233"


def test_feed_uses_distance_hint_from_details_json():
    row = _row(10)
    row.details_json = '{"distance_km": 2000}'
    client = _build_client([row])

    response = client.get("/api/v1/feed")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["distance_km"] == 2000
    assert item["rate_per_km"] == 50.0


def test_feed_click_logs_event():
    logs = []
    client = _build_client([_row(9)], call_logs=logs)
    response = client.post("/api/v1/feed/9/click", headers=_build_tma_header(777))
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(logs) == 1
    assert logs[0].user_id == 777
    assert logs[0].cargo_id == 9


def test_feed_click_requires_tma_authorization():
    client = _build_client([_row(9)], call_logs=[])
    response = client.post("/api/v1/feed/9/click")
    assert response.status_code == 401
