-- audit_events: аудит действий (application|cargo, created|sent|signed|...)
BEGIN;

CREATE TABLE IF NOT EXISTS audit_events (
    id            BIGSERIAL PRIMARY KEY,
    entity_type   VARCHAR(20) NOT NULL,
    entity_id     BIGINT NOT NULL,
    action        VARCHAR(30) NOT NULL,
    actor_user_id BIGINT NULL,
    actor_role    VARCHAR(20) NULL,
    meta_json     TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_audit_created_at ON audit_events(created_at);

COMMIT;
