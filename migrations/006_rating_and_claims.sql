-- Рейтинг в company_details
ALTER TABLE company_details ADD COLUMN IF NOT EXISTS rating_registration INTEGER DEFAULT 2;
ALTER TABLE company_details ADD COLUMN IF NOT EXISTS rating_subscription INTEGER DEFAULT 0;
ALTER TABLE company_details ADD COLUMN IF NOT EXISTS rating_experience INTEGER DEFAULT 0;
ALTER TABLE company_details ADD COLUMN IF NOT EXISTS rating_verified INTEGER DEFAULT 0;
ALTER TABLE company_details ADD COLUMN IF NOT EXISTS rating_deals_completed INTEGER DEFAULT 0;
ALTER TABLE company_details ADD COLUMN IF NOT EXISTS rating_no_claims INTEGER DEFAULT 1;
ALTER TABLE company_details ADD COLUMN IF NOT EXISTS rating_response_time INTEGER DEFAULT 0;
ALTER TABLE company_details ADD COLUMN IF NOT EXISTS rating_documents INTEGER DEFAULT 0;
ALTER TABLE company_details ADD COLUMN IF NOT EXISTS registered_at TIMESTAMP DEFAULT NOW();

-- Таблица претензий
CREATE TABLE IF NOT EXISTS claims (
    id SERIAL PRIMARY KEY,
    from_user_id BIGINT NOT NULL,
    from_company_id INTEGER REFERENCES company_details(id),
    to_user_id BIGINT NOT NULL,
    to_company_id INTEGER REFERENCES company_details(id),
    cargo_id INTEGER REFERENCES cargos(id),
    claim_type VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    amount INTEGER,
    status VARCHAR(20) DEFAULT 'open',
    response_text TEXT,
    response_at TIMESTAMP,
    resolution TEXT,
    resolved_by BIGINT,
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_claims_to_company ON claims(to_company_id);
CREATE INDEX IF NOT EXISTS ix_claims_from_user ON claims(from_user_id);
CREATE INDEX IF NOT EXISTS ix_claims_status ON claims(status);
