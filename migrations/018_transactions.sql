CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    feed_id INTEGER NOT NULL,
    carrier_user_id BIGINT NOT NULL,
    dispatcher_phone VARCHAR(32),
    dispatcher_inn VARCHAR(12),
    amount_rub INTEGER NOT NULL,
    payment_terms VARCHAR(120),
    payment_deadline TIMESTAMP,
    status VARCHAR(32) DEFAULT 'delivered',
    penalty_applied BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_transactions_carrier ON transactions (carrier_user_id);
CREATE INDEX IF NOT EXISTS ix_transactions_status ON transactions (status);
CREATE INDEX IF NOT EXISTS ix_transactions_deadline ON transactions (payment_deadline);
