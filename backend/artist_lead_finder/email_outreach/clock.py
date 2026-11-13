"""Time helpers of the email channel: UTC from the database, hours in a time zone."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# When the computer has no time zone database (Windows), whole-hour offsets are close enough.
ZONE_OFFSETS = {
    "America/New_York": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Los_Angeles": -8,
    "Europe/London": 0,
    "Europe/Berlin": 1,
    "Europe/Moscow": 3,
}


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def local_hour(zone: str, now: datetime) -> int:
    try:
        return now.astimezone(ZoneInfo(zone)).hour
    except (ZoneInfoNotFoundError, ValueError):
        return (now + timedelta(hours=ZONE_OFFSETS.get(zone, 0))).hour


def in_window(zone: str, start: int, end: int, now: datetime) -> bool:
    return start <= local_hour(zone, now) < end


def iso(value: datetime | None) -> str | None:
    value = as_utc(value)
    return value.isoformat() if value else None
