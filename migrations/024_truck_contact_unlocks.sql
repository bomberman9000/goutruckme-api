BEGIN;

CREATE TABLE IF NOT EXISTS truck_contact_unlocks (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    truck_id INTEGER NOT NULL REFERENCES available_trucks(id),
    amount_stars INTEGER NOT NULL DEFAULT 0,
    currency VARCHAR(10) NOT NULL DEFAULT 'XTR',
    status VARCHAR(20) NOT NULL DEFAULT 'success',
    invoice_payload VARCHAR(255),
    telegram_payment_charge_id VARCHAR(128),
    provider_payment_charge_id VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_truck_contact_unlock_user_truck UNIQUE (user_id, truck_id)
);

CREATE INDEX IF NOT EXISTS ix_truck_contact_unlocks_user ON truck_contact_unlocks(user_id);
CREATE INDEX IF NOT EXISTS ix_truck_contact_unlocks_truck ON truck_contact_unlocks(truck_id);
CREATE INDEX IF NOT EXISTS ix_truck_contact_unlocks_created ON truck_contact_unlocks(created_at);

COMMIT;
