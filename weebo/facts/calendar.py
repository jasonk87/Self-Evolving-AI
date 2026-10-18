"""Google Calendar facts adapter, reusing an existing sign-in without opening OAuth.

Incremental pagination and 410 reset follow Calendar's events.list contract.
Raw connector responses, not model paraphrases, supply the facts.
"""

from __future__ import annotations

import asyncio
import threading

from .. import paths
from .compute import instant
from .sync import FactBatch


class GoogleCalendarSource:
    entity = "events"

    def __init__(self, repository, calendar_id: str = "primary", service_factory=None):
        self.id = "google-calendar:" + calendar_id
        self.calendar_id = calendar_id
        self.repository = repository
        self._factory = service_factory or self._authorized_service
        self._service = None
        self._lock = threading.Lock()
        self._inflight: asyncio.Task | None = None
        self._inflight_cursor: str | None = None

    @staticmethod
    def available() -> bool:
        return (paths.data_dir() / "google" / "token.json").is_file()

    @staticmethod
    def _authorized_service():
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        token = paths.data_dir() / "google" / "token.json"
        if not token.is_file():
            raise RuntimeError("Google Calendar has no existing sign-in")
        creds = Credentials.from_authorized_user_file(str(token))
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds.valid:
            raise RuntimeError("Google Calendar needs the user's sign-in")
        # Refresh credentials in memory only. No adoption, new scopes, key changes or OAuth UI.
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def _fetch_pages(self, cursor):
        with self._lock:
            if self._service is None:
                self._service = self._factory()
            for attempt in range(2):
                items, page, seen = [], None, set()
                try:
                    for _ in range(1000):
                        params = {"calendarId": self.calendar_id, "singleEvents": True, "showDeleted": True, "maxResults": 2500}
                        if cursor:
                            params["syncToken"] = cursor
                        if page:
                            params["pageToken"] = page
                        result = self._service.events().list(**params).execute()
                        items.extend((raw, result.get("timeZone")) for raw in result.get("items", []))
                        page = result.get("nextPageToken")
                        if not page:
                            if not result.get("nextSyncToken"):
                                raise ValueError("Calendar did not supply a completed sync token")
                            return items, result["nextSyncToken"], cursor is None
                        if page in seen:
                            raise ValueError("Calendar returned a repeated page token")
                        seen.add(page)
                    raise ValueError("Calendar sync exceeded the pagination limit")
                except Exception as exc:
                    if attempt == 0 and cursor and getattr(getattr(exc, "resp", None), "status", None) == 410:
                        cursor = None
                        continue
                    raise

    async def fetch(self, cursor: str | None) -> FactBatch:
        # Cancelling asyncio.to_thread cannot stop the underlying HTTP request.
        # Reuse it after a timeout instead of stacking blocked executor threads.
        if self._inflight is None:
            self._inflight_cursor = cursor
            self._inflight = asyncio.create_task(asyncio.to_thread(self._fetch_pages, cursor))
            self._inflight.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        if cursor != self._inflight_cursor:
            raise ValueError("A sync with a different cursor is already in flight")
        try:
            items, next_cursor, full = await asyncio.shield(self._inflight)
        except asyncio.CancelledError:
            raise
        finally:
            if self._inflight is not None and self._inflight.done():
                self._inflight = None
        records = []
        for raw, calendar_tz in items:
            external_id = raw["id"]
            if raw.get("status") == "cancelled":
                previous = self.repository.store.query_one("SELECT id FROM facts_events WHERE source=? AND external_id=?",
                                                          (self.id, external_id))
                if previous:
                    records.append({"external_id": external_id, "status": "cancelled",
                                    "version": raw.get("etag", ""), "raw": raw})
                continue  # a tombstone for an event we never saw has no times to invent
            start, end = raw["start"], raw["end"]
            tz = start.get("timeZone") or calendar_tz
            if not tz:
                raise ValueError("Calendar did not supply the event timezone")
            records.append({"external_id": external_id, "title": raw.get("summary", "(untitled event)"),
                            "start": instant(start.get("dateTime") or start["date"], tz),
                            "end": instant(end.get("dateTime") or end["date"], end.get("timeZone") or tz),
                            "tz": tz, "location": raw.get("location", ""), "attendees": raw.get("attendees", []),
                            "status": raw.get("status", "confirmed"), "version": raw.get("etag", ""), "raw": raw})
        return FactBatch(records, next_cursor, full)
