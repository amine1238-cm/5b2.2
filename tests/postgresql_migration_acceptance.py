import json
import os
import tempfile
import unittest
from pathlib import Path

from resource_platform.postgresql_migrations import (
    POSTGRES_MANIFEST, POSTGRES_MIGRATIONS_DIR, migration_manifest,
    migrate, verify_manifest,
)


class PostgreSQLMigrationAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ.get("DATABASE_URL")
        if not cls.url:
            raise RuntimeError("DATABASE_URL is required; PostgreSQL tests must not be skipped")
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is required; PostgreSQL tests must not be skipped") from exc
        cls.psycopg = psycopg
        cls.schema = "ci_pg_" + next(tempfile._get_candidate_names()).replace("-", "_")
        cls.db = psycopg.connect(cls.url)
        with cls.db:
            with cls.db.cursor() as cur:
                cur.execute(f'CREATE SCHEMA "{cls.schema}"')
        cls.db.close()
        cls.first = migrate(cls.url, schema=cls.schema)

    @classmethod
    def tearDownClass(cls):
        db = cls.psycopg.connect(cls.url)
        with db:
            with db.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE')
        db.close()

    def connect_schema(self, schema):
        db = self.psycopg.connect(self.url)
        with db.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
        return db

    def connect(self):
        return self.connect_schema(self.schema)

    def assert_expected_failure_and_recover(self, db, operation, expected_exception):
        """Run an expected PostgreSQL failure inside a savepoint and recover."""
        savepoint = "sp_expected_failure"
        with db.cursor() as cur:
            cur.execute(f"SAVEPOINT {savepoint}")
            try:
                with self.assertRaises(expected_exception):
                    operation(cur)
            finally:
                cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            cur.execute("SELECT 1")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_fresh_and_repeated_migration(self):
        self.assertEqual(self.first, list(range(1, 8)))
        self.assertEqual(migrate(self.url, schema=self.schema), [])
        db = self.connect()
        with db.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            self.assertEqual([r[0] for r in cur.fetchall()], list(range(1, 8)))
            cur.execute("SELECT version, COUNT(*) FROM schema_migrations GROUP BY version HAVING COUNT(*) > 1")
            self.assertEqual(cur.fetchall(), [])
        db.close()

    def test_manifest_integrity(self):
        self.assertTrue(verify_manifest())
        self.assertEqual(set(migration_manifest()), set(json.loads(POSTGRES_MANIFEST.read_text())))

    def test_expected_tables_and_foreign_keys(self):
        db = self.connect()
        with db.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")
            tables = {r[0] for r in cur.fetchall()}
            expected = {"schema_migrations","users","roles","permissions","departments","locations","resource_types","resources","resource_type_fields","resource_field_values","audit_logs","booking_rules","bookings","qr_tokens","checkouts","checkins"}
            self.assertTrue(expected <= tables, expected - tables)
            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'btree_gist'")
            self.assertEqual(cur.fetchone()[0], "btree_gist")
            cur.execute("SELECT conname FROM pg_constraint WHERE conname = 'bookings_no_confirmed_overlap'")
            self.assertIsNotNone(cur.fetchone())
        db.close()

    def test_no_seed_data_and_foreign_keys(self):
        # Use a fresh schema for this assertion. Other acceptance tests create
        # fictional rows in the class-level schema; those rows must not be
        # mistaken for rows inserted by migrations.
        isolated_schema = "ci_pg_empty_" + next(tempfile._get_candidate_names()).replace("-", "_")
        admin = self.psycopg.connect(self.url)
        try:
            with admin:
                with admin.cursor() as cur:
                    cur.execute(f'CREATE SCHEMA "{isolated_schema}"')
            self.assertEqual(migrate(self.url, schema=isolated_schema), list(range(1, 8)))

            db = self.connect_schema(isolated_schema)
            try:
                with db:
                    with db.cursor() as cur:
                        # Migrations may create the required singleton booking-rules
                        # configuration row, but must not create business or seed data.
                        business_tables = (
                            "users", "roles", "permissions", "departments", "locations",
                            "resource_types", "resources", "resource_type_fields",
                            "resource_field_values", "audit_logs", "bookings",
                            "qr_tokens", "checkouts", "checkins",
                        )
                        for table in business_tables:
                            cur.execute(f"SELECT COUNT(*) FROM {table}")
                            count = cur.fetchone()[0]
                            self.assertEqual(count, 0, f"unexpected rows in {table}: {count}")
                        cur.execute("SELECT COUNT(*) FROM booking_rules")
                        self.assertEqual(cur.fetchone()[0], 1)
                        cur.execute("SELECT id FROM booking_rules")
                        self.assertEqual(cur.fetchone()[0], 1)

                        def invalid_resource(cur):
                            cur.execute(
                                "INSERT INTO resources(resource_type_id, name, description) "
                                "VALUES (999999, 'x', '')"
                            )

                        self.assert_expected_failure_and_recover(
                            db, invalid_resource, self.psycopg.errors.ForeignKeyViolation
                        )
            finally:
                db.close()
        finally:
            with self.psycopg.connect(self.url) as cleanup:
                with cleanup.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{isolated_schema}" CASCADE')
            admin.close()

    def test_booking_overlap_adjacent_cancelled_completed(self):
        db = self.connect()
        with db:
            with db.cursor() as cur:
                cur.execute("INSERT INTO resource_types(name, code, description) VALUES ('T','T','') RETURNING id")
                rt = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO resources(resource_type_id,name,description) "
                    "VALUES (%s,'R','') RETURNING id", (rt,)
                )
                resource = cur.fetchone()[0]
                cur.execute("INSERT INTO users(email,display_name) VALUES ('a@x.test','A') RETURNING id")
                user = cur.fetchone()[0]

                # The baseline confirmed interval is exactly 10:00-11:00 UTC.
                cur.execute(
                    """INSERT INTO bookings(
                        resource_id, owner_user_id, start_at_utc, end_at_utc,
                        purpose, created_by
                    ) VALUES (%s,%s,'2026-01-01 10:00+00','2026-01-01 11:00+00','x',%s)
                    RETURNING id""",
                    (resource, user, user),
                )
                confirmed = cur.fetchone()[0]

                def overlapping_confirmed(cur):
                    cur.execute(
                        """INSERT INTO bookings(
                            resource_id, owner_user_id, start_at_utc, end_at_utc,
                            purpose, created_by
                        ) VALUES (%s,%s,'2026-01-01 10:30+00','2026-01-01 11:30+00','x',%s)""",
                        (resource, user, user),
                    )

                self.assert_expected_failure_and_recover(
                    db, overlapping_confirmed, self.psycopg.errors.ExclusionViolation
                )

                # Adjacent [) intervals are allowed.
                cur.execute(
                    """INSERT INTO bookings(
                        resource_id, owner_user_id, start_at_utc, end_at_utc,
                        purpose, created_by
                    ) VALUES (%s,%s,'2026-01-01 11:00+00','2026-01-01 12:00+00','x',%s)""",
                    (resource, user, user),
                )

                # Cancelled and completed overlapping bookings do not block availability.
                cur.execute(
                    """INSERT INTO bookings(
                        resource_id, owner_user_id, start_at_utc, end_at_utc,
                        purpose, created_by, status, cancelled_at
                    ) VALUES (%s,%s,'2026-01-01 10:30+00','2026-01-01 11:30+00','x',%s,'cancelled',CURRENT_TIMESTAMP)""",
                    (resource, user, user),
                )
                cur.execute(
                    """INSERT INTO bookings(
                        resource_id, owner_user_id, start_at_utc, end_at_utc,
                        purpose, created_by, status
                    ) VALUES (%s,%s,'2026-01-01 10:30+00','2026-01-01 11:30+00','x',%s,'completed')""",
                    (resource, user, user),
                )

                cur.execute(
                    "SELECT status FROM bookings WHERE id=%s", (confirmed,)
                )
                self.assertEqual(cur.fetchone()[0], "confirmed")
        db.close()

    def test_lifecycle_constraints(self):
        db = self.connect()
        with db:
            with db.cursor() as cur:
                cur.execute("INSERT INTO resource_types(name,code,description) VALUES ('L','L','') RETURNING id")
                rt = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO resources(resource_type_id,name,description) "
                    "VALUES (%s,'R','') RETURNING id", (rt,)
                )
                resource = cur.fetchone()[0]
                cur.execute("INSERT INTO users(email,display_name) VALUES ('l@x.test','L') RETURNING id")
                user = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO checkouts(
                        resource_id,user_id,condition_before,is_admin_override,override_reason
                    ) VALUES (%s,%s,'good',TRUE,'reason') RETURNING id""",
                    (resource, user),
                )
                checkout = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO checkins(checkout_id,resource_id,user_id,condition_after) "
                    "VALUES (%s,%s,%s,'good')", (checkout, resource, user)
                )

                def duplicate_checkin(cur):
                    cur.execute(
                        "INSERT INTO checkins(checkout_id,resource_id,user_id,condition_after) "
                        "VALUES (%s,%s,%s,'good')", (checkout, resource, user)
                    )

                self.assert_expected_failure_and_recover(
                    db, duplicate_checkin, self.psycopg.errors.UniqueViolation
                )

                cur.execute(
                    "INSERT INTO resources(resource_type_id,name,description) "
                    "VALUES (%s,'R2','') RETURNING id", (rt,)
                )
                r2 = cur.fetchone()[0]
                # Use a valid ordinary checkout fixture: non-admin checkouts
                # must reference a confirmed booking.
                cur.execute(
                    """INSERT INTO bookings(
                        resource_id, owner_user_id, start_at_utc, end_at_utc,
                        purpose, created_by
                    ) VALUES (%s,%s,'2026-01-02 10:00+00','2026-01-02 11:00+00','lifecycle',%s)
                    RETURNING id""",
                    (r2, user, user),
                )
                r2_booking = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO checkouts(
                        resource_id,booking_id,user_id,condition_before,
                        is_admin_override,override_reason
                    ) VALUES (%s,%s,%s,'good',FALSE,NULL)""",
                    (r2, r2_booking, user),
                )

                def duplicate_checkout(cur):
                    cur.execute(
                        """INSERT INTO checkouts(
                            resource_id, booking_id, user_id, condition_before,
                            is_admin_override, override_reason
                        ) VALUES (%s,%s,%s,'good',FALSE,NULL)""",
                        (r2, r2_booking, user),
                    )

                self.assert_expected_failure_and_recover(
                    db, duplicate_checkout, self.psycopg.errors.UniqueViolation
                )

                cur.execute(
                    "SELECT COUNT(*) FROM checkouts WHERE resource_id=%s", (r2,)
                )
                self.assertEqual(cur.fetchone()[0], 1)
        db.close()

if __name__ == '__main__':
    unittest.main()
