-- Referral program: invites and reward ledger

CREATE TABLE IF NOT EXISTS referral_invites (
    id SERIAL PRIMARY KEY,
    inviter_user_id BIGINT NOT NULL,
    invited_user_id BIGINT NOT NULL,
    source_payload VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    rewarded_at TIMESTAMP WITHOUT TIME ZONE,
    reward_days INTEGER NOT NULL DEFAULT 0,
    trigger_payment_id INTEGER,
    CONSTRAINT uq_referral_invited_user UNIQUE (invited_user_id)
);

CREATE INDEX IF NOT EXISTS ix_referral_inviter ON referral_invites(inviter_user_id);
CREATE INDEX IF NOT EXISTS ix_referral_invited ON referral_invites(invited_user_id);
CREATE INDEX IF NOT EXISTS ix_referral_rewarded_at ON referral_invites(rewarded_at);

CREATE TABLE IF NOT EXISTS referral_rewards (
    id SERIAL PRIMARY KEY,
    inviter_user_id BIGINT NOT NULL,
    invited_user_id BIGINT NOT NULL,
    payment_id INTEGER NOT NULL,
    reward_days INTEGER NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_referral_reward_payment UNIQUE (payment_id)
);

CREATE INDEX IF NOT EXISTS ix_referral_rewards_inviter ON referral_rewards(inviter_user_id);
CREATE INDEX IF NOT EXISTS ix_referral_rewards_invited ON referral_rewards(invited_user_id);
CREATE INDEX IF NOT EXISTS ix_referral_rewards_created ON referral_rewards(created_at);
