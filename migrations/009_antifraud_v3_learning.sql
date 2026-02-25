-- Antifraud v3 learning/statistics tables

CREATE TABLE IF NOT EXISTS closed_deal_stats (
    id SERIAL PRIMARY KEY,
    from_city_norm VARCHAR(120) NOT NULL,
    to_city_norm VARCHAR(120) NOT NULL,
    distance_km DOUBLE PRECISION NOT NULL,
    rate_per_km DOUBLE PRECISION NOT NULL,
    total_rub DOUBLE PRECISION,
    closed_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_closed_deal_route ON closed_deal_stats(from_city_norm, to_city_norm);


CREATE TABLE IF NOT EXISTS counterparty_risk_history (
    id SERIAL PRIMARY KEY,
    counterparty_inn VARCHAR(12) NOT NULL,
    deal_id INTEGER NOT NULL,
    risk_level VARCHAR(20) NOT NULL,
    score_total INTEGER NOT NULL DEFAULT 0,
    reason_codes_json TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_counterparty_risk_inn ON counterparty_risk_history(counterparty_inn);


CREATE TABLE IF NOT EXISTS route_rate_stats (
    id SERIAL PRIMARY KEY,
    from_city_norm VARCHAR(120) NOT NULL,
    to_city_norm VARCHAR(120) NOT NULL,
    mean_rate DOUBLE PRECISION,
    median_rate DOUBLE PRECISION,
    std_dev DOUBLE PRECISION,
    p25 DOUBLE PRECISION,
    p75 DOUBLE PRECISION,
    sample_size INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_route_rate_stats UNIQUE (from_city_norm, to_city_norm)
);

CREATE INDEX IF NOT EXISTS ix_route_rate_stats_pair ON route_rate_stats(from_city_norm, to_city_norm);
