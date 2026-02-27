from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.core.models import Cargo, CargoStatus, RouteSubscription, UserVehicle
from src.core.services import notification_dispatcher as dispatcher


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _FakeSession:
    def __init__(self, cargo: Cargo, subs: list[RouteSubscription], vehicles: list[UserVehicle]):
        self.cargo = cargo
        self.subs = subs
        self.vehicles = vehicles
        self.commits = 0

    async def get(self, model, key):
        if model is Cargo and int(key) == int(self.cargo.id):
            return self.cargo
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM route_subscriptions" in sql:
            return _FakeExecuteResult(self.subs)
        if "FROM user_vehicles" in sql:
            return _FakeExecuteResult(self.vehicles)
        return _FakeExecuteResult([])

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
async def test_notify_matching_carriers_combines_subscriptions_and_fleet(monkeypatch):
    cargo = Cargo(
        id=7,
        owner_id=100,
        from_city="Москва",
        to_city="Казань",
        cargo_type="тент",
        weight=10,
        price=120000,
        load_date=datetime(2026, 2, 27),
        status=CargoStatus.NEW,
    )
    subs = [
        RouteSubscription(id=1, user_id=200, from_city="Москва", to_city="Казань", is_active=True),
        RouteSubscription(id=2, user_id=100, from_city="Москва", to_city="Казань", is_active=True),
    ]
    vehicles = [
        UserVehicle(
            id=1,
            user_id=300,
            body_type="тент",
            capacity_tons=20,
            location_city="Москва",
            is_available=True,
        ),
        UserVehicle(
            id=2,
            user_id=200,
            body_type="тент",
            capacity_tons=20,
            location_city="Москва",
            is_available=True,
        ),
        UserVehicle(
            id=3,
            user_id=400,
            body_type="реф",
            capacity_tons=20,
            location_city="Москва",
            is_available=True,
        ),
    ]
    fake_session = _FakeSession(cargo, subs, vehicles)
    sent_to: list[int] = []

    monkeypatch.setattr(dispatcher, "async_session", lambda: fake_session)

    async def _dispatch(cargo_obj: Cargo, user_ids: list[int]) -> int:
        assert cargo_obj.id == 7
        sent_to.extend(user_ids)
        return len(user_ids)

    monkeypatch.setattr(dispatcher, "dispatch_cargo_notification", _dispatch)

    sent = await dispatcher.notify_matching_carriers(7)

    assert sent == 2
    assert sorted(sent_to) == [200, 300]
    assert fake_session.cargo.notified_at is not None
    assert fake_session.commits == 1
