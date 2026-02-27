-- Escrow prototype: wallets + safe deal registry

ALTER TABLE cargos
    ADD COLUMN IF NOT EXISTS payment_status VARCHAR(32) NOT NULL DEFAULT 'unsecured';

ALTER TABLE cargos
    ADD COLUMN IF NOT EXISTS payment_verified_at TIMESTAMP WITHOUT TIME ZONE;

CREATE INDEX IF NOT EXISTS ix_cargos_payment_status ON cargos(payment_status);

CREATE TABLE IF NOT EXISTS user_wallets (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    balance_rub INTEGER NOT NULL DEFAULT 0,
    frozen_balance_rub INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS escrow_deals (
    id SERIAL PRIMARY KEY,
    cargo_id INTEGER NOT NULL REFERENCES cargos(id),
    client_id BIGINT NOT NULL REFERENCES users(id),
    carrier_id BIGINT REFERENCES users(id),
    amount_rub INTEGER NOT NULL,
    platform_fee_rub INTEGER NOT NULL DEFAULT 0,
    carrier_amount_rub INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'payment_pending',
    provider VARCHAR(32) NOT NULL DEFAULT 'mock_tochka',
    tochka_payment_id VARCHAR(120),
    payment_link VARCHAR(500),
    funded_at TIMESTAMP WITHOUT TIME ZONE,
    released_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_escrow_deals_cargo ON escrow_deals(cargo_id);
CREATE INDEX IF NOT EXISTS ix_escrow_deals_client ON escrow_deals(client_id, status);
CREATE INDEX IF NOT EXISTS ix_escrow_deals_carrier ON escrow_deals(carrier_id, status);

CREATE TABLE IF NOT EXISTS escrow_events (
    id SERIAL PRIMARY KEY,
    escrow_deal_id INTEGER NOT NULL REFERENCES escrow_deals(id),
    event_type VARCHAR(40) NOT NULL,
    actor_user_id BIGINT,
    payload_json TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_escrow_events_deal ON escrow_events(escrow_deal_id);
CREATE INDEX IF NOT EXISTS ix_escrow_events_type ON escrow_events(event_type);
