import os, sqlite3, tempfile, unittest
from pathlib import Path
from datetime import datetime, timezone
from resource_platform.database_backend import SQLiteBackend
from resource_platform.database import migrate
from resource_platform.repositories import UserRepository, ResourceRepository, BookingRepository, QrTokenRepository, LifecycleRepository, AuditRepository
from resource_platform.transaction_service import TransactionService

class SqliteRepositoryTests(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(self.path); migrate(self.path); self.backend=SQLiteBackend(self.path)
    def tearDown(self):
        if os.path.exists(self.path): os.unlink(self.path)
    def test_rows_and_transaction_commit(self):
        with TransactionService(self.backend).transaction() as db:
            db.execute("INSERT INTO roles(name,description) VALUES (?,?)",("employee","Employee"))
            self.assertEqual(UserRepository(self.backend,db).by_id(1),None)
        with self.backend.transaction() as db: self.assertEqual(db.execute("SELECT COUNT(*) FROM roles").fetchone()[0],1)
    def test_transaction_rollback(self):
        with self.assertRaises(RuntimeError):
            with TransactionService(self.backend).transaction() as db:
                db.execute("INSERT INTO roles(name,description) VALUES (?,?)",("employee","Employee")); raise RuntimeError("expected")
        with self.backend.transaction() as db: self.assertEqual(db.execute("SELECT COUNT(*) FROM roles").fetchone()[0],0)
    def test_repository_reads_mapping_rows(self):
        with self.backend.transaction() as db:
            db.execute("INSERT INTO roles(name,description) VALUES (?,?)",("employee","Employee"))
            db.execute("INSERT INTO users(email,display_name,status) VALUES (?,?,?)",("u@example.test","User","active"))
        with self.backend.transaction() as db:
            row=UserRepository(self.backend,db).by_email("u@example.test"); self.assertEqual(row["email"],"u@example.test")
    def test_audit_same_transaction(self):
        with self.assertRaises(RuntimeError):
            with self.backend.transaction() as db:
                db.execute("INSERT INTO roles(name,description) VALUES (?,?)",("manager","Manager")); AuditRepository(self.backend,db).record(None,"x","role"); raise RuntimeError("rollback")
        with self.backend.transaction() as db: self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0],0)
    def test_repository_requires_managed_connection(self):
        with self.assertRaises(ValueError): UserRepository(self.backend,None)

if __name__=="__main__": unittest.main()
