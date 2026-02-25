-- Antifraud v2 базовые таблицы

CREATE TABLE IF NOT EXISTS moderation_reviews (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(30) NOT NULL,
    entity_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'done',
    risk_level VARCHAR(20),
    flags_json TEXT,
    comment TEXT,
    recommended_action TEXT,
    model_used VARCHAR(120),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_moderation_entity UNIQUE (entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS ix_moderation_entity ON moderation_reviews(entity_type, entity_id);


CREATE TABLE IF NOT EXISTS route_rate_profiles (
    id SERIAL PRIMARY KEY,
    from_city_norm VARCHAR(120) NOT NULL,
    to_city_norm VARCHAR(120) NOT NULL,
    min_rate_per_km INTEGER NOT NULL,
    max_rate_per_km INTEGER NOT NULL,
    median_rate_per_km INTEGER,
    samples_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_route_rate_profile UNIQUE (from_city_norm, to_city_norm)
);

CREATE INDEX IF NOT EXISTS ix_route_rate_pair ON route_rate_profiles(from_city_norm, to_city_norm);


CREATE TABLE IF NOT EXISTS counterparty_lists (
    id SERIAL PRIMARY KEY,
    list_type VARCHAR(10) NOT NULL,
    inn VARCHAR(12),
    phone VARCHAR(20),
    name VARCHAR(255),
    note TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_counterparty_list_type ON counterparty_lists(list_type);
CREATE INDEX IF NOT EXISTS ix_counterparty_inn ON counterparty_lists(inn);
CREATE INDEX IF NOT EXISTS ix_counterparty_phone ON counterparty_lists(phone);
CREATE INDEX IF NOT EXISTS ix_counterparty_name ON counterparty_lists(name);


CREATE TABLE IF NOT EXISTS deal_doc_requests (
    id SERIAL PRIMARY KEY,
    deal_id INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'requested',
    required_docs_json TEXT,
    reason_codes_json TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_deal_doc_request UNIQUE (deal_id)
);

CREATE INDEX IF NOT EXISTS ix_deal_doc_request_deal ON deal_doc_requests(deal_id);
