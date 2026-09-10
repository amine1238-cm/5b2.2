CREATE TABLE IF NOT EXISTS qr_tokens(
 id INTEGER PRIMARY KEY,
 resource_id INTEGER NOT NULL REFERENCES resources(id),
 token_hash TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 created_by INTEGER REFERENCES users(id),
 invalidated_at TEXT,
 invalidated_by INTEGER REFERENCES users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_qr_active_resource ON qr_tokens(resource_id) WHERE invalidated_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_qr_token_hash ON qr_tokens(token_hash);
CREATE TABLE IF NOT EXISTS checkouts(
 id INTEGER PRIMARY KEY,
 resource_id INTEGER NOT NULL REFERENCES resources(id),
 booking_id INTEGER NOT NULL REFERENCES bookings(id),
 user_id INTEGER NOT NULL REFERENCES users(id),
 checked_out_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 condition_before TEXT NOT NULL CHECK(condition_before IN ('good','minor_issue','damaged','missing_accessory')),
 notes TEXT,
 checked_in_at TEXT,
 UNIQUE(booking_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_checkout_resource ON checkouts(resource_id) WHERE checked_in_at IS NULL;
CREATE TABLE IF NOT EXISTS checkins(
 id INTEGER PRIMARY KEY,
 checkout_id INTEGER NOT NULL UNIQUE REFERENCES checkouts(id),
 resource_id INTEGER NOT NULL REFERENCES resources(id),
 booking_id INTEGER NOT NULL REFERENCES bookings(id),
 user_id INTEGER NOT NULL REFERENCES users(id),
 checked_in_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 condition_after TEXT NOT NULL CHECK(condition_after IN ('good','minor_issue','damaged','missing_accessory')),
 notes TEXT,
 missing_accessories TEXT,
 problems TEXT,
 damage_reported INTEGER NOT NULL DEFAULT 0 CHECK(damage_reported IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_checkouts_resource ON checkouts(resource_id,checked_out_at);
CREATE INDEX IF NOT EXISTS idx_checkins_booking ON checkins(booking_id,checked_in_at);
