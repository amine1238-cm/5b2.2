ALTER TABLE qr_tokens ADD COLUMN encrypted_token TEXT;
CREATE INDEX IF NOT EXISTS idx_qr_active_resource_token ON qr_tokens(resource_id, invalidated_at);
