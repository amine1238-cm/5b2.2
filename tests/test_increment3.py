import os, tempfile, unittest, hashlib
from pathlib import Path
from resource_platform.database import connect,migrate
from resource_platform.qr import new_token,token_hash,resource_url,svg_for_url
from resource_platform.qr import png_for_url
from resource_platform.models import can_manage_resource

class Increment3Tests(unittest.TestCase):
 def setUp(self):
  fd,self.path=tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(self.path); migrate(self.path); self.db=connect(self.path)
 def tearDown(self): self.db.close();
 def test_migration_0004_schema(self):
  for t in ('qr_tokens','checkouts','checkins'): self.assertIsNotNone(self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone())
  idx={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
  for i in ('idx_qr_active_resource','idx_qr_token_hash','idx_active_checkout_resource'): self.assertIn(i,idx)
  fk={r[2] for r in self.db.execute('PRAGMA foreign_key_list(checkouts)')}; self.assertIn('resources',fk); self.assertIn('users',fk)
 def test_migration_idempotent(self): self.assertEqual(migrate(self.path),[]); self.assertEqual(self.db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0],len(list(Path(__file__).resolve().parents[1].joinpath("migrations").glob("*.sql"))))
 def test_qr_random_and_hash(self):
  a,b=new_token(),new_token(); self.assertNotEqual(a,b); self.assertEqual(token_hash(a),hashlib.sha256(a.encode()).hexdigest()); self.assertNotEqual(a,token_hash(a))
 @unittest.skipUnless(os.getenv("RUN_QR_DECODER_TESTS") == "1", "QR decoder integration unavailable: install .[test] and set RUN_QR_DECODER_TESTS=1")
 def test_qr_png_decodes_exact_payload(self):
  import cv2, numpy as np
  t=new_token(); u=resource_url(t); png=png_for_url(u)
  from PIL import Image
  from io import BytesIO
  image_pil=Image.open(BytesIO(png))
  image_pil.load()
  self.assertIn(image_pil.mode, ('RGB', 'L'))
  image=np.array(image_pil.convert('L'))
  detector=cv2.QRCodeDetector()
  decoded,points,_=detector.detectAndDecode(image)
  if points is None or decoded != u:
   # Keep the original image as the primary input. This lossless nearest-
   # neighbor fallback only accommodates OpenCV builds that need larger
   # sampling modules; it does not alter the QR payload or assertion.
   enlarged=cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
   decoded,points,_=detector.detectAndDecode(enlarged)
  if points is None or decoded != u:
   print('QR diagnostics: width=%d height=%d mode=%s png_bytes=%d payload_length=%d box_size=10 border=4 error_correction=M opencv=%s' % (image.shape[1], image.shape[0], image_pil.mode, len(png), len(u), cv2.__version__))
  self.assertTrue(points is not None); self.assertEqual(decoded,u)
  self.assertIn(t,decoded); self.assertNotIn('/resources/123',decoded); self.assertNotIn('booking',decoded.lower()); self.assertNotIn('@',decoded)

 def test_qr_svg_uses_same_payload_function(self):
  t=new_token(); u=resource_url(t); x=svg_for_url(u).decode(); self.assertTrue(x.startswith('<?xml')); self.assertNotIn('/resources/123',x)
 def test_qr_token_invalidated_hash_is_not_reusable(self):
  old=new_token(); self.db.execute("INSERT INTO roles(name,description) VALUES ('employee','x')"); self.db.execute("INSERT INTO users(email,display_name) VALUES ('x@test','x')"); self.db.execute("INSERT INTO resource_types(name,code,description) VALUES ('x','x','x')"); self.db.execute("INSERT INTO resources(resource_type_id,name) VALUES (1,'x')"); self.db.execute('INSERT INTO qr_tokens(resource_id,token_hash) VALUES (1,?)',(token_hash(old),)); self.db.execute('UPDATE qr_tokens SET invalidated_at=CURRENT_TIMESTAMP'); self.db.commit(); self.assertIsNone(self.db.execute('SELECT 1 FROM qr_tokens WHERE token_hash=? AND invalidated_at IS NULL',(token_hash(old),)).fetchone())
 def test_missing_inputs_fail_closed(self): self.assertFalse(can_manage_resource(self.db,None,None)); self.assertFalse(can_manage_resource(self.db,1,{}))
if __name__=='__main__': unittest.main()
