"""Small, dependency-free parsing for reminder times and recurrences (local time)."""

from __future__ import annotations

import re
import calendar
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")
_CLOCK_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.?|p\.m\.?)?")
_WORD_TIMES = {"noon": (12, 0), "midnight": (0, 0), "morning": (9, 0), "afternoon": (15, 0),
               "evening": (19, 0), "tonight": (20, 0)}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

RECURRENCES = ("", "hourly", "daily", "weekdays", "weekly", "monthly")


class TimeParseError(ValueError):
    pass


def parse_duration(text: str) -> float | None:
    text = text.lower().strip().replace(" and ", " ").replace(",", " ")
    if _DURATION_RE.sub("", text).strip():
        return None
    total = 0.0
    matched = False
    for amount, unit in _DURATION_RE.findall(text):
        if unit not in _UNITS:
            return None
        total += float(amount) * _UNITS[unit]
        matched = True
    return total if matched and math.isfinite(total) else None


def zone(name: str = "") -> ZoneInfo | None:
    """An explicit user zone, or the operating system's local time when unset."""
    return ZoneInfo(name) if name else None


def _valid_wall_time(value: datetime) -> datetime:
    # A nonexistent spring-forward wall time must not silently become a different appointment.
    normalized = datetime.fromtimestamp(value.timestamp(), value.tzinfo)
    if normalized.replace(tzinfo=None) != value.replace(tzinfo=None):
        raise TimeParseError("That local time does not exist because the clocks change. Choose another time.")
    return value


def _upcoming_fold(value: datetime, now: datetime) -> datetime:
    """During the repeated autumn hour, use its second occurrence if the first has already passed."""
    later = value.replace(fold=1)
    return later if value.timestamp() <= now.timestamp() < later.timestamp() else value


def parse_when(text: str, now: datetime | None = None, timezone: str = "") -> datetime:
    """Parse a reminder in the user's zone; relative durations measure elapsed time."""
    now = now or datetime.now(zone(timezone))
    if not isinstance(text, str):
        raise TimeParseError("Reminder time must be text.")
    raw = (text or "").strip()
    if not raw:
        raise TimeParseError("Give a time like 'in 20 minutes', 'tomorrow 9am', or '2026-10-04T15:30'.")
    lowered = raw.lower().strip().rstrip(".")

    iso = _parse_iso(raw, now.tzinfo)
    if iso is not None:
        return iso

    if lowered in ("now", "right now"):
        return datetime.fromtimestamp(now.timestamp() + 5, now.tzinfo)

    if lowered.startswith("in "):
        seconds = parse_duration(lowered[3:].replace(" and ", " ").replace(",", " "))
        if seconds is None or seconds <= 0:
            raise TimeParseError(f"Couldn't understand the duration in '{raw}'.")
        try:
            return datetime.fromtimestamp(now.timestamp() + seconds, now.tzinfo)
        except (OverflowError, OSError, ValueError) as exc:
            raise TimeParseError("That duration is too large.") from exc

    day_offset = 0
    rest = lowered
    if re.match(r"^tomorrow\b", rest):
        day_offset, rest = 1, rest[len("tomorrow"):]
    elif re.match(r"^today\b", rest):
        rest = rest[len("today"):]
    weekday_target = None
    next_week = False
    for index, name in enumerate(_WEEKDAYS):
        for prefix in (f"next {name}", name):
            if re.match(rf"^{prefix}\b", rest.strip()):
                weekday_target = index
                next_week = prefix.startswith("next ")
                rest = rest.strip()[len(prefix):]
                break
        if weekday_target is not None:
            break
    rest = rest.replace(" at ", " ").strip()
    if rest.startswith("at "):
        rest = rest[3:]

    hour, minute = None, 0
    for word, (h, m) in _WORD_TIMES.items():
        if word == rest:
            hour, minute = h, m
            break
    if hour is None:
        match = _CLOCK_RE.fullmatch(rest)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            meridiem = (match.group(3) or "").replace(".", "")
            if meridiem and not 1 <= hour <= 12:
                raise TimeParseError(f"'{raw}' is not a valid time of day.")
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            if hour > 23 or minute > 59:
                raise TimeParseError(f"'{raw}' is not a valid time of day.")
    if hour is None:
        if not rest and (day_offset or weekday_target is not None):
            hour, minute = 9, 0
        else:
            raise TimeParseError(f"Couldn't understand '{raw}'. Try 'in 20 minutes', 'tomorrow 9am' or an ISO time.")

    target = _upcoming_fold(now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                            + timedelta(days=day_offset), now)
    if weekday_target is not None:
        delta = (weekday_target - now.weekday()) % 7
        if delta == 0 and (target.timestamp() <= now.timestamp() or next_week):
            delta = 7
        target = _upcoming_fold((now + timedelta(days=delta)).replace(hour=hour, minute=minute, second=0, microsecond=0), now)
    elif day_offset == 0 and target.timestamp() <= now.timestamp() and not lowered.startswith("today"):
        target += timedelta(days=1)
    return _valid_wall_time(target)


def _parse_iso(raw: str, tz=None) -> datetime | None:
    candidate = raw.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            value = datetime.fromisoformat(candidate) if fmt is None else datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        if value.tzinfo is not None:
            return value.astimezone(tz) if tz else value.astimezone().replace(tzinfo=None)
        return _valid_wall_time(value.replace(tzinfo=tz))
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


def next_occurrence(due_at: float, recurrence: str, now: float, timezone: str = "",
                    anchor_at: float | None = None) -> float | None:
    if not recurrence:
        return None
    tz = zone(timezone)
    current = datetime.fromtimestamp(due_at, tz)
    anchor = datetime.fromtimestamp(anchor_at if anchor_at is not None else due_at, tz)
    now_dt = datetime.fromtimestamp(now, tz)
    interval = 3600 if recurrence == "hourly" else None
    if recurrence.startswith("every ") and recurrence.endswith("s"):
        interval = int(recurrence[6:-1])
    if interval is not None:
        if interval <= 0:
            return None
        jumps = max(1, math.floor((now - due_at) / interval) + 1)
        return due_at + jumps * interval
    month_day = anchor.day
    if recurrence in ("daily", "weekly", "weekdays"):
        step = 7 if recurrence == "weekly" else 1
        days = max(step, ((now_dt.date() - current.date()).days // step) * step)
        current += timedelta(days=days - step)
    for _ in range(10000):
        if recurrence == "daily":
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
            day = min(month_day, calendar.monthrange(year, month)[1])
            current = current.replace(year=year, month=month, day=day)
        else:
            return None
        current = current.replace(hour=anchor.hour, minute=anchor.minute, second=anchor.second,
                                  microsecond=anchor.microsecond, fold=anchor.fold)
        # timestamp normalizes nonexistent recurring wall times forward; the anchor restores the requested
        # clock/day on the following occurrence (March after February, or the day after a DST gap).
        candidate = current.timestamp()
        if candidate > max(now, due_at):
            return candidate
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
