"""Weebo 3 Phase 5: the day sheet, Ideas, and fact-checking what Weebo writes for the user."""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from weebo.brain.tools import ToolContext, registry
from weebo.facts import factcheck

from .conftest import drain

pytestmark = pytest.mark.asyncio
TZ = "America/Chicago"


def tomorrow_at(hour, minute=0, tz=TZ):
    day = datetime.now(ZoneInfo(tz)).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(tz)).timestamp()


def add_event(app, ext, title, start, end, location="", tz=TZ, status="confirmed", attendees=None):
    row, _ = app.facts.repository.upsert("events", {
        "external_id": ext, "title": title, "start": start, "end": end, "tz": tz, "location": location,
        "status": status, "attendees": attendees or []}, source="test:calendar", ttl_seconds=3600)
    return row


@pytest.fixture
def day(app):
    app.settings.update({"user.timezone": TZ})
    app.daysheet.weather = None
    return {
        "standup": add_event(app, "a", "Team standup", tomorrow_at(10), tomorrow_at(11), "Office"),
        "dentist": add_event(app, "b", "Dentist visit", tomorrow_at(11, 10), tomorrow_at(12), "Clinic"),
        "lunch": add_event(app, "c", "Lunch with Sam", tomorrow_at(11, 30), tomorrow_at(12, 30), "Cafe"),
        "london": add_event(app, "d", "London sync", tomorrow_at(15), tomorrow_at(16), "", tz="Europe/London"),
    }


async def test_day_sheet_never_guesses_travel_time(app, day):
    await app.daysheet.build()
    rows = {r["event_id"]: r for r in app.daysheet.rows()}
    dentist = rows[day["dentist"]["id"]]
    assert dentist["travel"]["status"] == "unknown" and "minutes" not in dentist["travel"]
    assert dentist["travel"]["gap_minutes"] == 10
    assert {c["event_id"] for c in dentist["conflicts"]} == {day["lunch"]["id"]}
    assert rows[day["london"]["id"]]["timezone"]["event_tz"] == "Europe/London"
    assert rows[day["standup"]["id"]]["source"] == "test:calendar"  # every row carries its provenance
    gap = app.store.query_one("SELECT * FROM capability_gaps")
    assert gap["data"]["need"] == "maps/directions connector"  # shown on the Capabilities page


async def test_day_sheet_uses_real_sources_when_connected(app, day):
    async def directions(origin, destination, when):
        return {"seconds": 1500, "source": "test:maps"}

    async def weather(location, when):
        return {"summary": "light rain, 61°F", "rain_chance": 80, "source": "test:weather", "fetched_at": time.time()}

    app.daysheet.directions, app.daysheet.weather = directions, weather
    await app.daysheet.build()
    dentist = {r["event_id"]: r for r in app.daysheet.rows()}[day["dentist"]["id"]]
    assert dentist["travel"] == {**dentist["travel"], "status": "known", "minutes": 25, "source": "test:maps",
                                 "shortfall_minutes": 15}
    assert dentist["weather"]["source"] == "test:weather"


async def test_only_changed_rows_are_rebuilt(app, day):
    first = await app.daysheet.build()
    assert first["built"] == 4
    assert (await app.daysheet.build())["built"] == 0
    app.facts.repository.upsert("events", {"external_id": "b", "title": "Dentist visit", "start": tomorrow_at(13),
                                           "end": tomorrow_at(14), "tz": TZ, "location": "Clinic"},
                                source="test:calendar", ttl_seconds=3600)
    again = await app.daysheet.build()
    assert 0 < again["built"] < 4  # the moved event and the rows that depended on it
    dentist = {r["event_id"]: r for r in app.daysheet.rows()}[day["dentist"]["id"]]
    assert "conflicts" not in dentist


async def test_ideas_are_specific_cited_capped_and_deduped(app, day):
    app.settings.update({"ideas.max_per_day": 3})
    made = await app.ideas.generate()
    assert len(made) == 3
    kinds = [i["kind"] for i in made]
    assert kinds[0] == "conflict"
    conflict = made[0]
    assert "Dentist visit" in conflict["data"]["statement"] and "Lunch with Sam" in conflict["data"]["statement"]
    assert {e["id"] for e in conflict["data"]["evidence"]} == {day["dentist"]["id"], day["lunch"]["id"]}
    assert all(e["source"] == "test:calendar" for e in conflict["data"]["evidence"])
    assert await app.ideas.generate() == []  # cap reached, and nothing twice
    app.settings.update({"ideas.max_per_day": 10})
    more = await app.ideas.generate()
    assert not {i["key"] for i in more} & {i["key"] for i in made}


async def test_not_useful_teaches_and_dismissed_ideas_stay_gone(app, day):
    made = await app.ideas.generate()
    travel = next(i for i in made if i["kind"] == "travel")
    app.ideas.not_useful(travel["id"])
    assert app.ideas.weights()["travel"] < 1
    assert not [i for i in await app.ideas.generate() if i["kind"] == "travel"]
    app.ideas._feedback("timezone", -1)
    app.ideas.not_useful(next(i for i in made if i["kind"] == "timezone")["id"])
    memories = app.store.search_memories("suggestions useful", limit=5)
    assert any("timezone suggestions" in m["text"] for m in memories)  # learned as a preference
    app.ideas._feedback("travel", -2)
    assert app.ideas.suppressed("travel")


async def test_ideas_go_stale_when_their_facts_change(app, day):
    made = await app.ideas.generate()
    conflict = next(i for i in made if i["kind"] == "conflict")
    app.facts.repository.upsert("events", {"external_id": "c", "title": "Lunch with Sam", "start": tomorrow_at(13),
                                           "end": tomorrow_at(14), "tz": TZ, "location": "Cafe"},
                                source="test:calendar", ttl_seconds=3600)
    assert next(i for i in app.ideas.list() if i["id"] == conflict["id"])["stale"]
    with pytest.raises(ValueError, match="changed"):
        await app.ideas.do_it(conflict["id"], 1)  # read before acting
    assert app.ideas.get(conflict["id"])["status"] == "expired"
    fresh = await app.ideas.generate()
    assert not [i for i in fresh if i["kind"] == "conflict"]  # the conflict is gone, so no new one


async def test_do_it_runs_only_safe_actions_and_verifies(app, day):
    made = await app.ideas.generate()
    travel = next(i for i in made if i["kind"] == "travel")
    done = await app.ideas.do_it(travel["id"], 0)
    assert done["result"]["verified"] and app.store.get_reminder(done["result"]["reminder_id"])
    assert app.ideas.get(travel["id"])["status"] == "done"
    risky = app.ideas.add("schedule", "Move the dentist", "Ask the clinic to move the dentist visit to 2pm tomorrow.",
                          [{"entity": "events", "id": day["dentist"]["id"]}],
                          [{"kind": "propose_time", "label": "Ask to move it"}])
    with pytest.raises(ValueError, match="approval"):
        await app.ideas.do_it(risky["id"], 0)


async def test_discuss_opens_the_chat_with_the_idea(app, day, fake_engine):
    made = await app.ideas.generate()
    idea = made[0]
    await app.ideas.discuss(idea["id"])
    main = app.conversations.main_id()
    sent = [m for m in app.store.list_messages(main) if m["role"] == "user"]
    assert sent[-1]["content"].startswith("Let's talk about this:")
    turn = fake_engine.turns[-1]
    assert idea["id"] in turn["context"] and "offer_replies" in turn["context"]
    assert app.ideas.get(idea["id"])["status"] == "discussed"


async def test_chief_ideas_need_real_evidence(app, day):
    ctx = ToolContext(app, "t", "turn", conversation_id=app.conversations.main_id(), trigger="brief")
    bad = await registry.call(ctx, "suggest_idea", {"kind": "prep", "title": "Prep", "statement": "Prepare the deck for "
                                                    "the board meeting tomorrow.", "evidence": [{"entity": "events", "id": "nope"}]})
    assert not bad.success and "facts store" in bad.text
    good = await registry.call(ctx, "suggest_idea", {
        "kind": "prep", "title": "Prep for the standup", "statement": "Jot down yesterday's progress before the "
        "team standup at 10am.", "evidence": [{"entity": "events", "id": day["standup"]["id"]}],
        "actions": [{"kind": "reminder", "label": "Remind me at 9:45", "params": {"when": "tomorrow 9:45am"}}]})
    assert good.success


# ---------------------------------------------------------------- fact-checking
async def test_deterministic_fact_check_catches_wrong_times_and_cancellations(app, day):
    events = [day["standup"], {**day["lunch"], "status": "cancelled"}]
    issues = factcheck.deterministic("Your Team standup is at 9am. Then Lunch with Sam at 11:30.", events, TZ)
    assert issues[0]["claim"] == "says 9am for Team standup" and "10am" in issues[0]["fact"]
    assert "cancelled" in issues[1]["fact"]
    assert factcheck.deterministic("Team standup at 10am, as usual.", events, TZ) == []


async def test_brief_is_flagged_before_it_reaches_the_user(app, day, fake_engine):
    main = app.conversations.main_id()
    await app.conversations.run_event(main, "brief please", "Morning brief", trigger="brief")
    thread = app.store.get_conversation(main)["thread_id"]
    await fake_engine.finish_turn(thread, fake_engine.turns[-1]["turn_id"], "Morning! Team standup is at 9am.")
    final = [m for m in app.store.list_messages(main) if m["kind"] == "text" and m["role"] == "assistant"][-1]
    assert "Fact-check: says 9am for Team standup, but the calendar says 10am" in final["content"]
    assert final["data"]["factcheck"][0]["id"] == day["standup"]["id"]


async def test_second_pass_runs_only_on_claims_about_stored_facts(app, day, monkeypatch):
    calls = []

    async def fake_ask(engine, prompt, **kw):
        calls.append(kw)
        return '{"issues": [{"claim": "Sam lunch is at the Office", "fact": "the calendar says Cafe"}]}'

    monkeypatch.setattr("weebo.codex.oneshot.ask", fake_ask)
    main = app.conversations.main_id()
    await app.factcheck.review_turn(main, "m1", "Have a lovely day!")
    assert calls == []  # nothing to check against
    await app.factcheck.review_turn(main, "m1", "Lunch with Sam is at the Office.")
    assert calls and calls[0]["effort"] == "low"
    note = [m for m in app.store.list_messages(main) if m["kind"] == "factcheck"][-1]
    assert "the calendar says Cafe" in note["content"]


async def test_ideas_and_day_sheet_over_the_api(app, day):
    from aiohttp.test_utils import TestClient, TestServer

    from weebo.server.app import TOKEN_KEY, create_app
    web_app = create_app(app)
    client = TestClient(TestServer(web_app))
    await client.start_server()
    try:
        h = {"Host": "127.0.0.1", "X-Weebo-Token": web_app[TOKEN_KEY]}
        sheet = await (await client.get("/api/daysheet", headers=h)).json()
        assert len(sheet["rows"]) == 4 and sheet["travel_source"] is False
        ideas = (await (await client.get("/api/ideas?refresh=1", headers=h)).json())["ideas"]
        assert ideas
        resp = await client.post(f"/api/ideas/{ideas[0]['id']}/dismiss", headers=h)
        assert (await resp.json())["idea"]["status"] == "dismissed"
        await drain()
    finally:
        await client.close()


# ---------------------------------------------------------------- email facts and verified actions
async def test_email_facts_come_from_the_connectors_raw_answer(app, fake_engine):
    import json as _json

    from weebo.facts import mail_ingest
    raw = {"content": [{"type": "text", "text": _json.dumps({"threads": [
        {"threadId": "t-1", "subject": "Contract review", "from": "pat@example.com", "labelIds": ["UNREAD", "INBOX"],
         "internalDate": "1790000000000", "snippet": "Can you look this over?", "needs_reply": True},
        {"threadId": "t-2", "subject": "No date here", "from": "x@example.com"}]})}]}
    assert mail_ingest.ingest(app.facts.repository, "app:gmail", raw) == 1  # no timestamp: skipped, not guessed
    thread = app.facts.repository.query("threads", needs_reply=True)[0]
    assert thread["subject"] == "Contract review" and thread["provenance"]["source"] == "app:gmail"
    # and it happens by itself when a mail tool call completes in a chat
    main = app.conversations.main_id()
    await app.conversations.send(main, "check my mail")
    thread_id = app.store.get_conversation(main)["thread_id"]
    turn_id = fake_engine.turns[-1]["turn_id"]
    await fake_engine.emit(thread_id, "turn/started", {"turn": {"id": turn_id}})
    raw2 = {"content": [{"type": "text", "text": _json.dumps([{"id": "t-9", "subject": "Lunch?",
                                                               "date": "2026-10-16T12:00:00Z", "unread": True}])}]}
    await fake_engine.emit(thread_id, "item/completed", {"turnId": turn_id, "item": {
        "type": "mcpToolCall", "id": "m9", "server": "codex_apps", "tool": "gmail_search_threads",
        "status": "completed", "result": raw2}})
    assert any(t["subject"] == "Lunch?" for t in app.facts.repository.query("threads"))


async def test_sms_is_verified_after_sending(app, monkeypatch):
    from weebo.integrations import http
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"):
        monkeypatch.setenv(var, "v")
    replies = [{"sid": "SM2", "status": "failed", "error_code": 30003}, {"sid": "SM3", "status": "queued"}]

    async def fake_post(url, data, auth=None, timeout=20.0):
        return replies.pop(0)

    async def yes(*a, **k):
        return True

    monkeypatch.setattr(http, "post_form", fake_post)
    monkeypatch.setattr(app.interactions, "confirm", yes)
    args = {"recipient": "+15551234567", "message": "On my way"}
    text, ok, _ = await app.integrations.run("send_text_message", args)
    assert not ok and "didn't send" in text
    text, ok, _ = await app.integrations.run("send_text_message", args)
    assert ok and "status queued" in text


async def test_calendar_events_are_read_back_after_creating(app, monkeypatch):
    from weebo.integrations import gcalendar

    class Call:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class Events:
        def __init__(self, stored):
            self.stored = stored

        def list(self, **kw):
            return Call({"items": [{"summary": "Gym"}]})

        def insert(self, **kw):
            return Call({"id": "e1", "htmlLink": "https://calendar.example/e1"})

        def get(self, **kw):
            return Call(self.stored)

    class Service:
        def __init__(self, stored):
            self.ev = Events(stored)

        def calendars(self):
            return type("C", (), {"get": lambda _s, **kw: Call({"timeZone": TZ})})()

        def events(self):
            return self.ev

    for stored, expect_ok in (({}, False), ({"id": "e1", "summary": "Dentist", "status": "confirmed",
                                            "start": {"dateTime": "2026-11-02T15:00:00-06:00"},
                                            "end": {"dateTime": "2026-11-02T16:00:00-06:00"}}, True)):
        async def fake_call(fn, stored=stored):
            return fn(Service(stored))

        monkeypatch.setattr(gcalendar, "_call", fake_call)
        if expect_ok:
            outcome = await gcalendar.schedule_event(app, "Dentist", "2026-11-02T15:00:00")
            assert "verified on the calendar" in outcome.text and "overlaps: Gym" in outcome.text
        else:
            with pytest.raises(gcalendar.IntegrationError, match="isn't confirmed"):
                await gcalendar.schedule_event(app, "Dentist", "2026-11-02T15:00:00")
