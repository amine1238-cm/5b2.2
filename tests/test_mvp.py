import os, sqlite3, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from resource_platform.application import create_app
from resource_platform.config import Settings
from resource_platform.database import connect, migrate
from resource_platform.seed import seed
from resource_platform.security import hash_password, sign_session, verify_session
from resource_platform.models import has_permission, can_manage_resource

ROOT = Path(__file__).resolve().parents[1]

class MVPTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(self.path)
        migrate(self.path); seed(self.path)
        self.settings = Settings(Path(self.path), "test-secret-which-is-long-enough", False, 28800, "test")
        self.app = create_app(self.settings, auto_migrate=False)
    def tearDown(self):
        if os.path.exists(self.path): os.unlink(self.path)
    def db(self): return connect(self.path)
    def request(self, path="/", method="GET", body=b"", cookie=""):
        result=[]; env={"REQUEST_METHOD":method,"PATH_INFO":path,"wsgi.input":BytesIO(body),"CONTENT_LENGTH":str(len(body)),"HTTP_COOKIE":cookie,"REMOTE_ADDR":"127.0.0.1","SERVER_NAME":"test","SERVER_PORT":"80","wsgi.url_scheme":"http","wsgi.errors":BytesIO()}
        data=b"".join(self.app(env, lambda status, headers: result.append((status,headers))))
        return result[0],data
    def login(self,email="admin@example.test",password="AdminPassphrase-2026!"):
        status,_=self.request("/login","POST",f"email={email}&password={password}".encode())
        cookies=[v for k,v in status[1] if k.lower()=="set-cookie"]
        return status, cookies[0].split(";",1)[0] if cookies else ""
    def test_application_startup_and_fresh_migration(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(path)
        versions=sorted(int(f.name.split("_",1)[0]) for f in (ROOT/"migrations").glob("*.sql")); self.assertEqual(migrate(path), versions); db=connect(path); self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],len(versions)); db.close(); os.unlink(path)
    def test_migration_idempotency(self):
        fd,path=tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(path)
        versions=sorted(int(f.name.split("_",1)[0]) for f in (ROOT/"migrations").glob("*.sql")); self.assertEqual(migrate(path), versions); self.assertEqual(migrate(path), []); db=connect(path); self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],len(versions)); db.close(); os.unlink(path)
    def test_migration_cli_help(self):
        result=subprocess.run([sys.executable,"-m","resource_platform.database","--help"],cwd=ROOT,text=True,capture_output=True); self.assertEqual(result.returncode,0); self.assertIn("migrate",result.stdout)
    def test_migration_cli_fresh_database(self):
        fd,path=tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(path)
        result=subprocess.run([sys.executable,"-m","resource_platform.database","migrate","--database-path",path],cwd=ROOT,text=True,capture_output=True); self.assertEqual(result.returncode,0,result.stderr); self.assertTrue(os.path.exists(path)); self.assertIn("Migration successful",result.stdout); self.assertEqual(migrate(path),[]); os.unlink(path)
    def test_migration_cli_failure_behavior(self):
        result=subprocess.run([sys.executable,"-m","resource_platform.database","migrate","--database-path",str(ROOT/"/dev/null"/"bad.db")],cwd=ROOT,text=True,capture_output=True); self.assertNotEqual(result.returncode,0); self.assertIn("Migration failed",result.stdout)
    def test_wsgi_login_success(self):
        status,cookie=self.login(); self.assertEqual(status[0],"303 See Other"); self.assertTrue(cookie.startswith("session="))
    def test_login_failure(self):
        status,_=self.login(password="incorrect-password"); self.assertEqual(status[0],"401 Unauthorized")
    def test_session_expiration(self):
        token=sign_session(1,"secret",now=100); self.assertIsNone(verify_session(token,"secret",max_age=5,now=106))
    def test_unauthorized_access(self):
        status,_=self.request("/admin/resources"); self.assertEqual(status[0],"303 See Other")
    def test_authorized_administrator_access(self):
        _,cookie=self.login(); status,data=self.request("/admin/resources",cookie=cookie); self.assertEqual(status[0],"200 OK"); self.assertIn(b"Fictional Pool Car",data)
    def test_employee_cannot_create_resource(self):
        _,cookie=self.login("employee@example.test","EmployeePassphrase-2026!"); status,_=self.request("/admin/resources/new",cookie=cookie); self.assertEqual(status[0],"403 Forbidden")
    def test_resource_creation(self):
        _,cookie=self.login(); db=self.db(); typ=db.execute("SELECT id FROM resource_types LIMIT 1").fetchone()[0]; db.close(); body=f"name=New+Resource&description=Test&resource_type_id={typ}&asset_number=NEW-001".encode(); status,_=self.request("/admin/resources/new","POST",body,cookie); self.assertEqual(status[0],"303 See Other"); db=self.db(); self.assertIsNotNone(db.execute("SELECT 1 FROM resources WHERE asset_number='NEW-001'").fetchone()); db.close()
    def test_resource_editing(self):
        _,cookie=self.login(); db=self.db(); r=db.execute("SELECT id FROM resources LIMIT 1").fetchone()[0]; typ=db.execute("SELECT resource_type_id FROM resources WHERE id=?",(r,)).fetchone()[0]; db.close(); body=f"name=Edited+Resource&description=Changed&resource_type_id={typ}&asset_number=EDIT-001".encode(); status,_=self.request(f"/admin/resources/{r}/edit","POST",body,cookie); self.assertEqual(status[0],"303 See Other"); db=self.db(); self.assertEqual(db.execute("SELECT name FROM resources WHERE id=?",(r,)).fetchone()[0],"Edited Resource"); db.close()
    def test_resource_manager_scope_restrictions(self):
        db=self.db(); m1=db.execute("SELECT id FROM users WHERE email='manager@example.test'").fetchone()[0]; m2=db.execute("SELECT id FROM users WHERE email='manager2@example.test'").fetchone()[0]; owned=db.execute("SELECT * FROM resources WHERE responsible_manager_id=? LIMIT 1",(m1,)).fetchone(); other=db.execute("SELECT * FROM resources WHERE responsible_manager_id=? LIMIT 1",(m2,)).fetchone(); self.assertIsNotNone(owned); self.assertIsNotNone(other); self.assertTrue(can_manage_resource(db,m1,owned)); self.assertFalse(can_manage_resource(db,m1,other)); db.close()
    def test_can_manage_missing_resource(self):
        db=self.db(); m=db.execute("SELECT id FROM users WHERE email='manager@example.test'").fetchone()[0]; self.assertFalse(can_manage_resource(db,m,None)); db.close()
    def test_can_manage_missing_user(self):
        db=self.db(); resource=db.execute("SELECT * FROM resources LIMIT 1").fetchone(); self.assertFalse(can_manage_resource(db,None,resource)); self.assertFalse(can_manage_resource(db,999999,resource)); db.close()
    def test_can_manage_missing_ownership_field(self):
        db=self.db(); m=db.execute("SELECT id FROM users WHERE email='manager@example.test'").fetchone()[0]; self.assertFalse(can_manage_resource(db,m,{"id":1})); self.assertFalse(can_manage_resource(db,m,{})); db.close()
    def test_audit_log_creation(self):
        _,cookie=self.login(); db=self.db(); count=db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='login_success'").fetchone()[0]; self.assertEqual(count,1); db.close()
    def test_test_database_isolation(self):
        db=self.db(); db.execute("INSERT INTO departments(name,code) VALUES ('Only Test Department','ONLYTEST')"); db.commit(); db.close(); fd,path=tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(path); migrate(path); db=connect(path); self.assertIsNone(db.execute("SELECT 1 FROM departments WHERE code='ONLYTEST'").fetchone()); db.close(); os.unlink(path)
    def test_seed_is_explicit(self):
        fd,path=tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(path); migrate(path); db=connect(path); self.assertIsNone(db.execute("SELECT 1 FROM users LIMIT 1").fetchone()); db.close(); os.unlink(path)
    def test_seed_creates_two_managers_and_two_resources(self):
        db=self.db(); self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE email LIKE 'manager%@example.test'").fetchone()[0],2); self.assertGreaterEqual(db.execute("SELECT COUNT(*) FROM resources WHERE responsible_manager_id IS NOT NULL").fetchone()[0],2); db.close()

    def booking_resource(self):
        db = self.db()
        try:
            return db.execute("SELECT * FROM resources WHERE status='available' LIMIT 1").fetchone()
        finally:
            db.close()

    def future_booking_times(self):
        """Return a future Europe/Berlin interval that cannot expire with time."""
        start = datetime.now(ZoneInfo("Europe/Berlin")) + timedelta(minutes=10)
        start = start.replace(second=0, microsecond=0)
        end = start + timedelta(minutes=60)
        return start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M")

    def booking_form(self, start_at, end_at, purpose="x"):
        return urlencode({
            "start_at": start_at,
            "end_at": end_at,
            "purpose": purpose,
        }).encode()
    def test_successful_booking(self):
        r=self.booking_resource(); _,cookie=self.login('employee@example.test','EmployeePassphrase-2026!'); start, end = self.future_booking_times(); body=self.booking_form(start, end, "Site visit"); status,_=self.request(f'/resources/{r["id"]}/book','POST',body,cookie); self.assertEqual(status[0],'303 See Other'); db=self.db(); self.assertEqual(db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0],1); db.close()
    def test_invalid_time_range(self):
        r=self.booking_resource(); _,c=self.login('employee@example.test','EmployeePassphrase-2026!'); start, end = self.future_booking_times(); st,d=self.request(f'/resources/{r["id"]}/book','POST',self.booking_form(end, start),c); self.assertEqual(st[0],'409 Conflict'); self.assertIn(b'Start time',d)
    def test_past_booking(self):
        r=self.booking_resource(); _,c=self.login('employee@example.test','EmployeePassphrase-2026!'); st,_=self.request(f'/resources/{r["id"]}/book','POST',b'start_at=2020-01-10T09%3A00&end_at=2020-01-10T10%3A00&purpose=x',c); self.assertEqual(st[0],'409 Conflict')
    def test_overlapping_booking(self):
        r=self.booking_resource(); _,c=self.login('employee@example.test','EmployeePassphrase-2026!'); start, end = self.future_booking_times(); first=self.booking_form(start, end); self.assertEqual(self.request(f'/resources/{r["id"]}/book','POST',first,c)[0][0],'303 See Other'); overlap_start = (datetime.fromisoformat(start) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M"); overlap_end = (datetime.fromisoformat(end) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M"); st,d=self.request(f'/resources/{r["id"]}/book','POST',self.booking_form(overlap_start, overlap_end),c); self.assertEqual(st[0],'409 Conflict'); self.assertIn(b'already booked',d)
    def test_booking_after_cancelled(self):
        r=self.booking_resource(); _,c=self.login('employee@example.test','EmployeePassphrase-2026!'); start, end = self.future_booking_times(); body=self.booking_form(start, end); st,_=self.request(f'/resources/{r["id"]}/book','POST',body,c); self.assertEqual(st[0],'303 See Other'); bid=int(dict((k,v) for k,v in st[1]).get('Location','').split('/')[-1]); self.assertEqual(self.request(f'/bookings/{bid}/cancel','POST',b'',c)[0][0],'303 See Other'); self.assertEqual(self.request(f'/resources/{r["id"]}/book','POST',body,c)[0][0],'303 See Other')
    def test_archived_damaged_unavailable_maintenance_rejected(self):
        db=self.db(); r=self.booking_resource(); _,c=self.login('employee@example.test','EmployeePassphrase-2026!');
        for status in ('archived','damaged','unavailable','under_maintenance'):
            db.execute('UPDATE resources SET status=? WHERE id=?',(status,r['id'])); db.commit(); st,_=self.request(f'/resources/{r["id"]}/book','GET',cookie=c); self.assertEqual(st[0],'403 Forbidden')
        db.close()
    def test_employee_only_own_bookings(self):
        r=self.booking_resource(); _,c=self.login('employee@example.test','EmployeePassphrase-2026!'); start, end = self.future_booking_times(); self.request(f'/resources/{r["id"]}/book','POST',self.booking_form(start, end),c); st,d=self.request('/bookings',cookie=c); self.assertEqual(st[0],'200 OK'); self.assertIn(b'Bookings',d)
    def test_admin_booking_access_and_audit(self):
        r=self.booking_resource(); _,c=self.login(); start, end = self.future_booking_times(); self.assertEqual(self.request(f'/resources/{r["id"]}/book','POST',self.booking_form(start, end),c)[0][0],'303 See Other'); db=self.db(); self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='booking.create'").fetchone()[0],1); db.close()
    def test_booking_across_midnight_and_timezone(self):
        r=self.booking_resource(); _,c=self.login('employee@example.test','EmployeePassphrase-2026!'); st,_=self.request(f'/resources/{r["id"]}/book','POST',b'start_at=2026-09-10T23%3A00&end_at=2026-09-11T01%3A00&purpose=x',c); self.assertEqual(st[0],'303 See Other'); db=self.db(); b=db.execute('SELECT * FROM bookings').fetchone(); self.assertTrue(b['start_at_utc'].endswith('+00:00')); db.close()
    def test_dst_transition_rejected(self):
        r=self.booking_resource(); _,c=self.login('employee@example.test','EmployeePassphrase-2026!'); st,_=self.request(f'/resources/{r["id"]}/book','POST',b'start_at=2027-03-28T02%3A30&end_at=2027-03-28T03%3A30&purpose=x',c); self.assertEqual(st[0],'409 Conflict')

    def test_two_simultaneous_booking_attempts(self):
        import threading
        r=self.booking_resource(); credentials=[('employee@example.test','EmployeePassphrase-2026!'),('admin@example.test','AdminPassphrase-2026!')]
        cookies=[self.login(*item)[1] for item in credentials]
        start, end = self.future_booking_times()
        body=self.booking_form(start, end, "Concurrent test")
        results=[]; lock=threading.Lock()
        def attempt(cookie):
            result=self.request(f'/resources/{r["id"]}/book','POST',body,cookie)[0][0]
            with lock: results.append(result)
        threads=[threading.Thread(target=attempt,args=(cookie,)) for cookie in cookies]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(results),['303 See Other','409 Conflict'])
        db=self.db(); self.assertEqual(db.execute("SELECT COUNT(*) FROM bookings WHERE resource_id=? AND status='confirmed'",(r['id'],)).fetchone()[0],1); db.close()

if __name__ == "__main__": unittest.main()
