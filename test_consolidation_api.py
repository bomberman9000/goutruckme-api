from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import ConsolidationPlan, ConsolidationPlanItem, Load, User, UserRole, Vehicle


def _make_user(db, suffix: str) -> User:
    user = User(
        phone=f"+7933{suffix}",
        password_hash=hash_password("pass123"),
        role=UserRole.carrier,
        organization_name=f"Consolidation Carrier {suffix}",
        company=f"Consolidation Carrier {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_consolidation_build_respects_limits_and_returns_explain():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)
    token = create_token({"id": user.id, "phone": user.phone})
    headers = {"Authorization": f"Bearer {token}"}

    vehicle = Vehicle(
        carrier_id=user.id,
        body_type="тент",
        capacity_tons=2.0,
        volume_m3=20.0,
        max_weight_t=2.0,
        max_volume_m3=20.0,
        location_city="Москва",
        location_region="Москва",
        available_from=date.today(),
        status="active",
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)

    loads = [
        Load(
            user_id=user.id,
            from_city="Москва",
            to_city="Тула",
            weight=0.8,
            volume=6.0,
            weight_t=0.8,
            volume_m3=6.0,
            required_body_type="тент",
            price=15000,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        ),
        Load(
            user_id=user.id,
            from_city="Москва",
            to_city="Рязань",
            weight=0.7,
            volume=7.0,
            weight_t=0.7,
            volume_m3=7.0,
            required_body_type="тент",
            price=12000,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        ),
        Load(
            user_id=user.id,
            from_city="Москва",
            to_city="Владимир",
            weight=1.5,
            volume=12.0,
            weight_t=1.5,
            volume_m3=12.0,
            required_body_type="тент",
            price=20000,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        ),
        # Неподходящий груз: превышает лимит по весу.
        Load(
            user_id=user.id,
            from_city="Москва",
            to_city="Калуга",
            weight=3.5,
            volume=8.0,
            weight_t=3.5,
            volume_m3=8.0,
            required_body_type="тент",
            price=30000,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        ),
    ]
    db.add_all(loads)
    db.commit()

    try:
        response = client.post(
            f"/api/consolidation/build/{vehicle.id}",
            headers=headers,
            json={"max_stops": 5, "radius_km": 120, "max_detour_km": 50, "variants": 10},
        )
        assert response.status_code == 200
        payload = response.json()
        plans = payload.get("plans") or []
        assert len(plans) >= 1

        for plan in plans:
            assert float(plan["total_weight"]) <= 2.0 + 1e-6
            assert float(plan["total_volume"]) <= 20.0 + 1e-6
            explain_joined = " ".join(plan.get("explain") or []).lower()
            assert "помещается" in explain_joined
            assert isinstance(plan.get("route"), list)
            assert isinstance(plan.get("route_points"), list)
            assert isinstance(plan.get("why_selected"), list)
            assert float(plan.get("profit_estimate") or 0.0) >= 0.0
    finally:
        db.query(ConsolidationPlanItem).delete()
        db.query(ConsolidationPlan).delete()
        db.query(Load).filter(Load.user_id == user.id).delete()
        db.query(Vehicle).filter(Vehicle.id == vehicle.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_consolidation_confirm_marks_plan_as_confirmed():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)
    token = create_token({"id": user.id, "phone": user.phone})
    headers = {"Authorization": f"Bearer {token}"}

    vehicle = Vehicle(
        carrier_id=user.id,
        body_type="тент",
        capacity_tons=2.0,
        volume_m3=20.0,
        max_weight_t=2.0,
        max_volume_m3=20.0,
        location_city="Москва",
        location_region="Москва",
        available_from=date.today(),
        status="active",
    )
    load = Load(
        user_id=user.id,
        from_city="Москва",
        to_city="Тверь",
        weight=1.0,
        volume=8.0,
        weight_t=1.0,
        volume_m3=8.0,
        required_body_type="тент",
        price=15000,
        loading_date=date.today() + timedelta(days=1),
        status="active",
    )
    db.add(vehicle)
    db.add(load)
    db.commit()
    db.refresh(vehicle)

    try:
        build_resp = client.post(
            f"/api/consolidation/build/{vehicle.id}",
            headers=headers,
            json={"max_stops": 3, "radius_km": 120, "max_detour_km": 40, "variants": 3},
        )
        assert build_resp.status_code == 200
        plans = build_resp.json().get("plans") or []
        assert plans, "build должен вернуть хотя бы один план"
        plan_id = int(plans[0]["plan_id"])

        confirm_resp = client.post(f"/api/consolidation/confirm/{plan_id}", headers=headers)
        assert confirm_resp.status_code == 200
        confirmed = confirm_resp.json()["plan"]
        assert confirmed["status"] == "confirmed"
        assert confirmed["plan_id"] == plan_id
    finally:
        db.query(ConsolidationPlanItem).delete()
        db.query(ConsolidationPlan).delete()
        db.query(Load).filter(Load.user_id == user.id).delete()
        db.query(Vehicle).filter(Vehicle.id == vehicle.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_consolidation_build_requires_auth():
    init_db()
    client = TestClient(app)
    response = client.post("/api/consolidation/build/1", json={"max_stops": 3})
    assert response.status_code == 401
