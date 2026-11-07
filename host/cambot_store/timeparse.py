"""Query bounds: RFC 3339, local HH:MM in the camera time zone, or -30m."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from host.cambot_store.format import utc

_RELATIVE = re.compile(r"^-(\d+)([smhd])$")
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def parse_bound(value: str, now: datetime, tz: ZoneInfo) -> datetime:
    text = value.strip()
    relative = _RELATIVE.match(text)
    if relative:
        count = int(relative.group(1))
        unit = relative.group(2)
        delta = {
            "s": timedelta(seconds=count),
            "m": timedelta(minutes=count),
            "h": timedelta(hours=count),
            "d": timedelta(days=count),
        }[unit]
        return utc(now - delta)
    clock = _CLOCK.match(text)
    if clock:
        local_now = utc(now).astimezone(tz)
        second = int(clock.group(3) or 0)
        local = local_now.replace(
            hour=int(clock.group(1)),
            minute=int(clock.group(2)),
            second=second,
            microsecond=0,
        )
        return utc(local)
    normalized = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return utc(parsed)


def parse_range(start: str, end: str, now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return parse_bound(start, now, tz), parse_bound(end, now, tz)
