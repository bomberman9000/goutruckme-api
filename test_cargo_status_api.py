from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import Load, User, UserRole
from app.services.cargo_status import expire_outdated_cargos


def _make_user(db, suffix: str) -> User:
    user = User(
        phone=f"+7921{suffix}",
        password_hash=hash_password("pass123"),
        role=UserRole.forwarder,
        organization_name=f"Cargo Owner {suffix}",
        company=f"Cargo Owner {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_expire_job_marks_past_loading_date_as_expired():
    init_db()
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)

    past_load = Load(
        user_id=user.id,
        from_city="Москва",
        to_city="Казань",
        price=10000,
        status="active",
        loading_date=date.today() - timedelta(days=1),
    )
    db.add(past_load)
    db.commit()
    db.refresh(past_load)

    try:
        affected = expire_outdated_cargos(db)
        db.refresh(past_load)
        assert affected >= 1
        assert past_load.status == "expired"
    finally:
        db.query(Load).filter(Load.id == past_load.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_cargos_api_status_filters_hide_expired_by_default():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)
    token = create_token({"id": user.id, "phone": user.phone})
    headers = {"Authorization": f"Bearer {token}"}

    load_active = Load(
        user_id=user.id,
        from_city="Москва",
        to_city="Тула",
        price=12000,
        status="active",
        loading_date=date.today() + timedelta(days=1),
    )
    load_expired_candidate = Load(
        user_id=user.id,
        from_city="Уфа",
        to_city="Пермь",
        price=9000,
        status="active",
        loading_date=date.today() - timedelta(days=2),
    )
    load_closed = Load(
        user_id=user.id,
        from_city="Самара",
        to_city="Екатеринбург",
        price=18000,
        status="closed",
        loading_date=date.today() + timedelta(days=2),
    )
    db.add_all([load_active, load_expired_candidate, load_closed])
    db.commit()
    db.refresh(load_active)
    db.refresh(load_expired_candidate)
    db.refresh(load_closed)

    try:
        default_resp = client.get("/api/cargos", headers=headers)
        assert default_resp.status_code == 200
        default_ids = {int(row["id"]) for row in default_resp.json()}
        assert load_active.id in default_ids
        assert load_expired_candidate.id not in default_ids
        assert load_closed.id not in default_ids

        expired_resp = client.get("/api/cargos?status=expired", headers=headers)
        assert expired_resp.status_code == 200
        expired_ids = {int(row["id"]) for row in expired_resp.json()}
        assert load_expired_candidate.id in expired_ids

        all_resp = client.get("/api/cargos?status=all", headers=headers)
        assert all_resp.status_code == 200
        all_ids = {int(row["id"]) for row in all_resp.json()}
        assert load_active.id in all_ids
        assert load_expired_candidate.id in all_ids
        assert load_closed.id in all_ids
    finally:
        db.query(Load).filter(Load.user_id == user.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_cargo_ai_risk_flags_do_not_include_past_date_reason():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)
    token = create_token({"id": user.id, "phone": user.phone})
    headers = {"Authorization": f"Bearer {token}"}

    load = Load(
        user_id=user.id,
        from_city="Казань",
        to_city="Москва",
        price=14000,
        status="active",
        loading_date=date.today() - timedelta(days=1),
    )
    db.add(load)
    db.commit()
    db.refresh(load)

    try:
        detail_resp = client.get(f"/api/cargos/{load.id}", headers=headers)
        assert detail_resp.status_code == 200
        payload = detail_resp.json()
        flags = [str(item).lower() for item in (payload.get("ai_flags") or [])]
        assert "date_in_past" not in flags
        assert "past_date" not in flags
        assert "loading_date_in_past" not in flags
    finally:
        db.query(Load).filter(Load.id == load.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()
