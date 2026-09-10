from .models import has_permission
from .security import verify_session

def cookie_value(environ, name):
    for item in environ.get("HTTP_COOKIE", "").split(";"):
        key, sep, value = item.strip().partition("=")
        if sep and key == name: return value
    return None

def current_user(environ, db, settings):
    token = cookie_value(environ, "session")
    if not token: return None
    payload = verify_session(token, settings.session_secret, settings.session_max_age)
    if not payload: return None
    return db.execute("SELECT * FROM users WHERE id=? AND status='active'", (payload["uid"],)).fetchone()

def require_permission(db, user, permission):
    return bool(user and has_permission(db, user["id"], permission))
