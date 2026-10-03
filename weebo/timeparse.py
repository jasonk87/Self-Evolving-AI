"""Small, dependency-free parsing for reminder times and recurrences (local time)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")
_CLOCK_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b")
_WORD_TIMES = {"noon": (12, 0), "midnight": (0, 0), "morning": (9, 0), "afternoon": (15, 0),
               "evening": (19, 0), "tonight": (20, 0)}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

RECURRENCES = ("", "hourly", "daily", "weekdays", "weekly", "monthly")


class TimeParseError(ValueError):
    pass


def parse_duration(text: str) -> float | None:
    text = text.lower().strip()
    total = 0.0
    matched = False
    for amount, unit in _DURATION_RE.findall(text):
        if unit not in _UNITS:
            return None
        total += float(amount) * _UNITS[unit]
        matched = True
    return total if matched else None


def parse_when(text: str, now: datetime | None = None) -> datetime:
    """Parse ``text`` into a local naive datetime in the future."""
    now = now or datetime.now()
    raw = (text or "").strip()
    if not raw:
        raise TimeParseError("Give a time like 'in 20 minutes', 'tomorrow 9am', or '2026-10-04T15:30'.")
    lowered = raw.lower().strip().rstrip(".")

    iso = _parse_iso(raw)
    if iso is not None:
        return iso

    if lowered in ("now", "right now"):
        return now + timedelta(seconds=5)

    if lowered.startswith("in "):
        seconds = parse_duration(lowered[3:].replace(" and ", " ").replace(",", " "))
        if seconds is None:
            raise TimeParseError(f"Couldn't understand the duration in '{raw}'.")
        return now + timedelta(seconds=seconds)

    day_offset = 0
    rest = lowered
    if rest.startswith("tomorrow"):
        day_offset, rest = 1, rest[len("tomorrow"):]
    elif rest.startswith("today"):
        rest = rest[len("today"):]
    weekday_target = None
    for index, name in enumerate(_WEEKDAYS):
        for prefix in (f"next {name}", name):
            if rest.strip().startswith(prefix):
                weekday_target = index
                rest = rest.strip()[len(prefix):]
                break
        if weekday_target is not None:
            break
    rest = rest.replace(" at ", " ").strip()
    if rest.startswith("at "):
        rest = rest[3:]

    hour, minute = None, 0
    for word, (h, m) in _WORD_TIMES.items():
        if word in rest:
            hour, minute = h, m
            break
    if hour is None:
        match = _CLOCK_RE.search(rest)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            meridiem = (match.group(3) or "").replace(".", "")
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            if hour > 23 or minute > 59:
                raise TimeParseError(f"'{raw}' is not a valid time of day.")
    if hour is None:
        if day_offset or weekday_target is not None:
            hour, minute = 9, 0
        else:
            raise TimeParseError(f"Couldn't understand '{raw}'. Try 'in 20 minutes', 'tomorrow 9am' or an ISO time.")

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=day_offset)
    if weekday_target is not None:
        delta = (weekday_target - now.weekday()) % 7
        if delta == 0 and target <= now:
            delta = 7
        target = (now + timedelta(days=delta)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif day_offset == 0 and target <= now:
        target += timedelta(days=1)
    return target


def _parse_iso(raw: str) -> datetime | None:
    candidate = raw.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            value = datetime.fromisoformat(candidate) if fmt is None else datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        if value.tzinfo is not None:
            value = value.astimezone().replace(tzinfo=None)
        return value
    return None


def normalize_recurrence(text: str) -> str:
    value = (text or "").strip().lower()
    if value in ("", "none", "once", "no"):
        return ""
    if value in ("every day", "everyday"):
        return "daily"
    if value in ("every week",):
        return "weekly"
    if value in ("every hour",):
        return "hourly"
    if value in ("every month",):
        return "monthly"
    if value in ("weekday", "every weekday", "workdays"):
        return "weekdays"
    if value in RECURRENCES:
        return value
    if value.startswith("every "):
        seconds = parse_duration(value[6:])
        if seconds and seconds >= 60:
            return f"every {int(seconds)}s"
    raise TimeParseError(
        f"Unknown recurrence '{text}'. Use hourly, daily, weekdays, weekly, monthly or 'every 30 minutes'."
    )


def next_occurrence(due_at: float, recurrence: str, now: float) -> float | None:
    if not recurrence:
        return None
    current = datetime.fromtimestamp(due_at)
    now_dt = datetime.fromtimestamp(now)
    for _ in range(10000):
        if recurrence == "hourly":
            current += timedelta(hours=1)
        elif recurrence == "daily":
            current += timedelta(days=1)
        elif recurrence == "weekly":
            current += timedelta(weeks=1)
        elif recurrence == "weekdays":
            current += timedelta(days=1)
            while current.weekday() >= 5:
                current += timedelta(days=1)
        elif recurrence == "monthly":
            month = current.month + 1
            year = current.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            day = min(current.day, 28)
            current = current.replace(year=year, month=month, day=day)
        elif recurrence.startswith("every ") and recurrence.endswith("s"):
            current += timedelta(seconds=int(recurrence[6:-1]))
        else:
            return None
        if current > now_dt:
            return current.timestamp()
    return None


def describe_recurrence(recurrence: str) -> str:
    if recurrence.startswith("every ") and recurrence.endswith("s"):
        seconds = int(recurrence[6:-1])
        for unit, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
            if seconds % size == 0:
                count = seconds // size
                return f"every {count} {unit}{'s' if count != 1 else ''}"
        return f"every {seconds} seconds"
    return recurrence
