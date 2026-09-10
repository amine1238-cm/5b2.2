ROLE_PERMISSIONS = {
    "employee": {"resource.read", "booking.create", "checkout.create", "checkin.create"},
    "resource_manager": {"resource.read", "booking.create", "checkout.create", "checkin.create", "admin.resources.read", "admin.resources.write"},
    "administrator": {"resource.read", "booking.create", "checkout.create", "checkin.create", "admin.resources.read", "admin.resources.write", "admin.users.read", "admin.users.write", "admin.audit.read", "admin.qr.write", "admin.lifecycle.read"},
    "super_administrator": {"resource.read", "booking.create", "checkout.create", "checkin.create", "admin.resources.read", "admin.resources.write", "admin.users.read", "admin.users.write", "admin.audit.read", "admin.qr.write", "admin.lifecycle.read", "security.manage"},
}

def _valid_user_id(user_id):
    return isinstance(user_id, int) and user_id > 0

def roles_for(db, user_id):
    if not _valid_user_id(user_id):
        return []
    return [r[0] for r in db.execute(
        "SELECT r.name FROM roles r JOIN user_roles ur ON ur.role_id=r.id WHERE ur.user_id=?",
        (user_id,),
    )]

def has_permission(db, user_id, permission):
    if not isinstance(permission, str) or not permission:
        return False
    return any(permission in ROLE_PERMISSIONS.get(role, set()) for role in roles_for(db, user_id))

def is_admin(db, user_id):
    return any(role in {"administrator", "super_administrator"} for role in roles_for(db, user_id))

def can_manage_resource(db, user_id, resource):
    """Return whether user may manage resource; malformed authorization input denies access."""
    if not _valid_user_id(user_id) or resource is None:
        return False
    try:
        ownership = resource["responsible_manager_id"]
    except (KeyError, TypeError, IndexError):
        return False
    if not is_admin(db, user_id) and "resource_manager" not in roles_for(db, user_id):
        return False
    if is_admin(db, user_id):
        return True
    return ownership == user_id


def user_can_use_resource(db, user_id, resource):
    if not _valid_user_id(user_id) or resource is None: return False
    try: rid=resource["id"]; status=resource["status"]; bookable=resource["is_bookable"]
    except (KeyError,TypeError,IndexError): return False
    if status in {"archived","damaged","unavailable","under_maintenance"} or not bookable: return False
    return bool(db.execute("SELECT 1 FROM users WHERE id=? AND status='active'",(user_id,)).fetchone())

def can_view_booking(db, user_id, booking):
    if not _valid_user_id(user_id) or booking is None: return False
    if is_admin(db,user_id): return True
    try: owner=booking["owner_user_id"]; resource_id=booking["resource_id"]
    except (KeyError,TypeError,IndexError): return False
    if owner == user_id: return True
    resource=db.execute("SELECT * FROM resources WHERE id=?",(resource_id,)).fetchone()
    return can_manage_resource(db,user_id,resource)


def can_view_resource_lifecycle(db, user_id, resource):
    if not _valid_user_id(user_id) or resource is None:
        return False
    if is_admin(db, user_id): return True
    return can_manage_resource(db, user_id, resource)
