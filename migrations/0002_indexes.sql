CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type,entity_id,occurred_at);
CREATE INDEX IF NOT EXISTS idx_resources_status ON resources(status);
CREATE INDEX IF NOT EXISTS idx_resources_location ON resources(current_location_id);
CREATE INDEX IF NOT EXISTS idx_resources_manager ON resources(responsible_manager_id);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
