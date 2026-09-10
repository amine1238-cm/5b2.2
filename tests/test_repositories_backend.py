"""Backend-parameterized repository tests for the 5B2.2 repository substep.

The same repository assertions run against either an isolated SQLite database or
an isolated PostgreSQL schema selected by DATABASE_BACKEND. These tests do not
exercise application routes.
"""
from __future__ import annotations

import os
import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from resource_platform.database import migrate as migrate_sqlite
from resource_platform.database_backend import SQLiteBackend, PostgreSQLBackend
from resource_platform.postgresql_migrations import migrate as migrate_postgresql
from resource_platform.repositories import (
    AuditRepository,
    BookingRepository,
    LifecycleRepository,
    QrTokenRepository,
    ResourceRepository,
    UserRepository,
)
from resource_platform.transaction_service import TransactionService


class BackendFixture(unittest.TestCase):
    """Select exactly one real backend and prepare an isolated schema."""

    backend_name = os.environ.get("DATABASE_BACKEND", "sqlite").lower()

    @classmethod
    def setUpClass(cls):
        if cls.backend_name == "postgresql":
            url = os.environ.get("DATABASE_URL")
            if not url:
                raise RuntimeError("DATABASE_URL is required for PostgreSQL repository tests")
            cls.backend = PostgreSQLBackend(
                url,
                min_size=1,
                max_size=4,
                connection_timeout=5,
                acquisition_timeout=5,
                health_check_timeout=3,
            )
            cls.backend.open()
            if cls.backend.name != "postgresql":
                raise AssertionError(f"wrong repository backend: {cls.backend.name}")
            with cls.backend.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_schema() AS schema_name, current_database() AS database_name")
                    row = cur.fetchone()
                    cls.schema_name = row["schema_name"]
                    cls.database_name = row["database_name"]
            cls._prepare_postgresql_data()
        elif cls.backend_name == "sqlite":
            fd, path = tempfile.mkstemp(prefix="repo-test-", suffix=".sqlite3")
            os.close(fd)
            os.unlink(path)
            cls.sqlite_path = Path(path)
            migrate_sqlite(cls.sqlite_path)
            cls.backend = SQLiteBackend(cls.sqlite_path)
            if cls.backend.name != "sqlite":
                raise AssertionError(f"wrong repository backend: {cls.backend.name}")
            cls._prepare_sqlite_data()
        else:
            raise RuntimeError(f"unsupported repository test backend: {cls.backend_name}")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "backend_name", "") == "postgresql":
            try:
                with cls.backend.transaction() as conn:
                    conn.execute("TRUNCATE TABLE checkins, checkouts, qr_tokens, bookings, audit_logs, resource_field_values, resources, resource_type_fields, resource_types, user_roles, users, roles, permissions, locations, departments RESTART IDENTITY CASCADE")
            finally:
                cls.backend.close()
        elif hasattr(cls, "sqlite_path"):
            try:
                cls.sqlite_path.unlink()
            except FileNotFoundError:
                pass

    @classmethod
    def _insert_id(cls, conn, sql, params):
        cur = cls.backend.execute(conn, sql, params)
        if cls.backend_name == "postgresql":
            with conn.cursor() as c:
                c.execute("SELECT lastval() AS last_id")
                row = c.fetchone()
                return row["last_id"]
        return cur.lastrowid

    @classmethod
    def _prepare_sqlite_data(cls):
        with cls.backend.transaction() as conn:
            cls._insert_id(conn, "INSERT INTO roles(name, description) VALUES (?, ?)", ("employee", "Employee"))
            cls._insert_id(conn, "INSERT INTO users(email, display_name, status, password_hash) VALUES (?, ?, ?, ?)", ("repo@example.test", "Repo User", "active", "hash"))
            cls._insert_id(conn, "INSERT INTO resource_types(name, code, description) VALUES (?, ?, ?)", ("Equipment", "equipment", "Equipment"))
            cls._insert_id(conn, "INSERT INTO resources(resource_type_id, name, description, created_by, updated_by) VALUES (?, ?, ?, ?, ?)", (1, "Test Resource", "Desc", 1, 1))

    @classmethod
    def _prepare_postgresql_data(cls):
        with cls.backend.transaction() as conn:
            cls._insert_id(conn, "INSERT INTO roles(name, description) VALUES (%s, %s)", ("employee", "Employee"))
            cls._insert_id(conn, "INSERT INTO users(email, display_name, status, password_hash) VALUES (%s, %s, %s, %s)", ("repo@example.test", "Repo User", "active", "hash"))
            cls._insert_id(conn, "INSERT INTO resource_types(name, code, description) VALUES (%s, %s, %s)", ("Equipment", "equipment", "Equipment"))
            cls._insert_id(conn, "INSERT INTO resources(resource_type_id, name, description, created_by, updated_by) VALUES (%s, %s, %s, %s, %s)", (1, "Test Resource", "Desc", 1, 1))

    def test_selected_backend_is_explicit(self):
        self.assertEqual(self.backend.name, self.backend_name)

    def test_all_repositories_against_selected_backend(self):
        with TransactionService(self.backend).transaction() as conn:
            users = UserRepository(self.backend, conn)
            resources = ResourceRepository(self.backend, conn)
            bookings = BookingRepository(self.backend, conn)
            qr = QrTokenRepository(self.backend, conn)
            lifecycle = LifecycleRepository(self.backend, conn)
            audit = AuditRepository(self.backend, conn)

            user = users.by_email("repo@example.test")
            self.assertEqual(user["status"], "active")
            resource = resources.by_id(1)
            self.assertEqual(resource["name"], "Test Resource")
            resources.update_name(1, "Renamed", 1)

            start = datetime(2099, 1, 1, 10, tzinfo=timezone.utc)
            end = datetime(2099, 1, 1, 11, tzinfo=timezone.utc)
            bookings.create(1, 1, start, end, "purpose", 1)
            booking = bookings.for_resource(1)[0]
            self.assertEqual(booking["purpose"], "purpose")

            qr.create(1, "hash-value", 1)
            self.assertEqual(qr.by_hash("hash-value")["resource_id"], 1)

            lifecycle.checkout(1, booking["id"], 1, "good", "notes")
            self.assertEqual(lifecycle.active_checkout(1)["user_id"], 1)
            lifecycle.checkin(1, 1, booking["id"], 1, "good", "ok")
            self.assertIsNotNone(lifecycle.checkin_for_checkout(1))
            audit.record(1, "test", "resource", 1, "repository")

        with TransactionService(self.backend).transaction() as conn:
            self.assertEqual(ResourceRepository(self.backend, conn).by_id(1)["name"], "Renamed")
            self.assertEqual(conn.execute("SELECT COUNT(*) AS row_count FROM audit_logs").fetchone()["row_count"], 1)

    def test_transaction_rollback_and_recovery(self):
        with self.assertRaises(RuntimeError):
            with TransactionService(self.backend).transaction() as conn:
                UserRepository(self.backend, conn).create("rollback@example.test", "Rollback")
                raise RuntimeError("expected rollback")
        with TransactionService(self.backend).transaction() as conn:
            self.assertIsNone(UserRepository(self.backend, conn).by_email("rollback@example.test"))
            self.assertEqual(conn.execute("SELECT 1 AS ok").fetchone()["ok"], 1)

    def test_audit_write_is_transactional(self):
        with self.assertRaises(RuntimeError):
            with TransactionService(self.backend).transaction() as conn:
                AuditRepository(self.backend, conn).record(1, "rollback", "resource", 1, "repository")
                raise RuntimeError("force rollback")
        with TransactionService(self.backend).transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS row_count FROM audit_logs WHERE action = 'rollback'").fetchone()["row_count"], 0)


if __name__ == "__main__":
    unittest.main()
