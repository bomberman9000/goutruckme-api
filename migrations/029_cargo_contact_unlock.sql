-- Migration 029: cargo phone field + cargo_contact_unlocks table

ALTER TABLE cargos
    ADD COLUMN IF NOT EXISTS phone VARCHAR(32);

CREATE TABLE IF NOT EXISTS cargo_contact_unlocks (
    id                         SERIAL PRIMARY KEY,
    user_id                    BIGINT NOT NULL,
    cargo_id                   INTEGER NOT NULL REFERENCES cargos(id),
    amount_stars               INTEGER NOT NULL DEFAULT 0,
    currency                   VARCHAR(10) NOT NULL DEFAULT 'XTR',
    status                     VARCHAR(20) NOT NULL DEFAULT 'success',
    invoice_payload            VARCHAR(255),
    telegram_payment_charge_id VARCHAR(128),
    provider_payment_charge_id VARCHAR(128),
    created_at                 TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_cargo_contact_unlock_user_cargo UNIQUE (user_id, cargo_id)
);

CREATE INDEX IF NOT EXISTS ix_cargo_contact_unlocks_user    ON cargo_contact_unlocks (user_id);
CREATE INDEX IF NOT EXISTS ix_cargo_contact_unlocks_cargo   ON cargo_contact_unlocks (cargo_id);
CREATE INDEX IF NOT EXISTS ix_cargo_contact_unlocks_created ON cargo_contact_unlocks (created_at);
