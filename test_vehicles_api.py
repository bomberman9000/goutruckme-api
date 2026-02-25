from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import City, Load, User, UserRole, Vehicle


def _ensure_city(
    db,
    name: str,
    *,
    region: str = "Самарская область",
    lat: float = 53.1959,
    lon: float = 50.1000,
) -> City:
    city = db.query(City).filter(City.name == name).first()
    if city:
        return city
    city = City(name=name, name_norm=name.lower().replace("ё", "е"), region=region, country="RU", lat=lat, lon=lon)
    db.add(city)
    db.commit()
    db.refresh(city)
    return city


def _create_user(db, *, phone: str, role: UserRole, org_name: str) -> User:
    user = User(
        phone=phone,
        password_hash=hash_password("pass123"),
        role=role,
        organization_name=org_name,
        company=org_name,
        trust_level="new",
        verified=False,
        rating=4.2,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth_headers(user: User) -> dict[str, str]:
    token = create_token(
        {
            "id": user.id,
            "sub": str(user.id),
            "phone": user.phone,
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
            "name": user.organization_name,
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _create_vehicle_payload(city_id: int, plate: str) -> dict:
    return {
        "vehicle_kind": "EUROFURA_TENT_20T",
        "plate_number": plate,
        "city_id": city_id,
        "payload_tons": 20,
        "volume_m3": 90,
        "loading_types": ["back", "side"],
        "options": ["straps", "liftgate"],
        "available_from": "2026-02-20",
    }


def test_vehicle_list_requires_auth_owner_scope_and_pagination():
    init_db()
    client = TestClient(app)
    db = SessionLocal()
    suffix = uuid4().hex[:8]

    owner1 = _create_user(db, phone=f"+7900{suffix}1", role=UserRole.carrier, org_name=f"CarrierA-{suffix}")
    owner2 = _create_user(db, phone=f"+7900{suffix}2", role=UserRole.carrier, org_name=f"CarrierB-{suffix}")
    admin = _create_user(db, phone=f"+7900{suffix}3", role=UserRole.admin, org_name=f"Admin-{suffix}")
    samara = _ensure_city(db, f"Самара-{suffix}", lat=53.1959, lon=50.1000)

    try:
        r1 = client.post("/api/vehicles", json=_create_vehicle_payload(samara.id, "а111аа163"), headers=_auth_headers(owner1))
        assert r1.status_code == 201, r1.text
        vehicle1_id = int(r1.json()["id"])

        r2 = client.post("/api/vehicles", json=_create_vehicle_payload(samara.id, "в222вв163"), headers=_auth_headers(owner2))
        assert r2.status_code == 201, r2.text
        vehicle2_id = int(r2.json()["id"])

        unauth = client.get("/api/vehicles")
        assert unauth.status_code == 401

        owner_list = client.get("/api/vehicles?page=1&size=100", headers=_auth_headers(owner1))
        assert owner_list.status_code == 200
        owner_payload = owner_list.json()
        assert isinstance(owner_payload.get("items"), list)
        assert owner_payload["page"] == 1
        assert owner_payload["size"] == 100
        owner_ids = {int(item["id"]) for item in owner_payload["items"]}
        assert vehicle1_id in owner_ids
        assert vehicle2_id not in owner_ids

        forbidden_scope = client.get("/api/vehicles?scope=all", headers=_auth_headers(owner1))
        assert forbidden_scope.status_code == 403

        admin_scope = client.get("/api/vehicles?scope=all&page=1&size=100", headers=_auth_headers(admin))
        assert admin_scope.status_code == 200
        admin_ids = {int(item["id"]) for item in admin_scope.json()["items"]}
        assert vehicle1_id in admin_ids
        assert vehicle2_id in admin_ids

        forbidden_detail = client.get(f"/api/vehicles/{vehicle2_id}", headers=_auth_headers(owner1))
        assert forbidden_detail.status_code == 403

        admin_detail = client.get(f"/api/vehicles/{vehicle2_id}", headers=_auth_headers(admin))
        assert admin_detail.status_code == 200
        assert int(admin_detail.json()["id"]) == vehicle2_id
    finally:
        db.query(Load).filter(Load.user_id.in_([owner1.id, owner2.id, admin.id])).delete(synchronize_session=False)
        db.query(Vehicle).filter(Vehicle.owner_user_id.in_([owner1.id, owner2.id, admin.id])).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([owner1.id, owner2.id, admin.id])).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_vehicle_dicts_endpoint_available_without_auth():
    init_db()
    client = TestClient(app)
    response = client.get("/api/dicts/vehicles")
    assert response.status_code == 200
    payload = response.json()
    assert "vehicle_kinds" in payload
    assert "loading_types" in payload
    assert "vehicle_options" in payload
    assert "adr_classes" in payload
    assert "cargo_kinds" in payload
    assert "PALLETIZED" in payload["cargo_kinds"]


def test_vehicle_matching_supports_liftgate_adr_multi_class_crew_and_cache():
    init_db()
    client = TestClient(app)
    db = SessionLocal()
    suffix = uuid4().hex[:8]

    carrier = _create_user(db, phone=f"+7911{suffix}", role=UserRole.carrier, org_name=f"CarrierMatch-{suffix}")
    samara = _ensure_city(db, f"Смр-{suffix}", lat=53.1959, lon=50.1000)
    kazan = _ensure_city(db, f"Кзн-{suffix}", region="Татарстан", lat=55.7963, lon=49.1088)

    try:
        create_vehicle = client.post(
            "/api/vehicles",
            json={
                "vehicle_kind": "EUROFURA_TENT_20T",
                "plate_number": "м123мм163",
                "city_id": samara.id,
                "payload_tons": 20,
                "volume_m3": 90,
                "loading_types": ["side", "back"],
                "options": ["liftgate", "adr", "straps"],
                "adr_classes": ["3", "8"],
                "crew_size": 2,
                "available_from": "2026-02-20",
            },
            headers=_auth_headers(carrier),
        )
        assert create_vehicle.status_code == 201, create_vehicle.text
        vehicle_id = int(create_vehicle.json()["id"])

        good_load = Load(
            user_id=carrier.id,
            from_city_id=samara.id,
            to_city_id=kazan.id,
            from_city=samara.name,
            to_city=kazan.name,
            weight=3.0,
            volume=10.0,
            weight_t=3.0,
            volume_m3=10.0,
            required_body_type="тент",
            required_vehicle_kinds=["EUROFURA_TENT_20T"],
            required_options=["liftgate"],
            adr_classes=["8"],
            crew_required=True,
            loading_type="side",
            price=65000,
            total_price=65000,
            distance_km=340,
            rate_per_km=191.2,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        )
        bad_adr_load = Load(
            user_id=carrier.id,
            from_city_id=samara.id,
            to_city_id=kazan.id,
            from_city=samara.name,
            to_city=kazan.name,
            weight=2.0,
            volume=8.0,
            weight_t=2.0,
            volume_m3=8.0,
            required_body_type="тент",
            required_options=["liftgate"],
            adr_classes=["2"],
            crew_required=True,
            loading_type="side",
            price=48000,
            total_price=48000,
            distance_km=340,
            rate_per_km=141.2,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        )
        bad_option_load = Load(
            user_id=carrier.id,
            from_city_id=samara.id,
            to_city_id=kazan.id,
            from_city=samara.name,
            to_city=kazan.name,
            weight=2.0,
            volume=7.0,
            weight_t=2.0,
            volume_m3=7.0,
            required_body_type="тент",
            required_options=["crane"],
            crew_required=False,
            loading_type="back",
            price=47000,
            total_price=47000,
            distance_km=340,
            rate_per_km=138.2,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        )
        db.add_all([good_load, bad_adr_load, bad_option_load])
        db.commit()
        db.refresh(good_load)
        db.refresh(bad_adr_load)
        db.refresh(bad_option_load)

        headers = _auth_headers(carrier)
        first = client.get(f"/api/vehicles/{vehicle_id}/matching-cargos?limit=50", headers=headers)
        assert first.status_code == 200, first.text
        payload1 = first.json()
        assert payload1["cache_hit"] is False
        ids1 = {int(item["load_id"]) for item in payload1["items"]}
        assert good_load.id in ids1
        assert bad_adr_load.id not in ids1
        assert bad_option_load.id not in ids1
        matched_item = next(item for item in payload1["items"] if int(item["load_id"]) == good_load.id)
        assert int(matched_item["cargo_id"]) == good_load.id
        assert isinstance(matched_item.get("compatibility"), dict)
        assert isinstance(matched_item["compatibility"].get("reasons"), list)
        assert matched_item["compatibility"]["reasons"]
        score_components = matched_item.get("score_components") or {}
        assert "proximity_score" in score_components
        assert "fill_score" in score_components
        assert "profit_score" in score_components
        assert "trust_bonus" in score_components
        assert "risk_penalty" in score_components
        assert isinstance(matched_item.get("reasons"), list) and matched_item["reasons"]
        assert "rejected_reasons" in matched_item

        second = client.get(f"/api/vehicles/{vehicle_id}/matching-cargos?limit=50", headers=headers)
        assert second.status_code == 200
        payload2 = second.json()
        assert payload2["cache_hit"] is True

        with_rejected = client.get(
            f"/api/vehicles/{vehicle_id}/matching-cargos?limit=50&include_rejected=true",
            headers=headers,
        )
        assert with_rejected.status_code == 200
        rejected_ids = {int(item["load_id"]) for item in with_rejected.json().get("rejected_items", [])}
        assert bad_adr_load.id in rejected_ids or bad_option_load.id in rejected_ids
    finally:
        db.query(Load).filter(Load.user_id == carrier.id).delete(synchronize_session=False)
        db.query(Vehicle).filter(Vehicle.owner_user_id == carrier.id).delete(synchronize_session=False)
        db.query(User).filter(User.id == carrier.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_vehicle_matching_oversize_and_bulk_rules():
    init_db()
    client = TestClient(app)
    db = SessionLocal()
    suffix = uuid4().hex[:8]

    carrier = _create_user(db, phone=f"+7955{suffix}", role=UserRole.carrier, org_name=f"CarrierRules-{suffix}")
    samara = _ensure_city(db, f"RuleSam-{suffix}", lat=53.1959, lon=50.1000)
    kazan = _ensure_city(db, f"RuleKaz-{suffix}", region="Татарстан", lat=55.7963, lon=49.1088)

    try:
        tral = client.post(
            "/api/vehicles",
            json={
                "vehicle_kind": "LOWBOY_TRAL",
                "plate_number": "н001гр163",
                "city_id": samara.id,
                "payload_tons": 40,
                "volume_m3": 80,
                "options": ["oversize_ok", "straps"],
                "available_from": "2026-02-20",
            },
            headers=_auth_headers(carrier),
        )
        assert tral.status_code == 201, tral.text
        tral_id = int(tral.json()["id"])

        dump = client.post(
            "/api/vehicles",
            json={
                "vehicle_kind": "DUMP_TRUCK",
                "plate_number": "н002гр163",
                "city_id": samara.id,
                "payload_tons": 20,
                "volume_m3": 35,
                "available_from": "2026-02-20",
            },
            headers=_auth_headers(carrier),
        )
        assert dump.status_code == 201, dump.text
        dump_id = int(dump.json()["id"])

        tent = client.post(
            "/api/vehicles",
            json={
                "vehicle_kind": "EUROFURA_TENT_20T",
                "plate_number": "н003гр163",
                "city_id": samara.id,
                "payload_tons": 20,
                "volume_m3": 90,
                "available_from": "2026-02-20",
            },
            headers=_auth_headers(carrier),
        )
        assert tent.status_code == 201, tent.text
        tent_id = int(tent.json()["id"])

        oversize_load = Load(
            user_id=carrier.id,
            from_city_id=samara.id,
            to_city_id=kazan.id,
            from_city=samara.name,
            to_city=kazan.name,
            weight_t=22.0,
            volume_m3=28.0,
            cargo_kind="OVERSIZE",
            price=145000,
            total_price=145000,
            distance_km=340,
            rate_per_km=426.5,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        )
        bulk_load = Load(
            user_id=carrier.id,
            from_city_id=samara.id,
            to_city_id=kazan.id,
            from_city=samara.name,
            to_city=kazan.name,
            weight_t=12.0,
            volume_m3=14.0,
            cargo_kind="BULK",
            needs_dump=True,
            price=90000,
            total_price=90000,
            distance_km=340,
            rate_per_km=264.7,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        )
        general_load = Load(
            user_id=carrier.id,
            from_city_id=samara.id,
            to_city_id=kazan.id,
            from_city=samara.name,
            to_city=kazan.name,
            weight_t=5.0,
            volume_m3=10.0,
            cargo_kind="GENERAL",
            price=52000,
            total_price=52000,
            distance_km=340,
            rate_per_km=152.9,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        )
        db.add_all([oversize_load, bulk_load, general_load])
        db.commit()
        db.refresh(oversize_load)
        db.refresh(bulk_load)
        db.refresh(general_load)

        headers = _auth_headers(carrier)

        tral_match = client.get(f"/api/vehicles/{tral_id}/matching-cargos?limit=50", headers=headers)
        assert tral_match.status_code == 200
        tral_ids = {int(item["load_id"]) for item in tral_match.json()["items"]}
        assert oversize_load.id in tral_ids

        dump_match = client.get(f"/api/vehicles/{dump_id}/matching-cargos?limit=50&include_rejected=true", headers=headers)
        assert dump_match.status_code == 200
        dump_ids = {int(item["load_id"]) for item in dump_match.json()["items"]}
        assert bulk_load.id in dump_ids
        assert general_load.id not in dump_ids

        tent_match = client.get(f"/api/vehicles/{tent_id}/matching-cargos?limit=50&include_rejected=true", headers=headers)
        assert tent_match.status_code == 200
        tent_ids = {int(item["load_id"]) for item in tent_match.json()["items"]}
        assert oversize_load.id not in tent_ids
        assert bulk_load.id not in tent_ids
    finally:
        db.query(Load).filter(Load.user_id == carrier.id).delete(synchronize_session=False)
        db.query(Vehicle).filter(Vehicle.owner_user_id == carrier.id).delete(synchronize_session=False)
        db.query(User).filter(User.id == carrier.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_vehicle_plate_must_be_unique_within_owner():
    init_db()
    client = TestClient(app)
    db = SessionLocal()
    suffix = uuid4().hex[:8]

    owner = _create_user(db, phone=f"+7922{suffix}", role=UserRole.carrier, org_name=f"Plate-{suffix}")
    samara = _ensure_city(db, f"Плейт-{suffix}", lat=53.1959, lon=50.1000)

    payload = {
        "vehicle_kind": "VAN_UP_TO_3_5T",
        "plate_number": "о777оо63",
        "city_id": samara.id,
        "payload_tons": 2.0,
        "volume_m3": 16.0,
        "available_from": "2026-02-20",
    }

    try:
        headers = _auth_headers(owner)
        first = client.post("/api/vehicles", json=payload, headers=headers)
        assert first.status_code == 201, first.text

        second = client.post("/api/vehicles", json=payload, headers=headers)
        assert second.status_code == 409
    finally:
        db.query(Vehicle).filter(Vehicle.owner_user_id == owner.id).delete(synchronize_session=False)
        db.query(User).filter(User.id == owner.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_vehicle_matching_refrigerated_cargo_only_refrigerator_with_temp_range():
    init_db()
    client = TestClient(app)
    db = SessionLocal()
    suffix = uuid4().hex[:8]

    carrier = _create_user(db, phone=f"+7933{suffix}", role=UserRole.carrier, org_name=f"CarrierRef-{suffix}")
    samara = _ensure_city(db, f"RefSam-{suffix}", lat=53.1959, lon=50.1000)
    kazan = _ensure_city(db, f"RefKaz-{suffix}", region="Татарстан", lat=55.7963, lon=49.1088)

    try:
        fridge = client.post(
            "/api/vehicles",
            json={
                "vehicle_kind": "REFRIGERATOR",
                "plate_number": "р001ре163",
                "city_id": samara.id,
                "payload_tons": 10,
                "volume_m3": 40,
                "options": ["reefer_unit"],
                "temp_min": -25,
                "temp_max": 5,
                "available_from": "2026-02-20",
            },
            headers=_auth_headers(carrier),
        )
        assert fridge.status_code == 201, fridge.text
        fridge_id = int(fridge.json()["id"])

        tent = client.post(
            "/api/vehicles",
            json={
                "vehicle_kind": "EUROFURA_TENT_20T",
                "plate_number": "р002ре163",
                "city_id": samara.id,
                "payload_tons": 20,
                "volume_m3": 90,
                "options": ["straps"],
                "available_from": "2026-02-20",
            },
            headers=_auth_headers(carrier),
        )
        assert tent.status_code == 201, tent.text
        tent_id = int(tent.json()["id"])

        cold_load = Load(
            user_id=carrier.id,
            from_city_id=samara.id,
            to_city_id=kazan.id,
            from_city=samara.name,
            to_city=kazan.name,
            weight_t=2.0,
            volume_m3=12.0,
            required_body_type="реф",
            temp_required=True,
            temp_min=-18.0,
            temp_max=-5.0,
            price=51000,
            total_price=51000,
            distance_km=340,
            rate_per_km=150.0,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        )
        db.add(cold_load)
        db.commit()
        db.refresh(cold_load)

        headers = _auth_headers(carrier)
        fridge_match = client.get(f"/api/vehicles/{fridge_id}/matching-cargos?limit=30", headers=headers)
        assert fridge_match.status_code == 200
        fridge_ids = {int(item["load_id"]) for item in fridge_match.json()["items"]}
        assert cold_load.id in fridge_ids

        tent_match = client.get(f"/api/vehicles/{tent_id}/matching-cargos?limit=30&include_rejected=true", headers=headers)
        assert tent_match.status_code == 200
        tent_ids = {int(item["load_id"]) for item in tent_match.json()["items"]}
        assert cold_load.id not in tent_ids
    finally:
        db.query(Load).filter(Load.user_id == carrier.id).delete(synchronize_session=False)
        db.query(Vehicle).filter(Vehicle.owner_user_id == carrier.id).delete(synchronize_session=False)
        db.query(User).filter(User.id == carrier.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_vehicle_matching_timber_allows_timber_truck_and_flatbed_with_conics():
    init_db()
    client = TestClient(app)
    db = SessionLocal()
    suffix = uuid4().hex[:8]

    carrier = _create_user(db, phone=f"+7944{suffix}", role=UserRole.carrier, org_name=f"CarrierTimber-{suffix}")
    samara = _ensure_city(db, f"TimSam-{suffix}", lat=53.1959, lon=50.1000)
    kazan = _ensure_city(db, f"TimKaz-{suffix}", region="Татарстан", lat=55.7963, lon=49.1088)

    try:
        timber_vehicle = client.post(
            "/api/vehicles",
            json={
                "vehicle_kind": "TIMBER_TRUCK",
                "plate_number": "л001лс163",
                "city_id": samara.id,
                "payload_tons": 18,
                "volume_m3": 55,
                "options": ["conics", "straps"],
                "available_from": "2026-02-20",
            },
            headers=_auth_headers(carrier),
        )
        assert timber_vehicle.status_code == 201, timber_vehicle.text
        timber_id = int(timber_vehicle.json()["id"])

        flatbed_conics = client.post(
            "/api/vehicles",
            json={
                "vehicle_kind": "FLATBED",
                "plate_number": "л002лс163",
                "city_id": samara.id,
                "payload_tons": 18,
                "volume_m3": 55,
                "options": ["conics", "straps"],
                "available_from": "2026-02-20",
            },
            headers=_auth_headers(carrier),
        )
        assert flatbed_conics.status_code == 201, flatbed_conics.text
        flatbed_conics_id = int(flatbed_conics.json()["id"])

        flatbed_plain = client.post(
            "/api/vehicles",
            json={
                "vehicle_kind": "FLATBED",
                "plate_number": "л003лс163",
                "city_id": samara.id,
                "payload_tons": 18,
                "volume_m3": 55,
                "options": ["straps"],
                "available_from": "2026-02-20",
            },
            headers=_auth_headers(carrier),
        )
        assert flatbed_plain.status_code == 201, flatbed_plain.text
        flatbed_plain_id = int(flatbed_plain.json()["id"])

        timber_load = Load(
            user_id=carrier.id,
            from_city_id=samara.id,
            to_city_id=kazan.id,
            from_city=samara.name,
            to_city=kazan.name,
            weight_t=8.0,
            volume_m3=25.0,
            required_body_type="коники",
            required_options=["conics"],
            price=72000,
            total_price=72000,
            distance_km=340,
            rate_per_km=212.0,
            loading_date=date.today() + timedelta(days=1),
            status="active",
        )
        db.add(timber_load)
        db.commit()
        db.refresh(timber_load)

        headers = _auth_headers(carrier)
        timber_match = client.get(f"/api/vehicles/{timber_id}/matching-cargos?limit=50", headers=headers)
        assert timber_match.status_code == 200
        timber_ids = {int(item["load_id"]) for item in timber_match.json()["items"]}
        assert timber_load.id in timber_ids

        flatbed_ok_match = client.get(f"/api/vehicles/{flatbed_conics_id}/matching-cargos?limit=50", headers=headers)
        assert flatbed_ok_match.status_code == 200
        flatbed_ok_ids = {int(item["load_id"]) for item in flatbed_ok_match.json()["items"]}
        assert timber_load.id in flatbed_ok_ids

        flatbed_bad_match = client.get(
            f"/api/vehicles/{flatbed_plain_id}/matching-cargos?limit=50&include_rejected=true",
            headers=headers,
        )
        assert flatbed_bad_match.status_code == 200
        flatbed_bad_ids = {int(item["load_id"]) for item in flatbed_bad_match.json()["items"]}
        assert timber_load.id not in flatbed_bad_ids
    finally:
        db.query(Load).filter(Load.user_id == carrier.id).delete(synchronize_session=False)
        db.query(Vehicle).filter(Vehicle.owner_user_id == carrier.id).delete(synchronize_session=False)
        db.query(User).filter(User.id == carrier.id).delete(synchronize_session=False)
        db.commit()
        db.close()
