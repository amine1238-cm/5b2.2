from html import escape
from io import BytesIO
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs
import re
from .config import Settings
from .database import connect, migrate, verify_database_ready
from .security import sign_session, verify_password
from .dependencies import current_user, require_permission
from .models import can_manage_resource, user_can_use_resource, can_view_booking, is_admin, roles_for
from .audit import audit
import hashlib, secrets, base64, sqlite3, time, json, logging, uuid
from .qr import new_token, token_hash, resource_url, svg_for_url, encrypt_token, decrypt_token
from .schemas import clean_resource, booking_form, local_display, lifecycle_form

def _html(body, title="Resource Platform"):
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title><style>body{{font:16px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem}}label{{display:block;margin:1rem 0}}input,textarea,select{{display:block;width:100%;max-width:600px;padding:.6rem}}button{{padding:.7rem 1rem}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #bbb;padding:.5rem;text-align:left}}.error{{color:#a00}}</style></head><body>{body}</body></html>"

def _json_response(start, status, payload, headers=None):
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    common = [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(data))), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]
    start(status, common + (headers or [])); return [data]

def _response(start, status, body, headers=None):
    data = body.encode(); headers = headers or []
    common = [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(data))), ("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"), ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store")]
    start(status, common + headers); return [data]

def _formdata(environ):
    length = int(environ.get("CONTENT_LENGTH") or 0)
    raw = environ.get("wsgi.input", BytesIO()).read(length).decode("utf-8", "replace")
    return {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}

def _admin_page(db, user, body=""):
    rows = db.execute("SELECT r.*,rt.name AS type_name,l.name AS location_name FROM resources r JOIN resource_types rt ON rt.id=r.resource_type_id LEFT JOIN locations l ON l.id=r.current_location_id ORDER BY r.name").fetchall()
    shown = []
    for r in rows:
        if can_manage_resource(db, user["id"], r) or require_permission(db, user, "admin.resources.read"):
            shown.append(f"<tr><td>{escape(r['name'])}</td><td>{escape(r['type_name'])}</td><td>{escape(r['status'])}</td><td>{escape(r['location_name'] or '')}</td><td><a href='/admin/resources/{r['id']}/edit'>Edit</a></td></tr>")
    return "<h1>Resources</h1><p><a href='/admin/resources/new'>Create resource</a> | <a href='/logout'>Logout</a></p>" + body + "<table><tr><th>Name</th><th>Type</th><th>Status</th><th>Location</th><th>Action</th></tr>" + "".join(shown) + "</table>"

def _resource_form(db, action, resource=None, error=""):
    types = db.execute("SELECT * FROM resource_types WHERE archived_at IS NULL ORDER BY name").fetchall()
    selected = resource["resource_type_id"] if resource else ""
    opts = "".join(f"<option value='{t['id']}' {'selected' if t['id']==selected else ''}>{escape(t['name'])}</option>" for t in types)
    r = resource or {}
    return f"<h1>{'Edit' if resource else 'Create'} resource</h1><p class='error'>{escape(error)}</p><form method='post' action='{escape(action)}'><label>Name <input name='name' required maxlength='160' value='{escape(r['name'] if resource else '')}'></label><label>Description <textarea name='description' maxlength='2000'>{escape(r['description'] if resource else '')}</textarea></label><label>Type <select name='resource_type_id' required>{opts}</select></label><label>Asset number <input name='asset_number' maxlength='80' value='{escape((r['asset_number'] or '') if resource else '')}'></label><button>Save</button></form><p><a href='/admin/resources'>Cancel</a></p>"

def _resource_availability(db, resource_id, start=None, end=None):
    q="SELECT * FROM bookings WHERE resource_id=? AND status='confirmed'"; args=[resource_id]
    if start: q += " AND end_at_utc > ?"; args.append(start)
    if end: q += " AND start_at_utc < ?"; args.append(end)
    return db.execute(q+" ORDER BY start_at_utc",args).fetchall()

def _booking_row(db, bid):
    return db.execute("SELECT b.*,r.name AS resource_name,u.display_name AS owner_name FROM bookings b JOIN resources r ON r.id=b.resource_id JOIN users u ON u.id=b.owner_user_id WHERE b.id=?",(bid,)).fetchone()

def _booking_text(b):
    return f"{escape(b['resource_name'])} from {escape(local_display(b['start_at_utc']))} to {escape(local_display(b['end_at_utc']))}"

def _booking_form(db, action, resource, error=""):
    return f"<h1>Book {escape(resource['name'])}</h1><p class='error'>{escape(error)}</p><form method='post' action='{escape(action)}'><label>Start <input type='datetime-local' name='start_at' required></label><label>End <input type='datetime-local' name='end_at' required></label><label>Purpose <textarea name='purpose' required maxlength='2000'></textarea></label><label>Project reference <input name='project_reference' maxlength='200'></label><label>Destination <input name='destination' maxlength='200'></label><button>Confirm booking</button></form><p><a href='/resources/{resource['id']}'>Cancel</a></p>"

def _booking_list(db,user,admin=False):
    if admin: rows=db.execute("SELECT b.*,r.name resource_name,u.display_name owner_name FROM bookings b JOIN resources r ON r.id=b.resource_id JOIN users u ON u.id=b.owner_user_id ORDER BY b.start_at_utc").fetchall()
    else: rows=db.execute("SELECT b.*,r.name resource_name,u.display_name owner_name FROM bookings b JOIN resources r ON r.id=b.resource_id JOIN users u ON u.id=b.owner_user_id WHERE b.owner_user_id=? ORDER BY b.start_at_utc",(user['id'],)).fetchall()
    return "<h1>Bookings</h1><p><a href='/resources'>Resources</a> | <a href='/logout'>Logout</a></p><table><tr><th>Resource</th><th>Owner</th><th>Start</th><th>End</th><th>Status</th><th>Action</th></tr>"+"".join(f"<tr><td>{escape(r['resource_name'])}</td><td>{escape(r['owner_name'])}</td><td>{escape(local_display(r['start_at_utc']))}</td><td>{escape(local_display(r['end_at_utc']))}</td><td>{escape(r['status'])}</td><td>{('<form method=post action=/bookings/'+str(r['id'])+'/cancel><button>Cancel</button></form>') if r['status']=='confirmed' and (admin or r['owner_user_id']==user['id']) else ''}</td></tr>" for r in rows)+"</table>"

def _qr_hash(token):
    return token_hash(token)

def _new_qr(db, resource_id, actor_id, secret, action="qr.regenerate"):
    token = new_token()
    db.execute("UPDATE qr_tokens SET invalidated_at=CURRENT_TIMESTAMP, invalidated_by=? WHERE resource_id=? AND invalidated_at IS NULL", (actor_id, resource_id))
    cur=db.execute("INSERT INTO qr_tokens(resource_id,token_hash,encrypted_token,created_by) VALUES (?,?,?,?)", (resource_id,token_hash(token),encrypt_token(token,secret),actor_id))
    audit(db,actor_id,action,"qr_token",cur.lastrowid,reason="New opaque QR token generated")
    return token

def _qr_resource(db, token):
    if not token or len(token) > 128: return None
    return db.execute("SELECT r.*,rt.name type_name,l.name location_name FROM qr_tokens q JOIN resources r ON r.id=q.resource_id JOIN resource_types rt ON rt.id=r.resource_type_id LEFT JOIN locations l ON l.id=r.current_location_id WHERE q.token_hash=? AND q.invalidated_at IS NULL", (_qr_hash(token),)).fetchone()

def _lifecycle_form(action, checkin=False, error=""):
    field="condition_after" if checkin else "condition_before"
    title="Check in resource" if checkin else "Check out resource"
    extra="<label>Missing accessories <textarea name='missing_accessories'></textarea></label><label>Problems <textarea name='problems'></textarea></label>" if checkin else ""
    return f"<h1>{title}</h1><p class='error'>{escape(error)}</p><form method='post' action='{escape(action)}'><label>Condition <select name='{field}' required><option value='good'>Good</option><option value='minor_issue'>Minor issue</option><option value='damaged'>Damaged</option><option value='missing_accessory'>Missing accessory</option></select></label>{extra}<label>Notes <textarea name='notes'></textarea></label><button>Confirm</button></form>"

def _qr_page(db, resource, user):
    rows=_resource_availability(db,resource['id'])
    body=f"<h1>{escape(resource['name'])}</h1><p>Type: {escape(resource['type_name'])}</p><p>Status: {escape(resource['status'])}</p><p>Location: {escape(resource['location_name'] or '')}</p><p>Current availability: {'available' if resource['status']=='available' else 'not available'}</p>"
    if user:
        body += "<p>Authenticated actions require a confirmed booking and permission.</p>"
        if user_can_use_resource(db,user['id'],resource): body += f"<a href='/resources/{resource['id']}'>Open resource actions</a>"
    else: body += "<p><a href='/login'>Sign in to book or use this resource.</a></p>"
    return body

def create_app(settings=None, *, db_path=None, session_secret=None, auto_migrate=True):
    settings = settings or Settings.from_env(database_path=db_path, session_secret=session_secret)
    settings.validate()
    if auto_migrate: migrate(settings.database_path)
    def _application(environ, start_response):
        method, path = environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "/")
        if path == "/health/live" and method == "GET":
            return _json_response(start_response, "200 OK", {"status": "live"})
        if path == "/health/ready" and method == "GET":
            try:
                settings.validate()
                db = connect(settings.database_path)
                db.execute("SELECT 1").fetchone()
                verify_database_ready(db, manifest_path=settings.migration_manifest_path)
                db.close()
                return _json_response(start_response, "200 OK", {"status": "ready"})
            except Exception:
                return _json_response(start_response, "503 Service Unavailable", {"status": "not_ready"})
        db = connect(settings.database_path)
        try:
            user = current_user(environ, db, settings)
            if path == "/login":
                if method == "POST":
                    form = _formdata(environ)
                    candidate = db.execute("SELECT * FROM users WHERE email=?", (form.get("email", "").strip().lower(),)).fetchone()
                    valid = bool(candidate and candidate["status"] == "active" and candidate["password_hash"] and verify_password(form.get("password", ""), candidate["password_hash"]))
                    if not valid:
                        return _response(start_response, "401 Unauthorized", _html("<h1>Login failed</h1><p>Invalid credentials.</p><a href='/login'>Try again</a>"))
                    audit(db, candidate["id"], "login_success", "user", candidate["id"], ip=environ.get("REMOTE_ADDR")); db.commit()
                    token = sign_session(candidate["id"], settings.session_secret)
                    cookie = f"session={token}; HttpOnly; Path=/; SameSite=Lax" + ("; Secure" if settings.cookie_secure else "")
                    return _response(start_response, "303 See Other", "", [("Location", "/admin/resources"), ("Set-Cookie", cookie)])
                return _response(start_response, "200 OK", _html("<h1>Login</h1><form method='post'><label>Email<input name='email' type='email' required></label><label>Password<input name='password' type='password' required></label><button>Sign in</button></form>"))
            mqr=re.fullmatch(r"/qr/([A-Za-z0-9_\-]+)",path)
            if mqr and method=="GET":
                resource=_qr_resource(db,mqr.group(1))
                if not resource or (resource["archived_at"] and not is_admin(db,user["id"]) if user else resource is None):
                    return _response(start_response,"404 Not Found",_html("<h1>QR code not found</h1>"))
                return _response(start_response,"200 OK",_html(_qr_page(db,resource,user),"Resource QR"))
            mregen=re.fullmatch(r"/admin/resources/(\d+)/qr/regenerate",path)
            if mregen and method=="POST":
                if not user or not require_permission(db,user,"admin.qr.write"): return _response(start_response,"403 Forbidden",_html("<h1>Forbidden</h1>"))
                rid=int(mregen.group(1)); resource=db.execute("SELECT * FROM resources WHERE id=?",(rid,)).fetchone()
                if not resource or resource["archived_at"]: return _response(start_response,"404 Not Found",_html("<h1>Not found</h1>"))
                db.execute("BEGIN IMMEDIATE"); token=_new_qr(db,rid,user["id"],settings.qr_token_encryption_key,"qr.regenerate"); db.commit()
                url=resource_url(token)
                svg=svg_for_url(url).decode("utf-8")
                safe_svg=svg.replace("</script>","</scr\u0069pt>")
                return _response(start_response,"200 OK",_html(f"<h1>QR code generated</h1><p>Print this label and attach it to the resource.</p><div>{safe_svg}</div><p>The QR code contains only the secure resource URL.</p>"))
            msvg=re.fullmatch(r"/admin/resources/(\d+)/qr/svg",path)
            if msvg:
                # The print route is the only printable representation. Keeping one route
                # avoids divergent token handling and prevents raw-token responses.
                return _response(start_response,"404 Not Found",_html("<h1>Not found</h1>"))
            mprint=re.fullmatch(r"/admin/resources/(\d+)/qr/print",path)
            if mprint and method=="GET":
                if not user or not require_permission(db,user,"admin.qr.write"): return _response(start_response,"403 Forbidden",_html("<h1>Forbidden</h1>"))
                rid=int(mprint.group(1)); resource=db.execute("SELECT r.*,rt.name type_name,l.name location_name FROM resources r JOIN resource_types rt ON rt.id=r.resource_type_id LEFT JOIN locations l ON l.id=r.current_location_id WHERE r.id=?",(rid,)).fetchone()
                if not resource or resource["archived_at"]: return _response(start_response,"404 Not Found",_html("<h1>Not found</h1>"))
                db.execute("BEGIN IMMEDIATE")
                row=db.execute("SELECT * FROM qr_tokens WHERE resource_id=? AND invalidated_at IS NULL ORDER BY id DESC LIMIT 1",(rid,)).fetchone()
                if row:
                    token=decrypt_token(row["encrypted_token"],settings.qr_token_encryption_key)
                    if not token:
                        db.rollback()
                        return _response(start_response,"503 Service Unavailable",_html("<h1>QR code unavailable</h1><p>The configured QR encryption key cannot decrypt this token. Regenerate the QR code after key recovery.</p>"))
                else:
                    token=_new_qr(db,rid,user["id"],settings.qr_token_encryption_key,"qr.generate")
                audit(db,user["id"],"qr.print","resource",rid,reason="Administrator printed QR label")
                db.commit()
                url=resource_url(token); safe_svg=svg_for_url(url).decode("utf-8").replace("</script>","</scr\u0069pt>")
                return _response(start_response,"200 OK",_html(f"<h1>Printable resource label</h1><h2>{escape(resource['name'])}</h2><p>{escape(resource['type_name'])} — {escape(resource['location_name'] or '')}</p><div>{safe_svg}</div><style>@media print{{button{{display:none}}}}</style><button onclick='window.print()'>Print</button>"))
            mco=re.fullmatch(r"/resources/(\d+)/checkout",path)
            if mco and method in {"GET","POST"}:
                if not user:
                    return _response(start_response,"401 Unauthorized",_html("<h1>Sign in required</h1>"))
                rid=int(mco.group(1))
                resource=db.execute("SELECT * FROM resources WHERE id=?",(rid,)).fetchone()
                # Authorization is deliberately evaluated before booking or lifecycle
                # state checks, so out-of-scope managers receive 403 without learning
                # whether the resource is booked or checked out.
                if not resource:
                    return _response(start_response,"404 Not Found",_html("<h1>Not found</h1>"))
                manager_scope = can_manage_resource(db,user["id"],resource)
                authorized = is_admin(db,user["id"]) or ("resource_manager" in roles_for(db,user["id"]) and manager_scope) or ("resource_manager" not in roles_for(db,user["id"]) and user_can_use_resource(db,user["id"],resource))
                if not authorized:
                    return _response(start_response,"403 Forbidden",_html("<h1>Forbidden</h1>"))
                if method=="GET":
                    return _response(start_response,"200 OK",_html(_lifecycle_form(path,False)))
                form=_formdata(environ)
                try:
                    data=lifecycle_form(form)
                    # SQLite serializes this critical section. BEGIN IMMEDIATE is
                    # required so the active-checkout check and insert are atomic.
                    db.execute("BEGIN IMMEDIATE")
                    locked=False
                    active=db.execute("SELECT 1 FROM checkouts WHERE resource_id=? AND checked_in_at IS NULL LIMIT 1",(rid,)).fetchone()
                    if active:
                        raise ValueError("This resource is already checked out.")
                    resource=db.execute("SELECT * FROM resources WHERE id=?",(rid,)).fetchone()
                    if not resource or resource["status"] in {"archived","damaged","unavailable","under_maintenance","checked_out"} or not resource["is_bookable"]:
                        raise PermissionError("Resource cannot be checked out")
                    booking=db.execute("SELECT * FROM bookings WHERE resource_id=? AND owner_user_id=? AND status='confirmed' AND start_at_utc<=CURRENT_TIMESTAMP AND end_at_utc>=CURRENT_TIMESTAMP ORDER BY start_at_utc LIMIT 1",(rid,user["id"])).fetchone()
                    admin_override=is_admin(db,user["id"]) and not booking
                    if not booking and not admin_override:
                        raise ValueError("A confirmed active booking is required")
                    if admin_override and not data["notes"].strip():
                        raise ValueError("An administrator override reason is required")
                    cur=db.execute("INSERT INTO checkouts(resource_id,booking_id,user_id,condition_before,notes,is_admin_override,override_reason) VALUES (?,?,?,?,?,?,?)",(rid,booking["id"] if booking else None,user["id"],data["condition"],data["notes"],int(admin_override),data["notes"] if admin_override else None))
                    db.execute("UPDATE resources SET status='checked_out',updated_at=CURRENT_TIMESTAMP,updated_by=? WHERE id=?",(user["id"],rid))
                    audit(db,user["id"],'checkout.create','checkout',cur.lastrowid,after=data,reason=data["notes"] if admin_override else None)
                    db.commit()
                    return _response(start_response,"303 See Other","",[("Location",f"/resources/{rid}")])
                except ValueError as exc:
                    db.rollback(); return _response(start_response,"409 Conflict",_html(_lifecycle_form(path,False,str(exc))))
                except PermissionError as exc:
                    db.rollback(); return _response(start_response,"403 Forbidden",_html(_lifecycle_form(path,False,str(exc))))
                except sqlite3.IntegrityError as exc:
                    db.rollback()
                    # The partial unique index is the final protection against a
                    # duplicate active checkout. Translate only that integrity
                    # violation to a lifecycle conflict.
                    if "checkouts" in str(exc).lower() or "unique" in str(exc).lower():
                        return _response(start_response,"409 Conflict",_html(_lifecycle_form(path,False,"This resource is already checked out.")))
                    return _response(start_response,"500 Internal Server Error",_html("<h1>Checkout failed</h1>"))
                except sqlite3.OperationalError as exc:
                    db.rollback()
                    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                        return _response(start_response,"503 Service Unavailable",_html("<h1>Checkout temporarily unavailable</h1>"))
                    return _response(start_response,"500 Internal Server Error",_html("<h1>Checkout failed</h1>"))
                except RuntimeError as exc:
                    db.rollback(); return _response(start_response,"409 Conflict",_html(_lifecycle_form(path,False,"Checkout could not be completed.")))
                except Exception:
                    db.rollback(); return _response(start_response,"500 Internal Server Error",_html("<h1>Checkout failed</h1>"))
            mci=re.fullmatch(r"/resources/(\d+)/checkin",path)
            if mci and method in {"GET","POST"}:
                if not user: return _response(start_response,"401 Unauthorized",_html("<h1>Sign in required</h1>"))
                rid=int(mci.group(1)); resource=db.execute("SELECT * FROM resources WHERE id=?",(rid,)).fetchone()
                if not resource: return _response(start_response,"404 Not Found",_html("<h1>Not found</h1>"))
                if method=="GET": return _response(start_response,"200 OK",_html(_lifecycle_form(path,True)))
                form=_formdata(environ)
                try:
                    data=lifecycle_form(form,checkin=True); db.execute("BEGIN IMMEDIATE"); checkout=db.execute("SELECT * FROM checkouts WHERE resource_id=? AND checked_in_at IS NULL",(rid,)).fetchone()
                    if not checkout: raise ValueError("The resource is not checked out")
                    allowed=checkout["user_id"]==user["id"] or can_manage_resource(db,user["id"],resource) or is_admin(db,user["id"])
                    if not allowed: raise ValueError("You are not authorized to check in this resource")
                    damaged=data["condition"] in {"damaged","minor_issue","missing_accessory"} or bool(data["problems"] or data["missing_accessories"])
                    cur=db.execute("INSERT INTO checkins(checkout_id,resource_id,booking_id,user_id,condition_after,notes,missing_accessories,problems,damage_reported) VALUES (?,?,?,?,?,?,?,?,?)",(checkout["id"],rid,checkout["booking_id"],user["id"],data["condition"],data["notes"],data["missing_accessories"],data["problems"],int(damaged)))
                    db.execute("UPDATE checkouts SET checked_in_at=CURRENT_TIMESTAMP WHERE id=?",(checkout["id"],)); 
                    if checkout["booking_id"]:
                        db.execute("UPDATE bookings SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE id=?",(checkout["booking_id"],))
                    db.execute("UPDATE resources SET status=?,updated_at=CURRENT_TIMESTAMP,updated_by=? WHERE id=?",('damaged' if damaged else 'available',user["id"],rid))
                    audit(db,user["id"],'checkin.create','checkin',cur.lastrowid,after=data,reason='Condition issue reported' if damaged else None); db.commit()
                    return _response(start_response,"303 See Other","",[("Location",f"/resources/{rid}")])
                except Exception as exc:
                    db.rollback(); return _response(start_response,"409 Conflict",_html(_lifecycle_form(path,True,str(exc))))
            if path == "/resources" and method == "GET":
                rows=db.execute("SELECT r.*,rt.name type_name,l.name location_name FROM resources r JOIN resource_types rt ON rt.id=r.resource_type_id LEFT JOIN locations l ON l.id=r.current_location_id WHERE r.archived_at IS NULL AND r.status NOT IN ('archived') ORDER BY r.name").fetchall()
                body="<h1>Resources</h1><p><a href='/bookings'>My bookings</a> | <a href='/logout'>Logout</a></p><table><tr><th>Name</th><th>Type</th><th>Status</th><th>Location</th><th>Action</th></tr>"+"".join(f"<tr><td>{escape(r['name'])}</td><td>{escape(r['type_name'])}</td><td>{escape(r['status'])}</td><td>{escape(r['location_name'] or '')}</td><td><a href='/resources/{r['id']}'>Availability</a></td></tr>" for r in rows)+"</table>"
                return _response(start_response,"200 OK",_html(body))
            mres=re.fullmatch(r"/resources/(\d+)",path)
            if mres and method=="GET":
                resource=db.execute("SELECT r.*,rt.name type_name,l.name location_name FROM resources r JOIN resource_types rt ON rt.id=r.resource_type_id LEFT JOIN locations l ON l.id=r.current_location_id WHERE r.id=?",(int(mres.group(1)),)).fetchone()
                if not resource or resource['archived_at']: return _response(start_response,"404 Not Found",_html("<h1>Not found</h1>"))
                rows=_resource_availability(db,resource['id'])
                body=f"<h1>{escape(resource['name'])}</h1><p>Status: {escape(resource['status'])}; Location: {escape(resource['location_name'] or '')}</p><h2>Confirmed bookings</h2><ul>"+"".join(f"<li>{escape(local_display(r['start_at_utc']))} – {escape(local_display(r['end_at_utc']))}</li>" for r in rows if r['owner_user_id']==user['id'] or is_admin(db,user['id']) or can_manage_resource(db,user['id'],resource))+"</ul>"
                if user and user_can_use_resource(db,user['id'],resource):
                    body += f"<p><a href='/resources/{resource['id']}/book'>Book this resource</a></p>"
                    body += f"<p><a href='/resources/{resource['id']}/checkout'>Check out</a> | <a href='/resources/{resource['id']}/checkin'>Check in</a></p>"
                body += "<p><a href='/resources'>Back</a></p>"
                return _response(start_response,"200 OK",_html(body))
            mbook=re.fullmatch(r"/resources/(\d+)/book",path)
            if mbook:
                resource=db.execute("SELECT * FROM resources WHERE id=?",(int(mbook.group(1)),)).fetchone()
                if not resource or not user_can_use_resource(db,user['id'],resource): return _response(start_response,"403 Forbidden",_html("<h1>Resource cannot be booked</h1><p>The resource is not available for booking.</p>"))
                if method=="GET": return _response(start_response,"200 OK",_html(_booking_form(db,path,resource)))
                try:
                    data=booking_form(_formdata(environ)); rules=db.execute("SELECT * FROM booking_rules WHERE id=1").fetchone(); duration=(datetime.fromisoformat(data['end_at_utc'])-datetime.fromisoformat(data['start_at_utc'])).total_seconds()/60
                    if duration<rules['minimum_duration_minutes'] or duration>rules['maximum_duration_minutes']: raise ValueError("The booking duration is outside the configured limits")
                    if datetime.fromisoformat(data['start_at_utc']) > datetime.now(timezone.utc)+timedelta(days=rules['advance_booking_days']): raise ValueError("The booking exceeds the advance-booking limit")
                    db.execute("BEGIN IMMEDIATE")
                    conflict=db.execute("SELECT * FROM bookings WHERE resource_id=? AND status='confirmed' AND end_at_utc>? AND start_at_utc<?",(resource['id'],data['start_at_utc'],data['end_at_utc'])).fetchone()
                    if conflict: raise ValueError(f"The resource is already booked from {local_display(conflict['start_at_utc'])} to {local_display(conflict['end_at_utc'])}.")
                    cur=db.execute("INSERT INTO bookings(resource_id,owner_user_id,start_at_utc,end_at_utc,purpose,project_reference,destination,created_by) VALUES (?,?,?,?,?,?,?,?)",(resource['id'],user['id'],data['start_at_utc'],data['end_at_utc'],data['purpose'],data['project_reference'],data['destination'],user['id']))
                    bid=cur.lastrowid; audit(db,user['id'],'booking.create','booking',bid,after=data); db.commit()
                    return _response(start_response,"303 See Other","",[("Location",f"/bookings/{bid}")])
                except Exception as exc:
                    db.rollback(); return _response(start_response,"409 Conflict",_html(_booking_form(db,path,resource,str(exc))))
            if path=="/bookings" and method=="GET":
                return _response(start_response,"200 OK",_html(_booking_list(db,user,is_admin(db,user['id']))))
            mb=re.fullmatch(r"/bookings/(\d+)",path)
            if mb and method=="GET":
                b=_booking_row(db,int(mb.group(1)))
                if not b or not can_view_booking(db,user['id'],b): return _response(start_response,"404 Not Found",_html("<h1>Not found</h1>"))
                return _response(start_response,"200 OK",_html(f"<h1>Booking confirmed</h1><p>{_booking_text(b)}</p><p>Purpose: {escape(b['purpose'])}</p><p><a href='/bookings'>Bookings</a></p>"))
            mc=re.fullmatch(r"/bookings/(\d+)/cancel",path)
            if mc and method=="POST":
                b=_booking_row(db,int(mc.group(1)))
                if not b or not can_view_booking(db,user['id'],b): return _response(start_response,"404 Not Found",_html("<h1>Not found</h1>"))
                if b['status']!='confirmed': return _response(start_response,"409 Conflict",_html("<h1>Booking cannot be cancelled</h1>"))
                db.execute("UPDATE bookings SET status='cancelled',cancelled_at=CURRENT_TIMESTAMP,cancellation_reason=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",("Cancelled by user",b['id']))
                audit(db,user['id'],'booking.cancel','booking',b['id'],before=dict(b),reason="Cancelled by user"); db.commit()
                return _response(start_response,"303 See Other","",[("Location","/bookings")])
            if path == "/logout":
                if user: audit(db, user["id"], "logout", "user", user["id"]); db.commit()
                return _response(start_response, "303 See Other", "", [("Location", "/login"), ("Set-Cookie", "session=; Max-Age=0; HttpOnly; Path=/; SameSite=Lax")])
            if not user: return _response(start_response, "303 See Other", "", [("Location", "/login")])
            if path == "/admin/resources" and method == "GET":
                if not require_permission(db, user, "admin.resources.read"): return _response(start_response, "403 Forbidden", _html("<h1>Forbidden</h1>"))
                return _response(start_response, "200 OK", _html(_admin_page(db, user)))
            if path == "/admin/resources/new":
                if not require_permission(db, user, "admin.resources.write"): return _response(start_response, "403 Forbidden", _html("<h1>Forbidden</h1>"))
                if method == "GET": return _response(start_response, "200 OK", _html(_resource_form(db, path)))
                if method == "POST":
                    try:
                        data = clean_resource(_formdata(environ)); typ = db.execute("SELECT id FROM resource_types WHERE id=? AND archived_at IS NULL", (data["resource_type_id"],)).fetchone()
                        loc = db.execute("SELECT id FROM locations WHERE archived_at IS NULL ORDER BY id LIMIT 1").fetchone()
                        if not typ or not loc: raise ValueError("Invalid resource configuration")
                        cur = db.execute("INSERT INTO resources(resource_type_id,name,description,asset_number,current_location_id,created_by,updated_by) VALUES (?,?,?,?,?,?,?)", (data["resource_type_id"], data["name"], data["description"], data["asset_number"], loc["id"], user["id"], user["id"]))
                        rid = cur.lastrowid; audit(db, user["id"], "resource.create", "resource", rid, after=data); db.commit()
                        return _response(start_response, "303 See Other", "", [("Location", "/admin/resources")])
                    except Exception:
                        db.rollback(); return _response(start_response, "400 Bad Request", _html(_resource_form(db, path, error="Resource could not be saved.")))
            match = re.fullmatch(r"/admin/resources/(\d+)/edit", path)
            if match:
                resource = db.execute("SELECT * FROM resources WHERE id=?", (int(match.group(1)),)).fetchone()
                if not resource or not can_manage_resource(db, user["id"], resource): return _response(start_response, "404 Not Found", _html("<h1>Not found</h1>"))
                if method == "GET": return _response(start_response, "200 OK", _html(_resource_form(db, path, resource)))
                if method == "POST":
                    try:
                        data = clean_resource(_formdata(environ)); before = dict(resource)
                        typ = db.execute("SELECT id FROM resource_types WHERE id=? AND archived_at IS NULL", (data["resource_type_id"],)).fetchone()
                        if not typ: raise ValueError("Invalid resource type")
                        db.execute("UPDATE resources SET resource_type_id=?,name=?,description=?,asset_number=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (data["resource_type_id"], data["name"], data["description"], data["asset_number"], user["id"], resource["id"]))
                        audit(db, user["id"], "resource.update", "resource", resource["id"], before=before, after=data); db.commit()
                        return _response(start_response, "303 See Other", "", [("Location", "/admin/resources")])
                    except Exception:
                        db.rollback(); return _response(start_response, "400 Bad Request", _html(_resource_form(db, path, resource, "Resource could not be saved.")))
            return _response(start_response, "404 Not Found", _html("<h1>Not found</h1>"))
        finally:
            db.close()
    logger = logging.getLogger("resource_platform")
    def application(environ, start_response):
        request_id = environ.get("HTTP_X_REQUEST_ID") or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request_id):
            request_id = uuid.uuid4().hex
        environ["resource_platform.request_id"] = request_id
        started = time.monotonic(); result = []
        def tracked_start(status, headers, exc_info=None):
            result.append(status)
            headers = list(headers) + [("X-Request-ID", request_id)]
            if exc_info is None:
                return start_response(status, headers)
            return start_response(status, headers, exc_info)
        try:
            response = _application(environ, tracked_start)
            status_code = int(result[0].split()[0]) if result else 500
            logger.info(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "level":"INFO", "request_id":request_id, "method":environ.get("REQUEST_METHOD",""), "route":environ.get("PATH_INFO",""), "status_code":status_code, "duration_ms":round((time.monotonic()-started)*1000,2), "environment":settings.environment, "error_category":None}, separators=(",",":")))
            return response
        except Exception:
            logger.error(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "level":"ERROR", "request_id":request_id, "method":environ.get("REQUEST_METHOD",""), "route":environ.get("PATH_INFO",""), "status_code":500, "duration_ms":round((time.monotonic()-started)*1000,2), "environment":settings.environment, "error_category":"internal_error"}, separators=(",",":")))
            return _json_response(tracked_start, "500 Internal Server Error", {"error":"Internal server error", "request_id":request_id})
    return application
