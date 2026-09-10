import io, os, re, sqlite3, tempfile, threading, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from resource_platform.application import create_app
from resource_platform.config import Settings
from resource_platform.database import connect, migrate
from resource_platform.qr import new_token, token_hash, encrypt_token, resource_url
from resource_platform.seed import seed

ROOT = Path(__file__).resolve().parents[1]

class Increment3MatrixTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.sqlite3'); os.close(fd); os.unlink(self.path)
        migrate(self.path); seed(self.path)
        self.key = Fernet.generate_key().decode()
        self.settings = Settings(Path(self.path), 'test-secret-which-is-long-enough', False, 28800, 'test', qr_token_encryption_key=self.key)
        self.app = create_app(self.settings, auto_migrate=False)

    def tearDown(self):
        try: os.unlink(self.path)
        except FileNotFoundError: pass

    def db(self): return connect(self.path)

    def request(self, path, method='GET', body=b'', cookie=''):
        result=[]; errors=io.BytesIO()
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'wsgi.input':io.BytesIO(body),'CONTENT_LENGTH':str(len(body)), 'HTTP_COOKIE':cookie,'REMOTE_ADDR':'127.0.0.1','SERVER_NAME':'test','SERVER_PORT':'80','wsgi.url_scheme':'http','wsgi.errors':errors}
        data=b''.join(self.app(env, lambda s,h: result.append((s,h))))
        return result[0], data, errors.getvalue().decode()

    def login(self, email='admin@example.test', password='AdminPassphrase-2026!'):
        status,_,_=self.request('/login','POST',f'email={email}&password={password}'.encode())
        cookies=[v for k,v in status[1] if k.lower()=='set-cookie']
        return cookies[0].split(';',1)[0] if cookies else ''

    def ids(self):
        db=self.db(); out={r['email']:r['id'] for r in db.execute('SELECT id,email FROM users')}; db.close(); return out

    def resource(self, manager=None, status='available'):
        db=self.db();
        if manager:
            r=db.execute('SELECT * FROM resources WHERE responsible_manager_id=? LIMIT 1',(manager,)).fetchone()
        else: r=db.execute('SELECT * FROM resources LIMIT 1').fetchone()
        db.execute('UPDATE resources SET status=? WHERE id=?',(status,r['id'])); db.commit(); db.close(); return r

    def booking(self, resource_id, owner_id, status='confirmed'):
        db=self.db()
        start=(datetime.now(timezone.utc)-timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
        end=(datetime.now(timezone.utc)+timedelta(minutes=55)).strftime('%Y-%m-%d %H:%M:%S')
        cur=db.execute("INSERT INTO bookings(resource_id,owner_user_id,start_at_utc,end_at_utc,purpose,created_by,status) VALUES (?,?,?,?,?,?,?)", (resource_id,owner_id,start,end,'test',owner_id,status))
        db.commit(); bid=cur.lastrowid; db.close(); return bid

    def token_row(self, rid):
        db=self.db(); row=db.execute('SELECT * FROM qr_tokens WHERE resource_id=? AND invalidated_at IS NULL',(rid,)).fetchone(); db.close(); return row

    def test_qr_print_twice_reuses_token_and_has_no_raw_url(self):
        ids=self.ids(); r=self.resource(); c=self.login(); s1,b1,_=self.request(f'/admin/resources/{r["id"]}/qr/print',cookie=c); s2,b2,_=self.request(f'/admin/resources/{r["id"]}/qr/print',cookie=c)
        self.assertEqual(s1[0], '200 OK'); self.assertEqual(s2[0], '200 OK'); row=self.token_row(r['id']); self.assertIsNotNone(row)
        token=self._decrypt(row['encrypted_token']); url=resource_url(token)
        self.assertNotIn(token.encode(), b1); self.assertNotIn(url.encode(), b1); self.assertNotIn(b'data-qr-url', b1); self.assertEqual(b1,b2)
        db=self.db(); self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='qr.print'").fetchone()[0],2); db.close()

    def _decrypt(self, value):
        return Fernet(self.key.encode()).decrypt(value.encode()).decode()

    def test_qr_regeneration_invalidates_old_and_is_admin_only(self):
        r=self.resource(); admin=self.login(); self.request(f'/admin/resources/{r["id"]}/qr/print',cookie=admin); old=self.token_row(r['id']); old_token=self._decrypt(old['encrypted_token'])
        s,b,_=self.request(f'/admin/resources/{r["id"]}/qr/regenerate','POST',cookie=admin); self.assertEqual(s[0],'200 OK'); new=self.token_row(r['id']); new_token=self._decrypt(new['encrypted_token']); self.assertNotEqual(old_token,new_token); self.assertNotIn(old_token.encode(),b); self.assertNotIn(resource_url(new_token).encode(),b)
        db=self.db(); self.assertIsNone(db.execute('SELECT 1 FROM qr_tokens WHERE token_hash=? AND invalidated_at IS NULL',(token_hash(old_token),)).fetchone()); self.assertIsNotNone(db.execute('SELECT 1 FROM qr_tokens WHERE token_hash=? AND invalidated_at IS NULL',(token_hash(new_token),)).fetchone()); self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='qr.regenerate'").fetchone()[0],1); db.close()
        employee=self.login('employee@example.test','EmployeePassphrase-2026!'); s,_,_=self.request(f'/admin/resources/{r["id"]}/qr/regenerate','POST',cookie=employee); self.assertEqual(s[0],'403 Forbidden')

    def test_qr_public_minimal_invalid_and_guess_safe(self):
        r=self.resource(); db=self.db(); t=new_token(); db.execute('INSERT INTO qr_tokens(resource_id,token_hash) VALUES (?,?)',(r['id'],token_hash(t))); db.commit(); db.close()
        s,b,_=self.request('/qr/'+t); self.assertEqual(s[0],'200 OK'); self.assertIn(b'Fictional Pool Car',b); self.assertNotIn(b'Fictional Employee',b); self.assertNotIn(b'booking',b.lower()); self.assertNotIn(b'owner',b.lower()); self.assertNotIn(b'data-qr-url',b.lower())
        for guessed in [new_token(), t+'x']:
            s,b,_=self.request('/qr/'+guessed); self.assertEqual(s[0],'404 Not Found'); self.assertNotIn(r['name'].encode(),b)
        c=self.login('employee@example.test','EmployeePassphrase-2026!'); s,_,_=self.request('/qr/'+t+'/checkout','POST',cookie=c); self.assertIn(s[0],('404 Not Found','401 Unauthorized'))

    def test_qr_decryption_failure_fails_closed(self):
        r=self.resource(); c=self.login(); self.request(f'/admin/resources/{r["id"]}/qr/print',cookie=c); bad=Settings(Path(self.path),'test-secret-which-is-long-enough',False,28800,'test',qr_token_encryption_key=Fernet.generate_key().decode()); app=create_app(bad,auto_migrate=False); old=self.app; self.app=app
        s,b,_=self.request(f'/admin/resources/{r["id"]}/qr/print',cookie=c); self.assertEqual(s[0],'503 Service Unavailable'); self.assertNotIn(b'http://127.0.0.1:8000/qr/',b); self.app=old

    def test_qr_config_rejects_missing_and_placeholder_in_staging_production(self):
        for env in ('staging','production'):
            for key in ('', 'generate-a-separate-fernet-key-per-environment'):
                with self.assertRaises(RuntimeError): Settings(Path(self.path),'secret',environment=env,qr_token_encryption_key=key).validate()
        for env in ('development','test'):
            Settings(Path(self.path),'secret',environment=env,qr_token_encryption_key='').validate()

    def test_employee_checkout_own_booking_and_status_audit(self):
        ids=self.ids(); r=self.resource(); bid=self.booking(r['id'],ids['employee@example.test']); c=self.login('employee@example.test','EmployeePassphrase-2026!'); s,_,_=self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good&notes=handover',c); self.assertEqual(s[0],'303 See Other'); db=self.db(); row=db.execute('SELECT * FROM checkouts').fetchone(); self.assertEqual(row['booking_id'],bid); self.assertEqual(db.execute('SELECT status FROM resources WHERE id=?',(r['id'],)).fetchone()[0],'checked_out'); self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='checkout.create'").fetchone()[0],1); db.close()

    def test_employee_checkout_denials(self):
        ids=self.ids(); r=self.resource(); employee=self.login('employee@example.test','EmployeePassphrase-2026!')
        for status in ('archived','damaged','unavailable','under_maintenance'):
            db=self.db(); db.execute('UPDATE resources SET status=? WHERE id=?',(status,r['id'])); db.commit(); db.close(); s,_,_=self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good',employee); self.assertEqual(s[0],'403 Forbidden')
            db=self.db(); db.execute('UPDATE resources SET status=\'available\' WHERE id=?',(r['id'],)); db.commit(); db.close()
        s,_,_=self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good',employee); self.assertEqual(s[0],'409 Conflict')
        other=self.ids()['admin@example.test']; self.booking(r['id'],other); s,_,_=self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good',employee); self.assertEqual(s[0],'409 Conflict')

    def test_manager_scope_and_no_override(self):
        ids=self.ids(); owned=self.resource(ids['manager@example.test']); other=self.resource(ids['manager2@example.test']); self.booking(owned['id'],ids['manager@example.test']); c=self.login('manager@example.test','ManagerPassphrase-2026!'); s,_,_=self.request(f'/resources/{owned["id"]}/checkout','POST',b'condition_before=good',c); self.assertEqual(s[0],'303 See Other')
        s,_,_=self.request(f'/resources/{other["id"]}/checkout','POST',b'condition_before=good',c); self.assertEqual(s[0],'403 Forbidden')
        s,_,_=self.request(f'/resources/{owned["id"]}/checkout','POST',b'condition_before=good&notes=override',c); self.assertIn(s[0],('409 Conflict','403 Forbidden'))

    def test_admin_override_requires_reason_and_is_recorded(self):
        ids=self.ids(); r=self.resource(); admin=self.login(); s,_,_=self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good',admin); self.assertEqual(s[0],'409 Conflict'); s,_,_=self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good&notes=Emergency+handover',admin); self.assertEqual(s[0],'303 See Other'); db=self.db(); row=db.execute('SELECT * FROM checkouts').fetchone(); self.assertEqual(row['is_admin_override'],1); self.assertEqual(row['override_reason'],'Emergency handover'); db.close()
        for email,pw in [('employee@example.test','EmployeePassphrase-2026!'),('manager@example.test','ManagerPassphrase-2026!')]:
            r2=self.resource(status='available'); c=self.login(email,pw); s,_,_=self.request(f'/resources/{r2["id"]}/checkout','POST',b'condition_before=good&notes=override',c); self.assertEqual(s[0],'409 Conflict')

    def test_duplicate_checkout_and_fk_uniqueness(self):
        ids=self.ids(); r=self.resource(); self.booking(r['id'],ids['employee@example.test']); c=self.login('employee@example.test','EmployeePassphrase-2026!'); self.assertEqual(self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good',c)[0][0],'303 See Other'); self.assertEqual(self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good',c)[0][0],'409 Conflict'); db=self.db();
        with self.assertRaises(sqlite3.IntegrityError): db.execute('INSERT INTO checkins(checkout_id,resource_id,user_id,condition_after) VALUES (999,999,999,\'good\')')
        db.close()

    def _checkout(self):
        ids=self.ids(); r=self.resource(); bid=self.booking(r['id'],ids['employee@example.test']); c=self.login('employee@example.test','EmployeePassphrase-2026!'); self.assertEqual(self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good',c)[0][0],'303 See Other'); return r,bid,c,ids

    def test_checkin_clean_damage_wrong_user_manager_admin(self):
        r,bid,c,ids=self._checkout(); s,_,_=self.request(f'/resources/{r["id"]}/checkin','POST',b'condition_after=good&notes=clean',c); self.assertEqual(s[0],'303 See Other'); db=self.db(); self.assertEqual(db.execute('SELECT status FROM resources WHERE id=?',(r['id'],)).fetchone()[0],'available'); self.assertEqual(db.execute('SELECT status FROM bookings WHERE id=?',(bid,)).fetchone()[0],'completed'); db.close()
        r,bid,c,ids=self._checkout(); wrong=self.login('manager2@example.test','ManagerTwoPassphrase-2026!'); self.assertEqual(self.request(f'/resources/{r["id"]}/checkin','POST',b'condition_after=good',wrong)[0][0],'409 Conflict'); manager=self.login('manager@example.test','ManagerPassphrase-2026!'); self.assertEqual(self.request(f'/resources/{r["id"]}/checkin','POST',b'condition_after=good&missing_accessories=tripod&problems=cracked+case',manager)[0][0],'303 See Other'); db=self.db(); row=db.execute('SELECT * FROM checkins ORDER BY id DESC LIMIT 1').fetchone(); self.assertEqual(row['damage_reported'],1); self.assertEqual(row['missing_accessories'],'tripod'); self.assertEqual(row['problems'],'cracked case'); self.assertEqual(db.execute('SELECT status FROM resources WHERE id=?',(r['id'],)).fetchone()[0],'damaged'); db.close()

    def test_checkin_duplicate_admin_and_audit(self):
        r,bid,c,ids=self._checkout(); admin=self.login(); self.assertEqual(self.request(f'/resources/{r["id"]}/checkin','POST',b'condition_after=good',admin)[0][0],'303 See Other'); self.assertEqual(self.request(f'/resources/{r["id"]}/checkin','POST',b'condition_after=good',admin)[0][0],'409 Conflict'); db=self.db(); self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='checkin.create'").fetchone()[0],1); db.close()

    def test_transaction_rollback_checkout_and_checkin(self):
        ids=self.ids(); r=self.resource(); self.booking(r['id'],ids['employee@example.test']); c=self.login('employee@example.test','EmployeePassphrase-2026!')
        with patch('resource_platform.application.audit', side_effect=RuntimeError('audit failure')):
            s,_,_=self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good',c)
        self.assertEqual(s[0],'409 Conflict'); db=self.db(); self.assertEqual(db.execute('SELECT COUNT(*) FROM checkouts').fetchone()[0],0); self.assertEqual(db.execute('SELECT status FROM resources WHERE id=?',(r['id'],)).fetchone()[0],'available'); db.close()

    def test_concurrent_checkout_and_active_uniqueness(self):
        ids=self.ids(); r=self.resource(); self.booking(r['id'],ids['employee@example.test']); c=self.login('employee@example.test','EmployeePassphrase-2026!'); results=[]; lock=threading.Lock()
        def go():
            x=self.request(f'/resources/{r["id"]}/checkout','POST',b'condition_before=good',c)[0][0]
            with lock: results.append(x)
        ts=[threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in ts]; [t.join() for t in ts]; self.assertEqual(results.count('303 See Other'),1); self.assertEqual(results.count('409 Conflict'),1)
        db=self.db(); self.assertEqual(db.execute("SELECT COUNT(*) FROM checkouts WHERE checked_in_at IS NULL AND resource_id=?",(r['id'],)).fetchone()[0],1); db.close()

    def test_concurrent_checkin_and_one_checkin_per_checkout(self):
        r,bid,c,ids=self._checkout(); results=[]; lock=threading.Lock()
        def go():
            x=self.request(f'/resources/{r["id"]}/checkin','POST',b'condition_after=good',c)[0][0]
            with lock: results.append(x)
        ts=[threading.Thread(target=go) for _ in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]; self.assertEqual(results.count('303 See Other'),1); self.assertEqual(results.count('409 Conflict'),1)
        db=self.db(); self.assertEqual(db.execute('SELECT COUNT(*) FROM checkins WHERE checkout_id=1').fetchone()[0],1); db.close()

    def test_history_not_silently_deleted_and_fk_integrity(self):
        r,bid,c,ids=self._checkout(); self.assertEqual(self.request(f'/resources/{r["id"]}/checkin','POST',b'condition_after=good',c)[0][0],'303 See Other'); db=self.db(); self.assertEqual(db.execute('SELECT COUNT(*) FROM checkouts').fetchone()[0],1); self.assertEqual(db.execute('SELECT COUNT(*) FROM checkins').fetchone()[0],1)
        with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM resources WHERE id=?',(r['id'],))
        db.close()

if __name__=='__main__': unittest.main()
