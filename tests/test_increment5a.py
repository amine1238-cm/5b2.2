import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from resource_platform.config import Settings
from resource_platform.database import connect, migrate, backend, classify_database_error
from resource_platform.database_backend import (SQLiteBackend, PostgreSQLBackend, PostgreSQLDependencyError, PostgreSQLBackendError, DatabaseErrorCategory, ConnectionFailure, parse_database_target)

ROOT=Path(__file__).resolve().parents[1]

class Increment5ATests(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(self.path)
    def tearDown(self):
        for suffix in ("", "-journal", "-wal", "-shm"):
            try: Path(self.path+suffix).unlink()
            except FileNotFoundError: pass
    def test_sqlite_selection_and_connection_row_mapping(self):
        settings=Settings(Path(self.path),"secret",environment="test",migration_manifest_path=ROOT/"migrations.manifest.json")
        selected=backend(settings); self.assertIsInstance(selected,SQLiteBackend)
        db=selected.connect(); db.execute("CREATE TABLE x (id INTEGER PRIMARY KEY, value TEXT)"); db.execute("INSERT INTO x(value) VALUES (?)",("ok",)); row=db.execute("SELECT * FROM x").fetchone(); self.assertEqual(row["value"],"ok"); db.close()
    def test_transaction_commit_and_rollback(self):
        b=SQLiteBackend(self.path); db=b.connect(); db.execute("CREATE TABLE x (id INTEGER PRIMARY KEY, value TEXT)")
        with b.transaction(db): db.execute("INSERT INTO x(value) VALUES (?)",("committed",))
        with self.assertRaises(RuntimeError):
            with b.transaction(db):
                db.execute("INSERT INTO x(value) VALUES (?)",("rolled back",)); raise RuntimeError("test")
        self.assertEqual([r[0] for r in db.execute("SELECT value FROM x")], ["committed"]); db.close()
    def test_sqlite_error_classification(self):
        self.assertEqual(classify_database_error(sqlite3.IntegrityError()),DatabaseErrorCategory.INTEGRITY_FAILURE)
        self.assertEqual(classify_database_error(sqlite3.OperationalError("database is locked")),DatabaseErrorCategory.TRANSIENT_DATABASE_FAILURE)
        self.assertEqual(classify_database_error(sqlite3.OperationalError("unable to open database file")),DatabaseErrorCategory.CONNECTION_FAILURE)
        self.assertEqual(classify_database_error(ValueError()),DatabaseErrorCategory.VALIDATION_FAILURE)
    def test_database_target_parsing_without_connecting(self):
        target=parse_database_target(backend="sqlite",database_path=self.path); self.assertEqual(target.backend,"sqlite")
        target=parse_database_target(backend="postgresql",database_url="postgresql://user:password@example.test/db"); self.assertEqual(target.backend,"postgresql")
        with self.assertRaises(ValueError): parse_database_target(backend="postgresql",database_url="not-a-url")
    def test_postgresql_selection_is_deferred_until_connection_use(self):
        settings=Settings(Path(self.path),"secret",environment="development",database_backend="postgresql",database_url="postgresql://user:password@example.test/db",migration_manifest_path=ROOT/"migrations.manifest.json")
        selected=backend(settings)
        self.assertIsInstance(selected,PostgreSQLBackend)
        # Backend selection and URL parsing do not connect during import or selection.
        self.assertEqual(selected.name, "postgresql")
        # This SQLite-only job must not require a live PostgreSQL service.
        try:
            selected.connect()
        except PostgreSQLDependencyError:
            pass
        except PostgreSQLBackendError as exc:
            self.assertEqual(exc.category, ConnectionFailure)
    def test_sqlite_migrations_and_isolation(self):
        first=migrate(self.path); second=migrate(self.path); self.assertTrue(first); self.assertEqual(second,[])
        other=tempfile.mktemp(suffix=".sqlite3");
        try:
            migrate(other); a=connect(self.path); b=connect(other); self.assertEqual(a.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],b.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]); a.close(); b.close()
        finally:
            try: Path(other).unlink()
            except FileNotFoundError: pass

if __name__ == "__main__": unittest.main()
