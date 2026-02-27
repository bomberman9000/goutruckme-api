from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.core.auth.telegram_tma import (
    TelegramTMAUser,
    get_optional_tma_user,
    get_required_tma_user,
)
from src.core.config import settings


def _build_init_data(user_id: int, *, bot_token: str | None = None) -> str:
    token = bot_token or settings.bot_token
    payload_user = json.dumps({"id": user_id}, separators=(",", ":"), ensure_ascii=False)
    pairs = {
        "auth_date": str(int(time.time())),
        "user": payload_user,
    }
    data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    sign = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**pairs, "hash": sign})


def _build_client() -> TestClient:
    app = FastAPI()

    @app.get("/required")
    async def required_route(tma_user: TelegramTMAUser = Depends(get_required_tma_user)):
        return {"user_id": tma_user.user_id}

    @app.get("/optional")
    async def optional_route(tma_user: TelegramTMAUser | None = Depends(get_optional_tma_user)):
        return {"user_id": tma_user.user_id if tma_user else None}

    return TestClient(app)


def test_required_tma_accepts_authorization_header():
    client = _build_client()
    init_data = _build_init_data(321)

    response = client.get("/required", headers={"Authorization": f"tma {init_data}"})

    assert response.status_code == 200
    assert response.json() == {"user_id": 321}


def test_required_tma_accepts_legacy_init_data_header():
    client = _build_client()
    init_data = _build_init_data(654)

    response = client.get("/required", headers={"X-Telegram-Init-Data": init_data})

    assert response.status_code == 200
    assert response.json() == {"user_id": 654}


def test_optional_tma_without_headers_returns_none():
    client = _build_client()

    response = client.get("/optional")

    assert response.status_code == 200
    assert response.json() == {"user_id": None}
