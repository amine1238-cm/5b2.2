def clean_resource(form):
    name = form.get("name", "").strip()
    description = form.get("description", "").strip()
    asset = form.get("asset_number", "").strip() or None
    if not name or len(name) > 160: raise ValueError("Name is required and must be 160 characters or fewer")
    if len(description) > 2000: raise ValueError("Description is too long")
    if asset and len(asset) > 80: raise ValueError("Asset number is too long")
    try: resource_type_id = int(form.get("resource_type_id", "0"))
    except ValueError: raise ValueError("Invalid resource type")
    if resource_type_id <= 0: raise ValueError("Invalid resource type")
    return {"name": name, "description": description, "asset_number": asset, "resource_type_id": resource_type_id}

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BERLIN=ZoneInfo("Europe/Berlin")

def parse_local_datetime(value):
    if not isinstance(value,str): raise ValueError("Invalid date and time")
    try: local=datetime.strptime(value,"%Y-%m-%dT%H:%M")
    except ValueError as exc: raise ValueError("Invalid date and time") from exc
    # Reject nonexistent and ambiguous local times rather than silently shifting them.
    candidates=[]
    for fold in (0,1):
        aware=local.replace(tzinfo=BERLIN,fold=fold)
        back=aware.astimezone(timezone.utc).astimezone(BERLIN).replace(tzinfo=None)
        if back==local and all(aware.utcoffset()!=existing.utcoffset() for existing in candidates):
            candidates.append(aware)
    if len(candidates)!=1: raise ValueError("The selected time is ambiguous or does not exist in Europe/Berlin")
    return candidates[0].astimezone(timezone.utc).isoformat(timespec="seconds")

def booking_form(form, now=None):
    start=parse_local_datetime(form.get("start_at","")); end=parse_local_datetime(form.get("end_at",""))
    if start>=end: raise ValueError("Start time must be before end time")
    now=now or datetime.now(timezone.utc)
    start_dt=datetime.fromisoformat(start); end_dt=datetime.fromisoformat(end)
    if start_dt < now: raise ValueError("Bookings in the past are not allowed")
    purpose=form.get("purpose","").strip(); project=form.get("project_reference","").strip() or None; destination=form.get("destination","").strip() or None
    if not purpose or len(purpose)>2000: raise ValueError("Purpose is required and must be 2000 characters or fewer")
    return {"start_at_utc":start,"end_at_utc":end,"purpose":purpose,"project_reference":project,"destination":destination}

def local_display(utc_value):
    return datetime.fromisoformat(utc_value).astimezone(BERLIN).strftime("%d.%m.%Y, %H:%M")


VALID_CONDITIONS = {"good", "minor_issue", "damaged", "missing_accessory"}

def lifecycle_form(form, *, checkin=False):
    condition = form.get("condition_after" if checkin else "condition_before", "").strip().lower()
    if condition not in VALID_CONDITIONS:
        raise ValueError("A valid condition is required")
    return {
        "condition": condition,
        "notes": form.get("notes", "").strip()[:2000],
        "missing_accessories": form.get("missing_accessories", "").strip()[:1000],
        "problems": form.get("problems", "").strip()[:2000],
    }
