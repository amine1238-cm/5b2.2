import base64, hashlib, hmac, json, secrets, time
ITERATIONS = 310000

def hash_password(password):
    if len(password) < 12: raise ValueError("Password must contain at least 12 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    return f"pbkdf2_sha256${ITERATIONS}${enc(salt)}${enc(digest)}"

def verify_password(password, encoded):
    try:
        algo, iterations, salt, digest = encoded.split("$")
        pad = lambda s: s + "=" * ((4 - len(s) % 4) % 4)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.urlsafe_b64decode(pad(salt)), int(iterations))
        return algo == "pbkdf2_sha256" and hmac.compare_digest(actual, base64.urlsafe_b64decode(pad(digest)))
    except Exception:
        return False

def sign_session(user_id, secret, now=None):
    payload = {"uid": int(user_id), "iat": int(time.time() if now is None else now), "nonce": secrets.token_urlsafe(18)}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return raw + "." + sig

def verify_session(value, secret, max_age=28800, now=None):
    try:
        raw, sig = value.split(".", 1)
        expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected): return None
        pad = "=" * ((4 - len(raw) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw + pad))
        current = int(time.time() if now is None else now)
        if payload["iat"] > current + 60 or current - int(payload["iat"]) > max_age: return None
        return payload
    except Exception:
        return None
