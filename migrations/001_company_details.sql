-- company_details: реквизиты юрлица, банк, контакт, водитель по user_id
BEGIN;

CREATE TABLE IF NOT EXISTS company_details (
    id                SERIAL PRIMARY KEY,
    user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_name      VARCHAR(255) NULL,
    inn               VARCHAR(12) NULL,
    kpp               VARCHAR(9) NULL,
    ogrn              VARCHAR(15) NULL,
    legal_address     VARCHAR(500) NULL,
    bank_name         VARCHAR(255) NULL,
    bank_bik           VARCHAR(9) NULL,
    bank_account      VARCHAR(20) NULL,
    bank_corr_account  VARCHAR(20) NULL,
    contact_person    VARCHAR(255) NULL,
    contact_phone     VARCHAR(20) NULL,
    contact_email     VARCHAR(100) NULL,
    driver_name       VARCHAR(255) NULL,
    driver_passport   VARCHAR(100) NULL,
    driver_phone      VARCHAR(20) NULL,
    vehicle_info      VARCHAR(255) NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    CONSTRAINT uq_company_details_user_id UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS ix_company_details_user_id ON company_details(user_id);

COMMIT;
