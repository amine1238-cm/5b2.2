CREATE TABLE IF NOT EXISTS booking_rules(
 id INTEGER PRIMARY KEY CHECK(id=1),
 minimum_duration_minutes INTEGER NOT NULL DEFAULT 15 CHECK(minimum_duration_minutes > 0),
 maximum_duration_minutes INTEGER NOT NULL DEFAULT 1440 CHECK(maximum_duration_minutes >= minimum_duration_minutes),
 advance_booking_days INTEGER NOT NULL DEFAULT 90 CHECK(advance_booking_days >= 0),
 cancellation_deadline_minutes INTEGER NOT NULL DEFAULT 0 CHECK(cancellation_deadline_minutes >= 0),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_by INTEGER REFERENCES users(id)
);
INSERT OR IGNORE INTO booking_rules(id) VALUES (1);
CREATE TABLE IF NOT EXISTS bookings(
 id INTEGER PRIMARY KEY, resource_id INTEGER NOT NULL REFERENCES resources(id), owner_user_id INTEGER NOT NULL REFERENCES users(id),
 start_at_utc TEXT NOT NULL, end_at_utc TEXT NOT NULL, purpose TEXT NOT NULL, project_reference TEXT, destination TEXT,
 status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN ('confirmed','cancelled','completed')),
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 cancelled_at TEXT, cancellation_reason TEXT, CHECK(start_at_utc < end_at_utc),
 CHECK((status='cancelled' AND cancelled_at IS NOT NULL) OR (status!='cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_bookings_resource_time ON bookings(resource_id,start_at_utc,end_at_utc,status);
CREATE INDEX IF NOT EXISTS idx_bookings_owner ON bookings(owner_user_id,status,start_at_utc);
CREATE TRIGGER IF NOT EXISTS prevent_confirmed_booking_overlap
BEFORE INSERT ON bookings WHEN NEW.status='confirmed'
BEGIN
 SELECT CASE WHEN EXISTS (SELECT 1 FROM bookings b WHERE b.resource_id=NEW.resource_id AND b.status='confirmed' AND NEW.start_at_utc < b.end_at_utc AND NEW.end_at_utc > b.start_at_utc) THEN RAISE(ABORT,'BOOKING_CONFLICT') END;
END;
CREATE TRIGGER IF NOT EXISTS prevent_confirmed_booking_overlap_update
BEFORE UPDATE OF resource_id,start_at_utc,end_at_utc,status ON bookings WHEN NEW.status='confirmed'
BEGIN
 SELECT CASE WHEN EXISTS (SELECT 1 FROM bookings b WHERE b.id<>NEW.id AND b.resource_id=NEW.resource_id AND b.status='confirmed' AND NEW.start_at_utc < b.end_at_utc AND NEW.end_at_utc > b.start_at_utc) THEN RAISE(ABORT,'BOOKING_CONFLICT') END;
END;
