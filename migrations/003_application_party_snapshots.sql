-- application_party_snapshots: снапшоты реквизитов сторон по заявке
BEGIN;

CREATE TABLE IF NOT EXISTS application_party_snapshots (
    id              SERIAL PRIMARY KEY,
    application_id  BIGINT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL,
    payload_json    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS ix_application_party_snapshots_application_id
    ON application_party_snapshots(application_id);

COMMIT;
