-- Add notified_at column to cargos for push-notification tracking
ALTER TABLE cargos ADD COLUMN IF NOT EXISTS notified_at TIMESTAMP;
