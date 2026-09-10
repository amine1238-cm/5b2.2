import io, json, os, signal, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from resource_platform.application import create_app
from resource_platform.config import Settings
from resource_platform.database import migrate, connect, verify_migration_integrity
from resource_platform.__main__ import request_shutdown

ROOT=Path(__file__).resolve().parents[1]
class Increment4Tests(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(self.path); migrate(self.path)
        self.settings=Settings(Path(self.path),"test-secret",False,28800,"test",qr_token_encryption_key="",migration_manifest_path=(ROOT/"migrations.manifest.json").resolve())
        self.app=create_app(self.settings,auto_migrate=False)
    def tearDown(self):
        try: os.unlink(self.path)
        except FileNotFoundError: pass
    def req(self,path="/health/live",headers=None):
        out=[]; env={"REQUEST_METHOD":"GET","PATH_INFO":path,"wsgi.input":io.BytesIO(),"CONTENT_LENGTH":"0","SERVER_NAME":"test","SERVER_PORT":"80","wsgi.url_scheme":"http","wsgi.errors":io.BytesIO()}
        for k,v in (headers or {}).items(): env["HTTP_"+k.upper().replace("-","_")]=v
        body=b"".join(self.app(env,lambda s,h:out.append((s,h))))
        return out[0],body
    def test_development_and_test_config(self):
        self.assertEqual(Settings(Path("x"),"secret",environment="development").validate().environment,"development")
        self.assertEqual(Settings(self.path,"secret",environment="test").validate().environment,"test")
    def test_test_missing_or_shared_database_rejected(self):
        for p in (Path("x"), Path("resource_platform.sqlite3"), Path(".")):
            with self.assertRaises(RuntimeError): Settings(p,"secret",environment="test").validate()
    def test_staging_production_missing_placeholder_and_secure_rules(self):
        for env in ("staging","production"):
            base=dict(environment=env,cookie_secure=True,allowed_hosts=("example.test",),database_url="postgresql://db",migration_manifest_path=(ROOT/"migrations.manifest.json").resolve())
            for sec,qr in (("", "real"),("development-only-secret-change-me","real"),("real-session-secret-123", ""),("real-session-secret-123","generate-a-separate-fernet-key-per-environment")):
                with self.assertRaises(RuntimeError): Settings(Path("/tmp/db"),sec,qr_token_encryption_key=qr,**base).validate()
            with self.assertRaises(RuntimeError): Settings(Path("/tmp/db"),"real-session-secret-123",qr_token_encryption_key="real",cookie_secure=False,**{k:v for k,v in base.items() if k!='cookie_secure'}).validate()
    def test_allowed_host_and_database_validation(self):
        common=dict(environment="production",session_secret="real-session-secret-123",qr_token_encryption_key="real",cookie_secure=True,allowed_hosts=("example.test",),migration_manifest_path=(ROOT/"migrations.manifest.json").resolve())
        with self.assertRaises(RuntimeError): Settings(Path("/tmp/db"),database_url="bad",**common).validate()
        with self.assertRaises(RuntimeError): Settings(Path("/tmp/db"),database_url="postgresql://db",allowed_hosts=("bad host",),**{k:v for k,v in common.items() if k!='allowed_hosts'}).validate()
        self.assertEqual(Settings(Path("/tmp/db"),database_url="postgresql://db",allowed_hosts=("example.test",),**{k:v for k,v in common.items() if k not in {'allowed_hosts'}}).validate().environment,"production")
    def test_invalid_environment_log_port_host(self):
        with self.assertRaises(RuntimeError): Settings(self.path,"s",environment="x").validate()
        with self.assertRaises(RuntimeError): Settings(self.path,"s",log_level="NOPE").validate()
        with self.assertRaises(RuntimeError): Settings(self.path,"s",port=0).validate()
        with self.assertRaises(RuntimeError): Settings(self.path,"s",host="bad host").validate()
    def test_live_request_id_and_log(self):
        with self.assertLogs("resource_platform",level="INFO") as logs: st,b=self.req(headers={"X-Request-ID":"abc-123"})
        self.assertEqual(st[0],"200 OK"); self.assertIn(("X-Request-ID","abc-123"),st[1]); self.assertTrue(json.loads(logs.output[0].split("resource_platform:",1)[-1].strip())['request_id']=='abc-123')
    def test_bad_request_id_replaced(self):
        st,_=self.req(headers={"X-Request-ID":"bad\nvalue"}); rid=dict(st[1])["X-Request-ID"]; self.assertNotEqual(rid,"bad\nvalue"); self.assertLessEqual(len(rid),128)
    def test_ready(self):
        st,b=self.req("/health/ready"); self.assertEqual(st[0],"200 OK"); self.assertIn(b'"ready"',b)
    def test_ready_db_unavailable(self):
        self.app=create_app(Settings(Path(self.path+"/missing"),"secret",environment="test",migration_manifest_path=(ROOT/"migrations.manifest.json").resolve()),auto_migrate=False); st,b=self.req("/health/ready"); self.assertEqual(st[0],"503 Service Unavailable"); self.assertNotIn(str(self.path).encode(),b)
    def test_ready_missing_schema(self):
        fd,p=tempfile.mkstemp(); os.close(fd); db=connect(p); db.close(); self.app=create_app(Settings(Path(p),"secret",environment="test",migration_manifest_path=(ROOT/"migrations.manifest.json").resolve()),auto_migrate=False); st,_=self.req("/health/ready"); self.assertEqual(st[0],"503 Service Unavailable"); os.unlink(p)
    def test_manifest_cases(self):
        self.assertTrue(verify_migration_integrity())
        with tempfile.TemporaryDirectory() as d:
            mp=Path(d)/"missing.json"
            with self.assertRaises(RuntimeError): verify_migration_integrity(manifest_path=mp)
            bad=Path(d)/"bad.json"; bad.write_text("{")
            with self.assertRaises(RuntimeError): verify_migration_integrity(manifest_path=bad)
            src=Path(d)/"migrations"; src.mkdir()
            for f in (ROOT/"migrations").glob("*.sql"): (src/f.name).write_bytes(f.read_bytes())
            man=Path(d)/"manifest.json"; man.write_text(json.dumps({f.name:"bad" for f in src.glob("*.sql")}))
            with self.assertRaises(RuntimeError): verify_migration_integrity(src,man)
    def test_manifest_is_cwd_independent(self):
        old=os.getcwd()
        try:
            os.chdir("/"); self.assertTrue(verify_migration_integrity())
        finally: os.chdir(old)
    def test_shutdown_helper_is_idempotent_and_bounded(self):
        class Server:
            def __init__(self): self.calls=0
            def shutdown(self): self.calls+=1
        import threading
        server=Server(); ev=threading.Event(); self.assertTrue(request_shutdown(server,ev,1)); self.assertFalse(request_shutdown(server,ev,1)); self.assertEqual(server.calls,1)
    def test_shutdown_timeout_returns_false(self):
        class Server:
            def shutdown(self): import time; time.sleep(.2)
        import threading
        self.assertFalse(request_shutdown(Server(),threading.Event(),.01))
    def test_migration_integrity_fresh_database(self):
        self.assertTrue(verify_migration_integrity()); db=connect(self.path); self.assertTrue(verify_migration_integrity()); db.close()
if __name__=="__main__": unittest.main()
