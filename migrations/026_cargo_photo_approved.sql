-- Migration 026: photo moderation
ALTER TABLE cargos ADD COLUMN IF NOT EXISTS photo_approved BOOLEAN NOT NULL DEFAULT FALSE;
