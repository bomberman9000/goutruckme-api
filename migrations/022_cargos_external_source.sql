ALTER TABLE cargos
ADD COLUMN IF NOT EXISTS external_url VARCHAR(500);

ALTER TABLE cargos
ADD COLUMN IF NOT EXISTS source_platform VARCHAR(64) NOT NULL DEFAULT 'manual';

UPDATE cargos
SET source_platform = 'manual'
WHERE source_platform IS NULL;

CREATE INDEX IF NOT EXISTS ix_cargos_external_url ON cargos (external_url);
CREATE INDEX IF NOT EXISTS ix_cargos_source_platform ON cargos (source_platform);
