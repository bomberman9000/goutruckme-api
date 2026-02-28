from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import escrow as escrow_api
from src.core.config import settings
from src.core.models import (
    AuditEvent,
    Cargo,
    CargoPaymentStatus,
    CargoStatus,
    EscrowDeal,
    EscrowEvent,
    EscrowStatus,
    UserWallet,
)


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
    def __init__(self):
        self.cargos: dict[int, Cargo] = {}
        self.wallets: dict[int, UserWallet] = {}
        self.deals: list[EscrowDeal] = []
        self.events: list[EscrowEvent] = []
        self.audit: list[AuditEvent] = []
        self._pending: list[object] = []
        self._deal_id = 0
        self._event_id = 0
        self._audit_id = 0

    async def get(self, model, key):
        if model is Cargo:
            return self.cargos.get(int(key))
        if model is UserWallet:
            return self.wallets.get(int(key))
        if model is EscrowDeal:
            for deal in self.deals:
                if int(deal.id) == int(key):
                    return deal
            return None
        return None

    async def scalar(self, stmt):
        sql = str(stmt)
        if "FROM escrow_deals" in sql:
            return self.deals[-1] if self.deals else None
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM escrow_deals" in sql:
            rows = list(reversed(self.deals))
        elif "FROM cargos" in sql:
            rows = list(self.cargos.values())
        else:
            rows = []
        return _FakeExecuteResult(rows)

    def add(self, obj):
        self._pending.append(obj)

    async def flush(self):
        for obj in list(self._pending):
            if isinstance(obj, UserWallet):
                self.wallets[int(obj.user_id)] = obj
            elif isinstance(obj, EscrowDeal) and getattr(obj, "id", None) is None:
                self._deal_id += 1
                obj.id = self._deal_id
                self.deals.append(obj)
            elif isinstance(obj, EscrowEvent) and getattr(obj, "id", None) is None:
                self._event_id += 1
                obj.id = self._event_id
                self.events.append(obj)
            elif isinstance(obj, AuditEvent) and getattr(obj, "id", None) is None:
                self._audit_id += 1
                obj.id = self._audit_id
                self.audit.append(obj)
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


def _build_client(fake_session: _FakeSession) -> TestClient:
    app = FastAPI()
    app.include_router(escrow_api.router)
    escrow_api.async_session = lambda: fake_session  # type: ignore[assignment]
    return TestClient(app)


def test_create_escrow_sets_payment_pending(monkeypatch):
    fake_session = _FakeSession()
    fake_session.cargos[1] = Cargo(
        id=1,
        owner_id=555,
        from_city="Москва",
        to_city="Казань",
        cargo_type="тент",
        weight=20,
        price=120000,
        load_date=datetime(2026, 2, 27, tzinfo=UTC),
        status=CargoStatus.NEW,
        payment_status=CargoPaymentStatus.UNSECURED,
    )
    client = _build_client(fake_session)

    async def _clear_cached(_prefix: str) -> None:
        return None

    monkeypatch.setattr(escrow_api, "clear_cached", _clear_cached)

    response = client.post("/api/v1/escrow/1/create", headers=_build_tma_header(555), json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "payment_pending"
    assert body["payment_status"] == "payment_pending"
    assert "/api/v1/escrow/1/pay/mock?" in body["payment_url"]
    assert fake_session.cargos[1].payment_status == CargoPaymentStatus.PAYMENT_PENDING
    assert len(fake_session.deals) == 1
    assert len(fake_session.events) == 1
    assert len(fake_session.audit) == 1


def test_mock_payment_funds_and_release(monkeypatch):
    fake_session = _FakeSession()
    fake_session.cargos[1] = Cargo(
        id=1,
        owner_id=555,
        carrier_id=777,
        from_city="Москва",
        to_city="Казань",
        cargo_type="тент",
        weight=20,
        price=120000,
        load_date=datetime(2026, 2, 27, tzinfo=UTC),
        status=CargoStatus.NEW,
        payment_status=CargoPaymentStatus.UNSECURED,
    )
    client = _build_client(fake_session)

    async def _clear_cached(_prefix: str) -> None:
        return None

    monkeypatch.setattr(escrow_api, "clear_cached", _clear_cached)

    create_resp = client.post("/api/v1/escrow/1/create", headers=_build_tma_header(555), json={})
    assert create_resp.status_code == 200
    payment_url = create_resp.json()["payment_url"]
    parsed = urlparse(payment_url)

    pay_resp = client.get(f"{parsed.path}?{parsed.query}")
    assert pay_resp.status_code == 200
    assert fake_session.cargos[1].payment_status == CargoPaymentStatus.FUNDED
    assert fake_session.wallets[555].balance_rub == 120000
    assert fake_session.wallets[555].frozen_balance_rub == 120000

    delivered = client.post("/api/v1/escrow/1/mark-delivered", headers=_build_tma_header(777))
    assert delivered.status_code == 200
    assert fake_session.deals[0].status == EscrowStatus.DELIVERY_MARKED

    released = client.post("/api/v1/escrow/1/release", headers=_build_tma_header(555))
    assert released.status_code == 200
    assert fake_session.deals[0].status == EscrowStatus.RELEASED
    assert fake_session.cargos[1].payment_status == CargoPaymentStatus.RELEASED
    assert fake_session.wallets[555].frozen_balance_rub == 0
    assert fake_session.wallets[777].balance_rub == 117600
    released_payload = json.loads(fake_session.events[-1].payload_json or "{}")
    assert released_payload["provider"] == "mock_tochka"
    assert released_payload["provider_payout_id"].startswith("mockout_")
