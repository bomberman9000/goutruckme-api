ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS driver_license_file_id TEXT,
    ADD COLUMN IF NOT EXISTS sts_file_id TEXT,
    ADD COLUMN IF NOT EXISTS driver_verified_at TIMESTAMP;
