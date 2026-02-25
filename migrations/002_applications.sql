-- applications: заявки A/B по сделке (cargos)
BEGIN;

CREATE TABLE IF NOT EXISTS applications (
    id                      BIGSERIAL PRIMARY KEY,
    deal_id                 INTEGER NOT NULL REFERENCES cargos(id) ON DELETE CASCADE,
    type                    VARCHAR(1) NOT NULL,
    status                  VARCHAR(20) NOT NULL DEFAULT 'draft',
    created_by_user_id      BIGINT NULL,
    selected_carrier_user_id BIGINT NULL,
    signed_by_client_at     TIMESTAMPTZ NULL,
    signed_by_forwarder_at   TIMESTAMPTZ NULL,
    signed_by_carrier_at    TIMESTAMPTZ NULL,
    rendered_text           TEXT NULL,
    rendered_updated_at     TIMESTAMPTZ NULL,
    pdf_path                VARCHAR(255) NULL,
    pdf_updated_at          TIMESTAMPTZ NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS ix_applications_deal_id ON applications(deal_id);
CREATE INDEX IF NOT EXISTS ix_applications_status ON applications(status);

COMMIT;
