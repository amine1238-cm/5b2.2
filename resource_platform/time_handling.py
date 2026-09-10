"""Shared timezone boundary for repository consumers."""
from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

def parse_local_datetime(value: str, *, timezone_name: str = "Europe/Berlin") -> datetime:
    """Parse a local ISO minute value and return an aware UTC datetime.

    Nonexistent and ambiguous DST times are rejected conservatively.
    """
    local = datetime.fromisoformat(value)
    if local.tzinfo is not None:
        return local.astimezone(timezone.utc)
    zone = ZoneInfo(timezone_name)
    valid = []
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        roundtrip = aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if roundtrip == local and all(aware.utcoffset() != other.utcoffset() for other in valid):
            valid.append(aware)
    if len(valid) != 1:
        raise ValueError("Local time is nonexistent or ambiguous")
    return valid[0].astimezone(timezone.utc)

def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("A timezone-aware datetime is required")
    return value.astimezone(timezone.utc)

def display_berlin(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BERLIN)

def sqlite_timestamp(value: datetime) -> str:
    return to_utc(value).isoformat()
