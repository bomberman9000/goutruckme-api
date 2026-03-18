-- Migration 027: cargo pickup timestamp
ALTER TABLE cargos ADD COLUMN IF NOT EXISTS pickup_confirmed_at TIMESTAMP;
