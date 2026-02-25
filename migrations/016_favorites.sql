CREATE TABLE IF NOT EXISTS favorites (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    feed_id INTEGER NOT NULL,
    note VARCHAR(500),
    status VARCHAR(20) DEFAULT 'saved',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (user_id, feed_id)
);
CREATE INDEX IF NOT EXISTS ix_favorites_user ON favorites (user_id);
CREATE INDEX IF NOT EXISTS ix_favorites_feed ON favorites (feed_id);
