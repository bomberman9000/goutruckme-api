CREATE TABLE IF NOT EXISTS feed_complaints (
    id SERIAL PRIMARY KEY,
    feed_id INTEGER NOT NULL,
    user_id BIGINT NOT NULL,
    reason VARCHAR(32) DEFAULT 'scam',
    comment VARCHAR(500),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (feed_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_feed_complaints_feed ON feed_complaints (feed_id);

CREATE TABLE IF NOT EXISTS user_vehicles (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    body_type VARCHAR(64) NOT NULL,
    capacity_tons DOUBLE PRECISION DEFAULT 20.0,
    location_city VARCHAR(120),
    is_available BOOLEAN DEFAULT false,
    plate_number VARCHAR(20),
    sts_verified BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_user_vehicles_user ON user_vehicles (user_id);
CREATE INDEX IF NOT EXISTS ix_user_vehicles_available ON user_vehicles (is_available, location_city);
