from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import internal as internal_api


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(internal_api.router)
    return TestClient(app)


def test_internal_notify_user_forbidden_without_valid_token(monkeypatch):
    monkeypatch.setattr(internal_api.settings, "internal_api_token", "secret-token")
    client = _build_client()

    response = client.post(
        "/internal/notify-user",
        json={"user_id": 1, "message": "test"},
        headers={"X-Internal-Token": "wrong"},
    )
    assert response.status_code == 403


def test_internal_notify_user_ok(monkeypatch):
    monkeypatch.setattr(internal_api.settings, "internal_api_token", "secret-token")

    async def _fake_send(payload):
        return {"ok": True, "message_id": 777}

    monkeypatch.setattr(internal_api, "_send_user_message", _fake_send)
    client = _build_client()

    response = client.post(
        "/internal/notify-user",
        json={"user_id": 55, "message": "hello", "action_link": "https://example.com"},
        headers={"X-Internal-Token": "secret-token"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["message_id"] == 777


def test_internal_notify_alias_ok(monkeypatch):
    monkeypatch.setattr(internal_api.settings, "internal_api_token", "secret-token")

    async def _fake_send(payload):
        return {"ok": True, "message_id": 778}

    monkeypatch.setattr(internal_api, "_send_user_message", _fake_send)
    client = _build_client()

    response = client.post(
        "/internal/notify",
        json={"user_id": 56, "message": "hello"},
        headers={"X-Internal-Token": "secret-token"},
    )
    assert response.status_code == 200
    assert response.json()["message_id"] == 778


def test_internal_sync_data_notifies_default_message(monkeypatch):
    monkeypatch.setattr(internal_api.settings, "internal_api_token", "secret-token")

    captured = {}

    async def _fake_send(payload):
        captured["user_id"] = payload.user_id
        captured["message"] = payload.message
        return {"ok": True, "message_id": 999}

    monkeypatch.setattr(internal_api, "_send_user_message", _fake_send)
    client = _build_client()

    response = client.post(
        "/internal/sync-data",
        json={
            "event_id": "evt-1",
            "event_type": "cargo.created",
            "source": "gruzpotok-api",
            "user_id": 101,
            "order": {
                "id": "5001",
                "from_city": "Самара",
                "to_city": "Москва",
                "source": "gruzpotok-api",
            },
        },
        headers={"X-Internal-Token": "secret-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["notified"] is True
    assert captured["user_id"] == 101
    assert "Новый груз" in captured["message"]


def test_internal_sync_alias_ok(monkeypatch):
    monkeypatch.setattr(internal_api.settings, "internal_api_token", "secret-token")

    async def _fake_send(payload):
        return {"ok": True, "message_id": 1000}

    monkeypatch.setattr(internal_api, "_send_user_message", _fake_send)
    client = _build_client()

    response = client.post(
        "/api/sync",
        json={"event_id": "evt-2", "event_type": "search.no_match", "source": "gruzpotok-api"},
        headers={"X-Internal-Token": "secret-token"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_internal_event_sends_to_explicit_target(monkeypatch):
    monkeypatch.setattr(internal_api.settings, "internal_api_token", "secret-token")

    captured = {}

    async def _fake_send(payload):
        captured["user_id"] = payload.user_id
        captured["message"] = payload.message
        return {"ok": True, "message_id": 1001}

    monkeypatch.setattr(internal_api, "_send_user_message", _fake_send)
    client = _build_client()

    response = client.post(
        "/internal/event",
        json={
            "event_type": "carrier_selected",
            "source": "gruzpotok-api",
            "data": {"telegram_id": 4242, "deal_id": 77},
        },
        headers={"X-Internal-Token": "secret-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["sent_count"] == 1
    assert captured["user_id"] == 4242
    assert "77" in captured["message"]
