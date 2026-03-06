CREATE TABLE IF NOT EXISTS available_trucks (
    id SERIAL PRIMARY KEY,
    source VARCHAR(64) NOT NULL,
    external_id VARCHAR(128) NOT NULL,
    truck_type VARCHAR(64),
    capacity_tons FLOAT,
    volume_m3 FLOAT,
    base_city VARCHAR(120),
    base_region VARCHAR(120),
    routes TEXT,
    phone VARCHAR(32),
    contact_name VARCHAR(128),
    price_rub INTEGER,
    raw_text TEXT NOT NULL,
    avito_url VARCHAR(512),
    last_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_available_trucks_source_ext UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS ix_available_trucks_truck_type ON available_trucks(truck_type);
CREATE INDEX IF NOT EXISTS ix_available_trucks_capacity ON available_trucks(capacity_tons);
CREATE INDEX IF NOT EXISTS ix_available_trucks_base_city ON available_trucks(base_city);
CREATE INDEX IF NOT EXISTS ix_available_trucks_last_seen ON available_trucks(last_seen_at);
CREATE INDEX IF NOT EXISTS ix_available_trucks_active ON available_trucks(is_active);
