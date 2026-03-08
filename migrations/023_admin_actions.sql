-- Admin action audit log
CREATE TABLE IF NOT EXISTS admin_actions (
    id             SERIAL PRIMARY KEY,
    actor_tg_id    BIGINT       NOT NULL,
    action         VARCHAR(64)  NOT NULL,
    target_type    VARCHAR(64),
    target_id      VARCHAR(256),
    result         VARCHAR(16)  NOT NULL DEFAULT 'ok',
    metadata_json  TEXT,
    timestamp      TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_admin_actions_actor   ON admin_actions (actor_tg_id);
CREATE INDEX IF NOT EXISTS ix_admin_actions_action  ON admin_actions (action);
CREATE INDEX IF NOT EXISTS ix_admin_actions_ts      ON admin_actions (timestamp);
