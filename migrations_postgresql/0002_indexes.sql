CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_resources_status ON resources(status);
CREATE INDEX IF NOT EXISTS idx_resources_location ON resources(current_location_id);
CREATE INDEX IF NOT EXISTS idx_resources_manager ON resources(responsible_manager_id);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_resource_fields_type ON resource_type_fields(resource_type_id);
CREATE INDEX IF NOT EXISTS idx_resource_field_values_resource ON resource_field_values(resource_id);
CREATE INDEX IF NOT EXISTS idx_resource_field_values_field ON resource_field_values(resource_type_field_id);
