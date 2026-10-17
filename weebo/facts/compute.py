"""Deterministic schedule mathematics. All stored instants are UTC epoch seconds.

Intervals are half-open [start, end), including all-day calendar events. A local
time in a DST gap is rejected; ambiguous local times require an explicit fold.
Travel durations come from a source, never from the language model.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


def instant(value: float | str | datetime, tz: str = "UTC", *, fold: int | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError("An instant must not be a boolean")
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(parsed, datetime):
            raise ValueError("Expected an ISO timestamp or epoch seconds")
        if parsed.tzinfo is None:
            zone = ZoneInfo(tz)
            candidates = []
            for choice in (0, 1):
                aware = parsed.replace(tzinfo=zone, fold=choice)
                if datetime.fromtimestamp(aware.timestamp(), zone).replace(tzinfo=None) == parsed:
                    candidates.append(aware)
            if not candidates:
                raise ValueError("Local time does not exist because the clock moved forward")
            if len({a.timestamp() for a in candidates}) > 1 and fold is None:
                raise ValueError("Local time is ambiguous; specify an offset or fold")
            if fold not in (None, 0, 1):
                raise ValueError("fold must be 0 or 1")
            parsed = next((a for a in candidates if a.fold == fold), candidates[0])
        result = parsed.timestamp()
    if not math.isfinite(result):
        raise ValueError("Timestamp must be finite")
    return result


def local_day(day: date | str, tz: str) -> tuple[float, float]:
    day = date.fromisoformat(day) if isinstance(day, str) else day
    # Some zones advance at midnight, and a date-line change can skip a whole
    # date. fold=0 maps a missing midnight forward to its first real instant.
    zone = ZoneInfo(tz)
    return (datetime.combine(day, time.min, tzinfo=zone).timestamp(),
            datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).timestamp())


def display_time(timestamp: float, tz: str) -> str:
    return datetime.fromtimestamp(instant(timestamp), ZoneInfo(tz)).isoformat()


def overlaps(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"] and a["start"] < a["end"] and b["start"] < b["end"]


def conflicts(events: list[dict]) -> list[dict[str, str]]:
    active: list[dict] = []
    found = []
    for event in sorted((e for e in events if e.get("status") != "cancelled"), key=lambda e: e["start"]):
        active = [e for e in active if e["end"] > event["start"]]
        found.extend({"first": e["id"], "second": event["id"]} for e in active if overlaps(e, event))
        active.append(event)
    return found


def travel_window(previous: dict, following: dict, *, travel_seconds: float | None = None,
                  source: str = "", buffer_seconds: float = 0) -> dict[str, Any]:
    gap = instant(following["start"]) - instant(previous["end"])
    if buffer_seconds < 0 or not math.isfinite(buffer_seconds):
        raise ValueError("Travel buffer must be finite and non-negative")
    if travel_seconds is not None and (travel_seconds < 0 or not math.isfinite(travel_seconds) or not source):
        raise ValueError("Travel time requires a source and a finite, non-negative duration")
    required = travel_seconds + buffer_seconds if travel_seconds is not None else None
    return {"from": previous["id"], "to": following["id"], "gap_seconds": gap,
            "travel_seconds": travel_seconds, "source": source if travel_seconds is not None else None,
            "leave_by": following["start"] - required if required is not None else None,
            "shortfall_seconds": max(0, required - gap) if required is not None else None,
            "back_to_back": gap == 0, "overlap": gap < 0,
            "travel_status": "known" if travel_seconds is not None else "unknown"}


def back_to_backs(events: list[dict], buffer_seconds: float = 0) -> list[dict]:
    if buffer_seconds < 0 or not math.isfinite(buffer_seconds):
        raise ValueError("Buffer must be finite and non-negative")
    ordered = sorted((e for e in events if e.get("status") != "cancelled"), key=lambda e: e["start"])
    return [travel_window(a, b, buffer_seconds=buffer_seconds) for i, a in enumerate(ordered) for b in ordered[i+1:]
            if 0 <= b["start"] - a["end"] <= buffer_seconds]


def timezone_difference(timestamp: float, origin_tz: str, destination_tz: str) -> float:
    dt = datetime.fromtimestamp(instant(timestamp), timezone.utc)
    return (dt.astimezone(ZoneInfo(destination_tz)).utcoffset()
            - dt.astimezone(ZoneInfo(origin_tz)).utcoffset()).total_seconds()


def due_items(commitments: list[dict], now: float, *, horizon_seconds: float = 86400) -> list[dict]:
    now = instant(now)
    if horizon_seconds < 0 or not math.isfinite(horizon_seconds):
        raise ValueError("Horizon must be finite and non-negative")
    return sorted((c for c in commitments if c.get("status") == "open" and c.get("due") is not None
                   and instant(c["due"]) <= now + horizon_seconds), key=lambda c: (c["due"], c["id"]))
