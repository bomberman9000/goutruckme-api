-- Premium access + click tracking

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS premium_until TIMESTAMP WITHOUT TIME ZONE;

CREATE TABLE IF NOT EXISTS call_logs (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    cargo_id INTEGER NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_call_logs_user ON call_logs(user_id);
CREATE INDEX IF NOT EXISTS ix_call_logs_cargo ON call_logs(cargo_id);
CREATE INDEX IF NOT EXISTS ix_call_logs_created ON call_logs(created_at);

CREATE TABLE IF NOT EXISTS premium_payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    plan_days INTEGER NOT NULL,
    amount_stars INTEGER NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'XTR',
    status VARCHAR(20) NOT NULL DEFAULT 'success',
    invoice_payload VARCHAR(255),
    telegram_payment_charge_id VARCHAR(128),
    provider_payment_charge_id VARCHAR(128),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_premium_payments_user ON premium_payments(user_id);
CREATE INDEX IF NOT EXISTS ix_premium_payments_created ON premium_payments(created_at);
