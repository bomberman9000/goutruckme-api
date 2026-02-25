-- cargos: колонки client_user_id, forwarder_user_id
BEGIN;

ALTER TABLE cargos
  ADD COLUMN IF NOT EXISTS client_user_id BIGINT NULL;

ALTER TABLE cargos
  ADD COLUMN IF NOT EXISTS forwarder_user_id BIGINT NULL;

CREATE INDEX IF NOT EXISTS ix_cargos_client_user_id ON cargos(client_user_id);
CREATE INDEX IF NOT EXISTS ix_cargos_forwarder_user_id ON cargos(forwarder_user_id);

COMMIT;
