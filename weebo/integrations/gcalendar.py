"""Google Calendar through the user's own OAuth client (credentials.json).

Credentials live in ``weebo_data/google/``. Files left by Weebo 1.x (``ai_assistant/core/data`` or the project
root) are picked up and copied there the first time, so an existing sign-in keeps working.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import log, paths
from .base import IntegrationError, Outcome

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("integrations.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar"]
_lock = threading.Lock()
_service: Any = None


def google_dir() -> Path:
    return paths.sub("google")


def _legacy_dirs() -> list[Path]:
    return [paths.PROJECT_ROOT / "ai_assistant" / "core" / "data", paths.PROJECT_ROOT]


def credentials_path() -> Path | None:
    """The OAuth client file, adopting a Weebo 1.x copy if that's the only one."""
    target = google_dir() / "credentials.json"
    if target.exists():
        return target
    for folder in _legacy_dirs():
        if (folder / "credentials.json").exists():
            shutil.copy2(folder / "credentials.json", target)
            return target
    return None


def libraries_missing() -> list[str]:
    missing = []
    for module, package in (("googleapiclient.discovery", "google-api-python-client"),
                            ("google_auth_oauthlib.flow", "google-auth-oauthlib")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return missing


def _load_token() -> Any:
    from google.oauth2.credentials import Credentials

    token = google_dir() / "token.json"
    if token.exists():
        try:
            return Credentials.from_authorized_user_file(str(token), SCOPES)
        except (ValueError, OSError) as exc:
            logger.warning("Ignoring unreadable Google token: %s", exc)
    for folder in _legacy_dirs():
        legacy = folder / "token.json"
        if legacy.exists():
            # Weebo 1.x pickled the credentials object under a .json name; it is Weebo's own file in its own folder.
            import pickle
            try:
                with legacy.open("rb") as handle:
                    creds = pickle.load(handle)  # noqa: S301
                token.write_text(creds.to_json(), encoding="utf-8")
                return creds
            except Exception as exc:
                logger.info("Couldn't reuse Weebo 1.x Google token (%s); signing in again.", exc)
    return None


def _build_service() -> Any:
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = _load_token()
    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            logger.warning("Google token refresh failed: %s", exc)
            creds = None
    if not creds or not creds.valid:
        client = credentials_path()
        if client is None:
            raise IntegrationError("Google Calendar isn't set up: put your OAuth credentials.json in "
                                   f"{google_dir()}.")
        creds = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES).run_local_server(port=0)
    (google_dir() / "token.json").write_text(creds.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _service_sync() -> Any:
    global _service
    with _lock:
        if _service is None:
            _service = _build_service()
        return _service


async def _call(fn: Any) -> Any:
    """Run a blocking Google API call off the event loop."""
    def run() -> Any:
        return fn(_service_sync())
    return await asyncio.to_thread(run)


def _format(events: list[dict[str, Any]], heading: str, empty: str) -> str:
    if not events:
        return empty
    lines = [heading]
    for event in events:
        start = event.get("start", {})
        lines.append(f"- {start.get('dateTime') or start.get('date')}: {event.get('summary') or '(no title)'}")
    return "\n".join(lines)


def _day(date_str: str) -> dt.date:
    value = (date_str or "").strip().lower()
    today = dt.datetime.now().date()
    if value in ("", "today"):
        return today
    if value == "tomorrow":
        return today + dt.timedelta(days=1)
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise IntegrationError("Date must be 'today', 'tomorrow' or YYYY-MM-DD.") from None


async def check_calendar(app: "WeeboApp", date_str: str = "today") -> Outcome:
    day = _day(date_str)
    start = dt.datetime.combine(day, dt.time.min).astimezone()
    end = dt.datetime.combine(day, dt.time.max).astimezone()
    result = await _call(lambda s: s.events().list(calendarId="primary", timeMin=start.isoformat(),
                                                   timeMax=end.isoformat(), singleEvents=True,
                                                   orderBy="startTime").execute())
    return Outcome(_format(result.get("items", []), f"Agenda for {day}:", f"No events on {day}."))


async def list_upcoming_events(app: "WeeboApp", max_results: int = 10) -> Outcome:
    count = max(1, min(50, int(max_results or 10)))
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    result = await _call(lambda s: s.events().list(calendarId="primary", timeMin=now, maxResults=count,
                                                   singleEvents=True, orderBy="startTime").execute())
    return Outcome(_format(result.get("items", []), "Upcoming events:", "No upcoming events."))


async def schedule_event(app: "WeeboApp", summary: str, datetime_str: str, duration_mins: int = 60,
                         description: str = "") -> Outcome:
    try:
        start = dt.datetime.fromisoformat(datetime_str)
    except (TypeError, ValueError):
        raise IntegrationError("datetime_str must be ISO format, e.g. 2026-10-04T15:30:00.") from None
    end = start + dt.timedelta(minutes=max(1, int(duration_mins or 60)))

    def insert(service: Any) -> Any:
        zone = service.calendars().get(calendarId="primary").execute().get("timeZone", "UTC")
        body = {"summary": summary, "description": description,
                "start": {"dateTime": start.isoformat(), "timeZone": zone},
                "end": {"dateTime": end.isoformat(), "timeZone": zone}}
        return service.events().insert(calendarId="primary", body=body).execute()

    event = await _call(insert)
    return Outcome(f"Created \"{summary}\" at {start:%a %b %d %I:%M %p}: {event.get('htmlLink', '')}")
