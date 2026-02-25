CREATE TABLE IF NOT EXISTS team_members (
    id SERIAL PRIMARY KEY,
    company_inn VARCHAR(12) NOT NULL,
    user_id BIGINT NOT NULL,
    role VARCHAR(20) DEFAULT 'carrier',
    name VARCHAR(120),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (company_inn, user_id)
);
CREATE INDEX IF NOT EXISTS ix_team_members_company ON team_members (company_inn);
CREATE INDEX IF NOT EXISTS ix_team_members_user ON team_members (user_id);
