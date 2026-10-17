from types import SimpleNamespace

import pytest

from weebo.facts.calendar import GoogleCalendarSource

pytestmark = pytest.mark.asyncio


class Calendar:
    def __init__(self, *pages):
        self.pages, self.calls = list(pages), []

    def events(self):
        return self

    def list(self, **params):
        self.calls.append(params)
        return self

    def execute(self):
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def event(id="one", **values):
    return {"id": id, "summary": "Spring day", "start": {"date": "2026-03-08"},
            "end": {"date": "2026-03-09"}, "etag": "v1", **values}


async def test_calendar_pages_all_day_and_incremental_tombstone(app):
    calendar = Calendar({"items": [event()], "timeZone": "America/Chicago", "nextPageToken": "page-2"},
                        {"items": [event("two")], "timeZone": "America/Chicago", "nextSyncToken": "sync-1"},
                        {"items": [{"id": "one", "status": "cancelled"}], "nextSyncToken": "sync-2"})
    source = GoogleCalendarSource(app.facts.repository, service_factory=lambda: calendar)
    app.facts.sync.register(source)
    assert (await app.facts.sync.sync(source.id, force=True))["ok"]
    rows = app.facts.repository.query("events")
    assert len(rows) == 2 and rows[0]["end"] - rows[0]["start"] == 23 * 3600
    assert calendar.calls[1]["pageToken"] == "page-2"
    assert "syncToken" not in calendar.calls[1]
    await app.facts.sync.sync(source.id, force=True)
    assert calendar.calls[2]["syncToken"] == "sync-1"
    assert len(app.facts.repository.query("events", status="cancelled")) == 1


async def test_invalid_sync_token_reconciles_full_snapshot_without_losing_history(app):
    gone = RuntimeError("gone")
    gone.resp = SimpleNamespace(status=410)
    calendar = Calendar({"items": [event()], "timeZone": "UTC", "nextSyncToken": "old"}, gone,
                        {"items": [event("replacement")], "timeZone": "UTC", "nextSyncToken": "new"})
    source = GoogleCalendarSource(app.facts.repository, service_factory=lambda: calendar)
    app.facts.sync.register(source)
    await app.facts.sync.sync(source.id, force=True)
    await app.facts.sync.sync(source.id, force=True)
    assert calendar.calls[1]["syncToken"] == "old" and "syncToken" not in calendar.calls[2]
    assert len(app.facts.repository.query("events")) == 2
    assert app.facts.repository.query("events", status="cancelled")[0]["external_id"] == "one"
    assert len(app.facts.repository.changes_since()) == 3


async def test_partial_pagination_failure_cannot_publish_a_half_calendar(app):
    calendar = Calendar({"items": [event()], "timeZone": "UTC", "nextPageToken": "second"}, OSError("offline"))
    source = GoogleCalendarSource(app.facts.repository, service_factory=lambda: calendar)
    app.facts.sync.register(source)
    result = await app.facts.sync.sync(source.id, force=True)
    assert not result["ok"] and app.facts.repository.query("events") == []
    assert not app.store.query_one("SELECT * FROM facts_sources")["last_success_at"]


async def test_no_new_authorization_or_token_adoption(app, monkeypatch):
    from weebo.integrations import gcalendar
    monkeypatch.setattr(gcalendar, "_load_token", lambda: pytest.fail("Must not adopt credentials"))
    assert not GoogleCalendarSource.available()
    # The availability check doesn't load the Google SDK or open OAuth.
    assert not app.facts.sync.sources
