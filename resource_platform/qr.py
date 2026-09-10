from hashlib import sha256
import base64, hashlib, hmac, secrets
from io import StringIO, BytesIO
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderSVG
try:
    import qrcode
except ImportError:
    qrcode = None
try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception

def new_token():
    return secrets.token_urlsafe(32)

def token_hash(token):
    return sha256(token.encode("ascii")).hexdigest()

def resource_url(token, base_url="http://127.0.0.1:8000"):
    return base_url.rstrip("/") + "/qr/" + token

def _fernet(key):
    if not key or key == "generate-a-separate-fernet-key-per-environment" or Fernet is None:
        raise RuntimeError("QR_TOKEN_ENCRYPTION_KEY and cryptography are required for QR token storage")
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise RuntimeError("QR_TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from exc

def encrypt_token(token, key):
    return _fernet(key).encrypt(token.encode("ascii")).decode("ascii")

def decrypt_token(value, key):
    if not value:
        return None
    try:
        return _fernet(key).decrypt(value.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError, TypeError, UnicodeError, RuntimeError):
        return None

def svg_for_url(url):
    widget = qr.QrCodeWidget(url); x0,y0,x1,y1=widget.getBounds()
    drawing=Drawing(x1-x0,y1-y0,transform=[1,0,0,1,-x0,-y0]); drawing.add(widget)
    out=StringIO(); renderSVG.drawToFile(drawing,out); return out.getvalue().encode("utf-8")

def png_for_url(url):
    """Generate a decoder-friendly, lossless QR PNG for *url*."""
    if qrcode is None:
        raise RuntimeError(
            'PNG QR generation requires the test extra; install with '
            'python -m pip install -e ".[test]"'
        )
    qr_code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr_code.add_data(url)
    qr_code.make(fit=True)
    image = qr_code.make_image(fill_color="black", back_color="white").convert("RGB")
    out = BytesIO()
    image.save(out, format="PNG", optimize=False)
    return out.getvalue()
