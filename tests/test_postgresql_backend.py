import unittest
from pathlib import Path
from unittest.mock import patch
from resource_platform.config import Settings
from resource_platform.database_backend import (
    PostgreSQLBackend, PostgreSQLDependencyError, BackendNotImplementedError,
    BusinessConflict, IntegrityFailure, TransientDatabaseFailure, ConnectionFailure,
    classify_database_error, parse_database_target,
)

class FakePGError(Exception):
    def __init__(self, state): self.sqlstate = state

class PostgreSQLBackendTests(unittest.TestCase):
    def test_url_and_pool_configuration(self):
        b=PostgreSQLBackend("postgresql://user:pass@example.test/db", min_size=2, max_size=4, connection_timeout=2, acquisition_timeout=3)
        self.assertEqual(b.name, "postgresql"); self.assertEqual((b.min_size,b.max_size),(2,4))
    def test_invalid_url_and_pool_values(self):
        with self.assertRaises(ValueError): PostgreSQLBackend("not-a-url")
        with self.assertRaises(ValueError): PostgreSQLBackend("postgresql://u:p@h/db", min_size=3, max_size=2)
    def test_sqlstate_classification(self):
        self.assertEqual(classify_database_error(FakePGError("23505")), BusinessConflict)
        self.assertEqual(classify_database_error(FakePGError("23503")), IntegrityFailure)
        self.assertEqual(classify_database_error(FakePGError("23P01")), BusinessConflict)
        self.assertEqual(classify_database_error(FakePGError("40001")), TransientDatabaseFailure)
        self.assertEqual(classify_database_error(FakePGError("40P01")), TransientDatabaseFailure)
        self.assertEqual(classify_database_error(FakePGError("55P03")), TransientDatabaseFailure)
        self.assertEqual(classify_database_error(FakePGError("08001")), ConnectionFailure)
        self.assertEqual(classify_database_error(FakePGError("08006")), ConnectionFailure)
    def test_no_connection_at_import_or_url_parse(self):
        target=parse_database_target(backend="postgresql", database_url="postgresql://u:p@example.test/db")
        self.assertEqual(target.backend,"postgresql")
    def test_dependency_failure_is_clear(self):
        b=PostgreSQLBackend("postgresql://u:p@example.test/db")
        with patch.object(b, "_imports", side_effect=PostgreSQLDependencyError("unavailable")):
            with self.assertRaises(PostgreSQLDependencyError): b.connect()
    def test_settings_pool_values(self):
        s=Settings(Path("/tmp/test-db.sqlite3"),"secret", environment="test", db_pool_min_size=2, db_pool_max_size=5)
        self.assertIs(s.validate(),s)

if __name__ == "__main__": unittest.main()
