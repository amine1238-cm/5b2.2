PRAGMA foreign_keys=OFF;
CREATE TABLE checkouts_new(
 id INTEGER PRIMARY KEY,
 resource_id INTEGER NOT NULL REFERENCES resources(id),
 booking_id INTEGER REFERENCES bookings(id),
 user_id INTEGER NOT NULL REFERENCES users(id),
 checked_out_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 condition_before TEXT NOT NULL CHECK(condition_before IN ('good','minor_issue','damaged','missing_accessory')),
 notes TEXT,
 checked_in_at TEXT,
 UNIQUE(booking_id)
);
INSERT INTO checkouts_new SELECT id,resource_id,booking_id,user_id,checked_out_at,condition_before,notes,checked_in_at FROM checkouts;
DROP TABLE checkouts;
ALTER TABLE checkouts_new RENAME TO checkouts;
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_checkout_resource ON checkouts(resource_id) WHERE checked_in_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_checkouts_resource ON checkouts(resource_id,checked_out_at);
PRAGMA foreign_keys=ON;
