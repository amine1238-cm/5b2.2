import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from resource_platform.database import migrate
from resource_platform.database_backend import (
    SQLiteBackend, render_named_query, DatabaseErrorCategory,
)
from resource_platform.repositories import (
    UserRepository, ResourceRepository, BookingRepository,
    QrTokenRepository, LifecycleRepository, AuditRepository,
)
from resource_platform.transaction_service import TransactionService


class RendererTests(unittest.TestCase):
    def check(self, sql, params, style, expected_sql, expected_values):
        self.assertEqual(render_named_query(sql, params, style=style), (expected_sql, expected_values))

    def test_named_and_ordered_parameters(self):
        self.check('SELECT * FROM t WHERE a=:a AND b=:b', {'a': 1, 'b': 2}, 'sqlite', 'SELECT * FROM t WHERE a=? AND b=?', (1, 2))
        self.check('SELECT * FROM t WHERE a=:a AND b=:b', {'a': 1, 'b': 2}, 'postgresql', 'SELECT * FROM t WHERE a=%s AND b=%s', (1, 2))

    def test_repeated_parameters(self):
        self.check('SELECT * FROM t WHERE a=:x OR b=:x', {'x': 'value'}, 'sqlite', 'SELECT * FROM t WHERE a=? OR b=?', ('value', 'value'))

    def test_literals_identifiers_casts_urls_comments(self):
        sql = "SELECT 'http://example.test:443', \"time:zone\", value::text -- :ignored\n FROM t /* :ignored2 */ WHERE id=:id"
        out, values = render_named_query(sql, {'id': 7}, style='postgresql')
        self.assertIn("'http://example.test:443'", out)
        self.assertIn('"time:zone"', out)
        self.assertIn('value::text', out)
        self.assertIn('-- :ignored', out)
        self.assertIn('/* :ignored2 */', out)
        self.assertEqual(values, (7,))

    def test_missing_and_unused_parameters(self):
        with self.assertRaises(ValueError): render_named_query('SELECT :missing', {}, style='sqlite')
        with self.assertRaises(ValueError): render_named_query('SELECT :used', {'used': 1, 'extra': 2}, style='sqlite')

    def test_values_are_not_interpolated(self):
        value = "x' OR 1=1 --"
        sql, params = render_named_query('SELECT * FROM t WHERE name=:name', {'name': value}, style='sqlite')
        self.assertNotIn(value, sql)
        self.assertEqual(params, (value,))


class SQLiteRepositoryInfrastructureTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd); os.unlink(self.path)
        migrate(self.path)
        self.backend = SQLiteBackend(self.path)
        with self.backend.transaction() as db:
            db.execute("INSERT INTO roles(name,description) VALUES (?,?)", ('employee','Employee'))
            db.execute("INSERT INTO users(email,display_name,status,password_hash) VALUES (?,?,?,?)", ('repo@example.test','Repo User','active','hash'))
            db.execute("INSERT INTO resource_types(name,code,description) VALUES (?,?,?)", ('Equipment','equipment','Equipment'))
            db.execute("INSERT INTO resources(resource_type_id,name,description,created_by,updated_by) VALUES (?,?,?,?,?)", (1,'Test Resource','Desc',1,1))

    def tearDown(self):
        try: os.unlink(self.path)
        except FileNotFoundError: pass

    def test_all_repositories_read_write_and_mapping(self):
        with TransactionService(self.backend).transaction() as db:
            users = UserRepository(self.backend, db)
            resources = ResourceRepository(self.backend, db)
            bookings = BookingRepository(self.backend, db)
            qr = QrTokenRepository(self.backend, db)
            life = LifecycleRepository(self.backend, db)
            audit = AuditRepository(self.backend, db)
            self.assertEqual(users.by_email('repo@example.test')['status'], 'active')
            self.assertEqual(resources.by_id(1)['name'], 'Test Resource')
            resources.update_name(1, 'Renamed', 1)
            start = '2099-01-01T10:00:00+00:00'; end = '2099-01-01T11:00:00+00:00'
            bookings.create(1, 1, start, end, 'purpose', 1)
            booking = bookings.by_id(1)
            self.assertEqual(booking['purpose'], 'purpose')
            qr.create(1, 'hash-value', 1)
            self.assertEqual(qr.by_hash('hash-value')['resource_id'], 1)
            life.checkout(1, 1, 1, 'good', 'notes')
            self.assertEqual(life.active_checkout(1)['user_id'], 1)
            life.checkin(1, 1, 1, 1, 'good', 'ok')
            self.assertIsNotNone(life.checkin_for_checkout(1))
            audit.record(1, 'test', 'resource', 1, 'req')
        with self.backend.transaction() as db:
            self.assertEqual(ResourceRepository(self.backend, db).by_id(1)['name'], 'Renamed')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit_logs').fetchone()[0], 1)

    def test_rollback_and_failed_transaction_recovery(self):
        with self.assertRaises(RuntimeError):
            with TransactionService(self.backend).transaction() as db:
                UserRepository(self.backend, db).create('rollback@example.test', 'Rollback')
                raise RuntimeError('expected')
        with self.backend.transaction() as db:
            self.assertIsNone(UserRepository(self.backend, db).by_email('rollback@example.test'))
            with self.assertRaises(sqlite3.IntegrityError):
                UserRepository(self.backend, db).create('repo@example.test', 'Duplicate')
            db.rollback()
        with self.backend.transaction() as db:
            self.assertEqual(db.execute('SELECT 1').fetchone()[0], 1)

    def test_repositories_require_managed_connection(self):
        with self.assertRaises(ValueError): UserRepository(self.backend, None)
        self.assertEqual(self.backend.name, 'sqlite')
        self.assertEqual(self.backend.classify_error(sqlite3.IntegrityError('x')), DatabaseErrorCategory.INTEGRITY_FAILURE)


if __name__ == '__main__':
    unittest.main()
