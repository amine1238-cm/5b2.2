ALTER TABLE checkouts ADD COLUMN is_admin_override INTEGER NOT NULL DEFAULT 0 CHECK(is_admin_override IN (0,1));
ALTER TABLE checkouts ADD COLUMN override_reason TEXT;
CREATE INDEX IF NOT EXISTS idx_checkouts_override ON checkouts(is_admin_override,checked_out_at);
