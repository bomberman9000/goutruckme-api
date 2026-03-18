-- Migration 025: add photo_file_id to cargos
ALTER TABLE cargos ADD COLUMN IF NOT EXISTS photo_file_id TEXT;
