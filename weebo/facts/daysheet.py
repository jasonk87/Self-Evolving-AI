"""The day sheet: every upcoming event, enriched by code from the facts store and real sources.

For each event in the next N days: weather at its time and place (only from a weather source), travel from the
previous place (only from a directions source; otherwise "unknown", never guessed), time zone differences,
conflicts and back-to-backs, related email threads, and the people involved. Rows are stored as facts artifacts that
depend on the facts they used, so a moved or cancelled event marks exactly the affected rows stale, and only those
are rebuilt.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .. import log
from ..timeparse import zone
from . import compute

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("daysheet")

KIND = "day_sheet"
# A provider returns {"summary", "source", "fetched_at"} (weather) or {"seconds", "source"} (directions), or None.
WeatherFn = Callable[[str, float], Awaitable[dict[str, Any] | None]]
DirectionsFn = Callable[[str, str, float], Awaitable[dict[str, Any] | None]]


async def openweather_forecast(location: str, when: float) -> dict[str, Any] | None:
    """OpenWeather's 5-day forecast (the user's own key), the slot nearest the event. None without a key."""
    key = os.environ.get("OPENWEATHER_API_KEY")
    if not key or not location or when - time.time() > 5 * 86400:
        return None
    from ..integrations import http
    data = await http.get_json("https://api.openweathermap.org/data/2.5/forecast",
                               {"q": location, "appid": key, "units": "imperial"})
    slots = data.get("list") or []
    if not slots:
        return None
    slot = min(slots, key=lambda s: abs(float(s.get("dt", 0)) - when))
    weather = (slot.get("weather") or [{}])[0]
    temp = (slot.get("main") or {}).get("temp")
    rain = float((slot.get("pop") or 0)) * 100
    summary = f"{weather.get('description', 'forecast')}" + (f", {float(temp):.0f}°F" if temp is not None else "") \
        + (f", {rain:.0f}% chance of rain" if rain >= 20 else "")
    return {"summary": summary, "rain_chance": rain, "source": "openweather:forecast", "fetched_at": time.time()}


class DaySheet:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.weather: WeatherFn | None = openweather_forecast
        self.directions: DirectionsFn | None = None  # set when a directions source is connected

    def _tz(self) -> str:
        return self.app.settings.get("user.timezone") or ""

    def events(self, horizon_days: int | None = None) -> list[dict[str, Any]]:
        days = int(horizon_days or self.app.settings.get("ideas.horizon_days"))
        now = time.time()
        return self.app.facts.repository.query("events", start=now, end=now + days * 86400, limit=500)

    async def build(self, horizon_days: int | None = None) -> dict[str, int]:
        """(Re)build the rows that are missing or stale. Fresh rows are left alone."""
        repo = self.app.facts.repository
        events = self.events(horizon_days)
        live = [e for e in events if e.get("status") != "cancelled"]
        clashes = compute.conflicts(live)
        built = kept = 0
        missing_directions = False
        ordered = sorted(live, key=lambda e: e["start"])
        for index, event in enumerate(ordered):
            artifact_id = f"daysheet:{event['id']}"
            existing = repo.artifact(artifact_id)
            if existing and not existing["stale"] and existing["data"].get("version") == self._version(event):
                kept += 1
                continue
            previous = ordered[index - 1] if index else None
            row, refs, needs_directions = await self._row(event, previous, clashes, live)
            missing_directions |= needs_directions
            repo.save_artifact(artifact_id, KIND, row, refs)
            built += 1
        for event in events:  # a cancelled event's row says so, and its neighbours were already rebuilt
            if event.get("status") == "cancelled":
                repo.save_artifact(f"daysheet:{event['id']}", KIND, {**self._base(event), "cancelled": True},
                                   [("events", event["id"])])
        if missing_directions and not self.app.store.kv_get("daysheet.directions_gap_logged"):
            self.app.store.kv_set("daysheet.directions_gap_logged", True)
            self.app.growth.log_gap("maps/directions connector",
                                    "The day sheet can't check travel time between events in different places.",
                                    by="day-sheet")
        if built:
            self.app.bus.publish("daysheet.updated", {"built": built})
        return {"built": built, "kept": kept}

    @staticmethod
    def _version(event: dict[str, Any]) -> str:
        return f"{event.get('start')}|{event.get('end')}|{event.get('tz')}|{event.get('location')}|{event.get('status')}"

    def _base(self, event: dict[str, Any]) -> dict[str, Any]:
        tz = self._tz()
        start = datetime.fromtimestamp(event["start"], zone(tz))
        p = event.get("provenance") or {}
        return {"event_id": event["id"], "title": event["title"], "start": event["start"], "end": event["end"],
                "day": start.strftime("%Y-%m-%d"), "local_time": start.strftime("%a %b %d, %I:%M %p"),
                "location": event.get("location") or "", "version": self._version(event),
                "source": p.get("source"), "fetched_at": p.get("fetched_at")}

    async def _row(self, event: dict[str, Any], previous: dict[str, Any] | None, clashes: list[dict[str, str]],
                   events: list[dict[str, Any]]) -> tuple[dict[str, Any], list[tuple[str, str]], bool]:
        repo = self.app.facts.repository
        tz = self._tz() or "UTC"
        row = self._base(event)
        refs: list[tuple[str, str]] = [("events", event["id"])]
        # time zones
        event_tz = event.get("tz") or tz
        try:
            diff = compute.timezone_difference(event["start"], tz, event_tz) if event_tz != tz else 0.0
        except Exception:
            diff = 0.0
        if diff:
            there = datetime.fromtimestamp(event["start"], zone(event_tz)).strftime("%I:%M %p")
            row["timezone"] = {"event_tz": event_tz, "hours": diff / 3600, "there": there}
        # conflicts
        others = [c["second"] if c["first"] == event["id"] else c["first"] for c in clashes
                  if event["id"] in (c["first"], c["second"])]
        if others:
            by_id = {e["id"]: e for e in events}
            row["conflicts"] = [{"event_id": o, "title": by_id[o]["title"]} for o in others if o in by_id]
            refs += [("events", o) for o in others if o in by_id]
        # travel from the previous event: from a directions source, or honestly unknown
        needs_directions = False
        if previous and (event["start"] - previous["end"]) < 6 * 3600:
            refs.append(("events", previous["id"]))
            gap = event["start"] - previous["end"]
            moving = bool(event.get("location") and previous.get("location")
                          and event["location"].strip().lower() != previous["location"].strip().lower())
            travel: dict[str, Any] = {"from_event": previous["id"], "from": previous.get("location") or "",
                                      "gap_minutes": round(gap / 60), "back_to_back": gap <= 0}
            route = None
            if moving and self.directions:
                try:
                    route = await self.directions(previous["location"], event["location"], previous["end"])
                except Exception as exc:
                    logger.info("Directions source failed: %s", exc)
            if route and route.get("seconds") is not None and route.get("source"):
                window = compute.travel_window(previous, event, travel_seconds=float(route["seconds"]),
                                               source=route["source"])
                travel.update(status="known", minutes=round(route["seconds"] / 60), source=route["source"],
                              leave_by=window["leave_by"],
                              shortfall_minutes=round((window["shortfall_seconds"] or 0) / 60))
            elif moving:
                needs_directions = not self.directions
                travel.update(status="unknown", reason="No directions source is connected; travel time isn't "
                                                       "guessed." if not self.directions else
                              "The directions source didn't answer.")
            else:
                travel.update(status="same_place")
            row["travel"] = travel
        # weather at its time and place (only from a source)
        if event.get("location") and self.weather:
            try:
                forecast = await self.weather(event["location"], event["start"])
            except Exception as exc:
                logger.info("Weather source failed: %s", exc)
                forecast = None
            row["weather"] = forecast or {"summary": "unknown", "source": None}
        # related email threads and people
        emails = {str(a.get("email") if isinstance(a, dict) else a).lower() for a in event.get("attendees") or []}
        title_words = {w for w in re.findall(r"[a-z0-9]+", event["title"].lower()) if len(w) > 3}
        threads = []
        for thread in repo.query("threads", limit=200):
            participants = {str(p.get("email") if isinstance(p, dict) else p).lower() for p in thread["participants"]}
            subject_words = set(re.findall(r"[a-z0-9]+", thread["subject"].lower()))
            if (emails & participants) or (title_words and len(title_words & subject_words) >= min(2, len(title_words))):
                threads.append({"id": thread["id"], "subject": thread["subject"], "needs_reply": bool(thread["needs_reply"])})
                refs.append(("threads", thread["id"]))
        if threads:
            row["threads"] = threads[:5]
        people = []
        for person in repo.query("people", limit=500):
            if emails & {e.lower() for e in person["emails"]}:
                people.append({"id": person["id"], "name": person["name"], "tz": person.get("tz") or "",
                               "notes_ref": person.get("notes_ref") or ""})
                refs.append(("people", person["id"]))
        if people:
            row["people"] = people
        return row, list(dict.fromkeys(refs)), needs_directions

    def rows(self, include_stale: bool = True) -> list[dict[str, Any]]:
        rows = self.app.store.query("SELECT * FROM facts_artifacts WHERE kind=?", (KIND,))
        out = []
        now = time.time()
        for row in rows:
            data = row["data"]
            if data.get("end", 0) < now or (row["stale"] and not include_stale):
                continue
            out.append({**data, "stale": bool(row["stale"]), "updated_at": row["updated_at"]})
        return sorted(out, key=lambda r: r.get("start", 0))
