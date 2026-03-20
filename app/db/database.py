from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# SQLite для разработки, PostgreSQL для продакшена
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./gruzpotok.db")

# Для SQLite нужен check_same_thread=False
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency для получения сессии БД."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Создание таблиц в БД."""
    from app.models.models import (  # noqa: F401
        User, Truck, Vehicle, City, Load, Bid, Message, Deal, Document, DocumentSignSession,
        Shipment, Payment, Attachment, DealSync, DocumentSync, ModerationReview, CompanyTrustStats,
        ConsolidationPlan, ConsolidationPlanItem, RouteRateProfile, CounterpartyList, DealDocRequest,
        ClosedDealStat, CounterpartyRiskHistory, RouteRateStats,
        FraudEntity, FraudEdge, FraudComponent, FraudEntityComponent, FraudSignal, EnforcementDecision, AntifraudModel,
    )
    Base.metadata.create_all(bind=engine)
    _ensure_user_profile_columns()
    _ensure_document_sign_columns()
    _ensure_load_columns()
    _ensure_vehicle_registry_columns()
    _ensure_consolidation_columns()
    _ensure_cities_catalog()
    _ensure_referral_columns()


def _ensure_user_profile_columns() -> None:
    """
    Для существующей SQLite/PostgreSQL базы добавляем недостающие колонки users,
    которые появились после первого релиза.
    """
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    required_columns = {
        "email": "VARCHAR",
        "ogrn": "VARCHAR",
        "city": "VARCHAR",
        "contact_person": "VARCHAR",
        "website": "VARCHAR",
        "edo_enabled": "BOOLEAN DEFAULT FALSE",
        "requisites_verified": "BOOLEAN DEFAULT FALSE",
        "documents_verified": "BOOLEAN DEFAULT FALSE",
    }

    with engine.begin() as conn:
        for column_name, ddl_type in required_columns.items():
            if column_name in existing_columns:
                continue
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {ddl_type}"))


def _ensure_document_sign_columns() -> None:
    inspector = inspect(engine)
    if "documents" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("documents")}
    required_columns = {
        "company_id_from": "INTEGER",
        "company_id_to": "INTEGER",
        "payload_json": "JSON",
        "pdf_draft_path": "VARCHAR(500)",
        "pdf_signed_path": "VARCHAR(500)",
        "updated_at": "TIMESTAMP",
    }

    with engine.begin() as conn:
        for column_name, ddl_type in required_columns.items():
            if column_name in existing_columns:
                continue
            conn.execute(text(f"ALTER TABLE documents ADD COLUMN {column_name} {ddl_type}"))


def _ensure_load_columns() -> None:
    inspector = inspect(engine)
    if "loads" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("loads")}
    with engine.begin() as conn:
        if "loading_date" not in existing_columns:
            conn.execute(text("ALTER TABLE loads ADD COLUMN loading_date DATE"))
        if "status" not in existing_columns:
            conn.execute(text("ALTER TABLE loads ADD COLUMN status VARCHAR(20) DEFAULT 'active'"))
        if "from_city_id" not in existing_columns:
            conn.execute(text("ALTER TABLE loads ADD COLUMN from_city_id INTEGER"))
        if "to_city_id" not in existing_columns:
            conn.execute(text("ALTER TABLE loads ADD COLUMN to_city_id INTEGER"))
        if "from_city_text" not in existing_columns:
            conn.execute(text("ALTER TABLE loads ADD COLUMN from_city_text VARCHAR(255)"))
        if "to_city_text" not in existing_columns:
            conn.execute(text("ALTER TABLE loads ADD COLUMN to_city_text VARCHAR(255)"))
        if "total_price" not in existing_columns:
            conn.execute(text("ALTER TABLE loads ADD COLUMN total_price FLOAT"))
        if "distance_km" not in existing_columns:
            conn.execute(text("ALTER TABLE loads ADD COLUMN distance_km FLOAT"))
        if "rate_per_km" not in existing_columns:
            conn.execute(text("ALTER TABLE loads ADD COLUMN rate_per_km FLOAT"))
        if "loading_time" not in existing_columns:
            conn.execute(text("ALTER TABLE loads ADD COLUMN loading_time VARCHAR(5)"))

        # Миграция legacy-статусов в канонические.
        conn.execute(text("UPDATE loads SET status = 'active' WHERE status IS NULL OR TRIM(status) = ''"))
        conn.execute(text("UPDATE loads SET status = 'active' WHERE LOWER(status) = 'open'"))
        conn.execute(text("UPDATE loads SET status = 'closed' WHERE LOWER(status) = 'covered'"))
        conn.execute(text("UPDATE loads SET status = 'cancelled' WHERE LOWER(status) = 'canceled'"))

        # Для старых записей используем дату создания как дату погрузки.
        conn.execute(
            text(
                "UPDATE loads SET loading_date = DATE(created_at) "
                "WHERE loading_date IS NULL AND created_at IS NOT NULL"
            )
        )
        conn.execute(text("UPDATE loads SET from_city_text = from_city WHERE from_city_text IS NULL"))
        conn.execute(text("UPDATE loads SET to_city_text = to_city WHERE to_city_text IS NULL"))
        conn.execute(text("UPDATE loads SET total_price = price WHERE total_price IS NULL AND price IS NOT NULL"))
        conn.execute(text("UPDATE loads SET price = total_price WHERE price IS NULL AND total_price IS NOT NULL"))
        conn.execute(
            text(
                "UPDATE loads SET rate_per_km = ROUND(CAST((COALESCE(total_price, price) / NULLIF(distance_km, 0)) AS numeric), 1) "
                "WHERE rate_per_km IS NULL AND distance_km IS NOT NULL AND distance_km > 0 "
                "AND COALESCE(total_price, price) IS NOT NULL"
            )
        )
        # Авто-истечение: если дата уже прошла.
        conn.execute(
            text(
                "UPDATE loads SET status = 'expired' "
                "WHERE status = 'active' AND loading_date IS NOT NULL AND loading_date < CURRENT_DATE"
            )
        )

        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_status ON loads (status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_loading_date ON loads (loading_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_status_loading_date ON loads (status, loading_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_from_city_id ON loads (from_city_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_to_city_id ON loads (to_city_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_status_from_to ON loads (status, from_city_id, to_city_id)"))


def _ensure_consolidation_columns() -> None:
    inspector = inspect(engine)

    if "vehicles" in inspector.get_table_names():
        vehicle_columns = {column["name"] for column in inspector.get_columns("vehicles")}
        required_vehicle_columns = {
            "max_weight_t": "FLOAT",
            "max_volume_m3": "FLOAT",
            "start_lat": "FLOAT",
            "start_lon": "FLOAT",
        }
        with engine.begin() as conn:
            for column_name, ddl_type in required_vehicle_columns.items():
                if column_name in vehicle_columns:
                    continue
                conn.execute(text(f"ALTER TABLE vehicles ADD COLUMN {column_name} {ddl_type}"))
            conn.execute(
                text(
                    "UPDATE vehicles SET "
                    "max_weight_t = COALESCE(max_weight_t, capacity_tons), "
                    "max_volume_m3 = COALESCE(max_volume_m3, volume_m3)"
                )
            )

    if "loads" in inspector.get_table_names():
        load_columns = {column["name"] for column in inspector.get_columns("loads")}
        required_load_columns = {
            "weight_t": "FLOAT",
            "volume_m3": "FLOAT",
            "pickup_lat": "FLOAT",
            "pickup_lon": "FLOAT",
            "delivery_lat": "FLOAT",
            "delivery_lon": "FLOAT",
            "required_body_type": "VARCHAR(32)",
            "cargo_kind": "VARCHAR(32)",
            "required_vehicle_kinds": "JSON",
            "required_options": "JSON",
            "adr_class": "VARCHAR(32)",
            "adr_classes": "JSON",
            "crew_required": "BOOLEAN DEFAULT FALSE",
            "container_size": "VARCHAR(8)",
            "needs_crane": "BOOLEAN DEFAULT FALSE",
            "needs_dump": "BOOLEAN DEFAULT FALSE",
            "temp_required": "BOOLEAN DEFAULT FALSE",
            "temp_min": "FLOAT",
            "temp_max": "FLOAT",
            "loading_type": "VARCHAR(32)",
        }
        with engine.begin() as conn:
            for column_name, ddl_type in required_load_columns.items():
                if column_name in load_columns:
                    continue
                conn.execute(text(f"ALTER TABLE loads ADD COLUMN {column_name} {ddl_type}"))
            conn.execute(
                text(
                    "UPDATE loads SET "
                    "weight_t = COALESCE(weight_t, weight), "
                    "volume_m3 = COALESCE(volume_m3, volume), "
                    "crew_required = COALESCE(crew_required, FALSE), "
                    "needs_crane = COALESCE(needs_crane, FALSE), "
                    "needs_dump = COALESCE(needs_dump, FALSE)"
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_cargo_kind ON loads (cargo_kind)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_required_body_type ON loads (required_body_type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_crew_required ON loads (crew_required)"))
            if conn.dialect.name == "postgresql":
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_required_options_gin ON loads USING GIN ((required_options::jsonb))"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_required_vehicle_kinds_gin ON loads USING GIN ((required_vehicle_kinds::jsonb))"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_loads_adr_classes_gin ON loads USING GIN ((adr_classes::jsonb))"))


def _ensure_vehicle_registry_columns() -> None:
    inspector = inspect(engine)
    if "vehicles" not in inspector.get_table_names():
        return

    vehicle_columns = {column["name"] for column in inspector.get_columns("vehicles")}
    required_columns = {
        "owner_user_id": "INTEGER",
        "name": "VARCHAR(120)",
        "vehicle_kind": "VARCHAR(40)",
        "brand": "VARCHAR(64)",
        "model": "VARCHAR(64)",
        "plate_number": "VARCHAR(24)",
        "vin": "VARCHAR(64)",
        "pts_number": "VARCHAR(64)",
        "payload_tons": "FLOAT",
        "length_m": "FLOAT",
        "width_m": "FLOAT",
        "height_m": "FLOAT",
        "loading_types": "JSON",
        "options": "JSON",
        "adr_classes": "JSON",
        "crew_size": "INTEGER DEFAULT 1",
        "temp_min": "FLOAT",
        "temp_max": "FLOAT",
        "city_id": "INTEGER",
        "radius_km": "INTEGER DEFAULT 50",
        "available_to": "DATE",
        "updated_at": "TIMESTAMP",
    }

    with engine.begin() as conn:
        for column_name, ddl_type in required_columns.items():
            if column_name in vehicle_columns:
                continue
            conn.execute(text(f"ALTER TABLE vehicles ADD COLUMN {column_name} {ddl_type}"))

        payload_coalesce = "COALESCE(payload_tons, capacity_tons)"
        if "max_weight_t" in vehicle_columns:
            payload_coalesce = "COALESCE(payload_tons, max_weight_t, capacity_tons)"

        conn.execute(
            text(
                "UPDATE vehicles SET "
                "owner_user_id = COALESCE(owner_user_id, carrier_id), "
                f"payload_tons = {payload_coalesce}, "
                "crew_size = COALESCE(crew_size, 1), "
                "radius_km = COALESCE(radius_km, 50), "
                "updated_at = COALESCE(updated_at, created_at)"
            )
        )

        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_owner_user_id ON vehicles (owner_user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_owner_status ON vehicles (owner_user_id, status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_vehicle_kind ON vehicles (vehicle_kind)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_plate_number ON vehicles (plate_number)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_city_id ON vehicles (city_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_available_to ON vehicles (available_to)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_radius_km ON vehicles (radius_km)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_crew_size ON vehicles (crew_size)"))
        if conn.dialect.name == "postgresql":
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_options_gin ON vehicles USING GIN ((options::jsonb))"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_adr_classes_gin ON vehicles USING GIN ((adr_classes::jsonb))"))

    # Уникальность owner+plate: стараемся включить, но не валим инициализацию,
    # если в legacy-данных уже есть дубликаты.
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_vehicle_owner_plate_idx "
                    "ON vehicles (owner_user_id, plate_number)"
                )
            )
    except Exception:
        pass


def _ensure_cities_catalog() -> None:
    inspector = inspect(engine)
    if "cities" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("cities")}
    with engine.begin() as conn:
        if "population" not in existing_columns:
            conn.execute(text("ALTER TABLE cities ADD COLUMN population INTEGER"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cities_name_norm ON cities (name_norm)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cities_country ON cities (country)"))

    from app.services.geo import seed_default_cities

    db = SessionLocal()
    try:
        seed_default_cities(db)
    finally:
        db.close()

def _ensure_referral_columns() -> None:
    from sqlalchemy import text
    db = SessionLocal()
    try:
        db.execute(text('ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR(20) UNIQUE'))
        db.execute(text('ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by INTEGER REFERENCES users(id) ON DELETE SET NULL'))
        db.execute(text('ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER NOT NULL DEFAULT 0'))
        db.execute(text('ALTER TABLE users ADD COLUMN IF NOT EXISTS pro_until TIMESTAMP'))
        db.execute(text('CREATE INDEX IF NOT EXISTS ix_users_referral_code ON users(referral_code)'))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f'_ensure_referral_columns error: {e}')
    finally:
        db.close()
