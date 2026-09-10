ALTER TABLE checkouts DROP CONSTRAINT IF EXISTS checkouts_override_consistency;
ALTER TABLE checkouts ADD CONSTRAINT checkouts_override_consistency CHECK (
    (booking_id IS NOT NULL AND is_admin_override IS NOT TRUE AND override_reason IS NULL)
    OR (booking_id IS NULL AND is_admin_override IS TRUE AND override_reason IS NOT NULL AND length(btrim(override_reason)) > 0)
);
CREATE INDEX IF NOT EXISTS idx_checkouts_override ON checkouts(is_admin_override, checked_out_at);
