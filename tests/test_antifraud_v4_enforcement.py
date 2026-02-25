from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.antifraud.enforcement import decide_enforcement
from app.api.antifraud import require_antifraud_admin, router
from app.db.database import Base, get_db
from app.models.models import EnforcementDecision


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.mark.asyncio
async def test_blacklist_to_hard_block():
    result = await decide_enforcement(
        deal_id=10001,
        risk_level="low",
        reason_codes=["blacklist_match"],
        flags={"blacklist_match": True},
        network_component_risk=30,
        ml_probability=0.2,
        whitelist_match=False,
        blacklist_match=True,
    )
    assert result["decision"] in {"hard_block", "manual_review"}


@pytest.mark.asyncio
async def test_medium_to_soft_block_with_expiry():
    result = await decide_enforcement(
        deal_id=10002,
        risk_level="medium",
        reason_codes=[],
        flags={},
        network_component_risk=10,
        ml_probability=0.1,
        whitelist_match=False,
        blacklist_match=False,
    )
    assert result["decision"] == "soft_block"
    assert result["expires_at"] is not None


def test_override_endpoint_persists_admin_decision(db_session):
    app = FastAPI()
    app.include_router(router)

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[require_antifraud_admin] = lambda: SimpleNamespace(id=999, role="admin")

    with TestClient(app) as client:
        expires_at = (datetime.utcnow() + timedelta(hours=6)).isoformat()
        resp = client.post(
            "/antifraud/enforcement/deal/777/override",
            json={"decision": "hard_block", "note": "manual admin action", "expires_at": expires_at},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "hard_block"
        assert data["created_by"] == "admin:999"

        row = (
            db_session.query(EnforcementDecision)
            .filter(EnforcementDecision.scope == "deal", EnforcementDecision.scope_id == "777")
            .first()
        )
        assert row is not None
        assert row.decision == "hard_block"
