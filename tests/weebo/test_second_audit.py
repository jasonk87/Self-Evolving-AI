"""Second Weebo 3 audit: lifecycle, truthful completion and cache regressions."""
import asyncio
import copy
import time
from datetime import datetime

import pytest

from weebo.agents.manager import AgentRun
from weebo.brain.tools import ToolResult
from weebo.integrations import gcalendar
from weebo.store import new_id

from .conftest import drain
from .test_bot_policy import ask
from .test_daysheet_ideas import add_event, tomorrow_at
from .test_weebo3_bugfixes import bot, idea

pytestmark = pytest.mark.asyncio


async def test_paused_bot_cannot_receive_an_automatic_approval(app):
    owner = bot(app, autonomy=["read", "act"])
    app.bots.set_status(owner["id"], "paused", "user")
    assert await ask(app, owner, "make test") == {"decision": "decline"}
    assert not app.approvals.open_items()


@pytest.mark.parametrize("delete", [False, True])
async def test_retirement_closes_its_approval_requests(app, delete):
    owner = bot(app)
    await ask(app, owner, "make deploy")
    await app.bots.retire(owner["id"], delete=delete)
    assert not app.approvals.open_items()
    assert app.store.get_message("needs-you")["status"] == "done"


async def test_retirement_cancels_all_jobs_and_paused_routines(app):
    owner = bot(app, routines=[{"text": "Review examples", "when": "in 1 hour", "recurrence": "daily"}])
    app.bots.set_status(owner["id"], "paused", "user")
    for _ in range(55):
        app.store._insert("bot_jobs", {"id": new_id("job"), "bot_id": owner["id"], "status": "open",
                                       "created_at": time.time(), "updated_at": time.time(), "data": {}})
    await app.bots.retire(owner["id"])
    assert not app.bots.jobs(owner["id"], ("open", "running"))
    assert all(app.store.get_reminder(r)["status"] == "cancelled" for r in owner["data"]["routine_ids"])


async def test_retirement_stops_owned_agents_only(app, fake_engine):
    owner = bot(app)
    owned = await app.agents.start("Example", "Review", conversation_id=owner["conversation_id"])
    chief = await app.agents.start("Other", "Review", conversation_id=app.conversations.main_id())
    await drain(30)
    await app.bots.retire(owner["id"])
    await drain(30)
    assert app.store.get_task(owned["id"])["status"] == "cancelled"
    assert app.store.get_task(chief["id"])["status"] in ("queued", "running")


async def test_deleted_bot_agent_late_tool_call_does_not_become_unrestricted(app, monkeypatch):
    owner = bot(app)
    task = app.store.create_task("Example", "Review", conversation_id=owner["conversation_id"],
                                 meta={"bot_id": owner["id"]})
    await app.bots.retire(owner["id"], delete=True)
    called = []

    async def call(*args):
        called.append(args)
        return ToolResult("Should not run")

    monkeypatch.setattr("weebo.agents.manager.registry.call", call)
    result = await app.agents._on_request(AgentRun(task["id"]), "item/tool/call", {"tool": "recall"})
    assert not result["success"] and not called


async def test_deleted_bot_agent_cannot_create_a_late_native_approval(app, monkeypatch):
    owner = bot(app)
    task = app.store.create_task("Example", "Review", conversation_id=owner["conversation_id"],
                                 meta={"bot_id": owner["id"]})
    await app.bots.retire(owner["id"], delete=True)
    asked = []
    async def ask_user(*args, **kwargs):
        asked.append(args)
        return {"decision": "accept"}
    monkeypatch.setattr(app.interactions, "ask", ask_user)
    result = await app.agents._on_request(AgentRun(task["id"]), "item/commandExecution/requestApproval",
                                          {"command": "make deploy"})
    assert result == {"decision": "decline"} and not asked


async def test_retiring_during_thread_startup_stops_the_wait(app, monkeypatch):
    owner = bot(app)
    entered = asyncio.Event()
    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(app.conversations, "_ensure_thread", blocked)
    running = asyncio.create_task(app.conversations.run_event(owner["conversation_id"], "Review", ""))
    await entered.wait()
    await app.bots.retire(owner["id"])
    assert await asyncio.wait_for(running, 0.5) == "interrupted"
    assert app.conversations.session(owner["conversation_id"]).turn is None


async def test_retirement_waits_for_codex_completion_before_detaching_and_archiving(app, fake_engine, monkeypatch):
    owner = bot(app)
    await app.conversations.run_event(owner["conversation_id"], "Review", "")
    turn = fake_engine.turns[-1]
    waiting = await app.conversations.send(owner["conversation_id"], "Any update?")
    async def acknowledge_only(thread_id, turn_id):
        fake_engine.interrupts.append((thread_id, turn_id))
    monkeypatch.setattr(fake_engine, "interrupt", acknowledge_only)
    await app.bots.retire(owner["id"])
    await drain()
    assert turn["thread_id"] in fake_engine.routes
    assert turn["thread_id"] not in fake_engine.archive_log
    result = await app.conversations._on_request(owner["conversation_id"], "item/tool/call", {"tool": "recall"})
    assert not result["success"]  # tracking completion does not keep its tools enabled
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], status="interrupted")
    await drain(30)
    assert app.conversations.session(owner["conversation_id"]).turn is None
    assert turn["thread_id"] in fake_engine.archive_log and turn["thread_id"] not in fake_engine.routes
    assert len(fake_engine.turns) == 1
    assert not app.store.get_message(waiting["id"])["data"].get("queued")


@pytest.mark.parametrize("status", ["done", "dismissed", "expired"])
async def test_closed_idea_cannot_be_resurrected_by_discuss(app, fake_engine, status):
    row = idea(app)
    app.ideas._set(row["id"], status)
    with pytest.raises(ValueError, match="open|expir"):
        await app.ideas.discuss(row["id"])
    assert not fake_engine.turns and app.ideas.get(row["id"])["status"] == status


async def test_repeated_dismissal_does_not_poison_idea_preferences(app):
    row = idea(app)
    app.ideas.not_useful(row["id"])
    score = app.ideas.weights()
    app.ideas.not_useful(row["id"])
    assert app.ideas.weights() == score


@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
async def test_draft_completion_tracks_the_actual_turn(app, fake_engine, status):
    row = idea(app, "draft_email")
    requested = await app.ideas.do_it(row["id"])
    assert not requested["result"]["verified"]
    assert app.ideas.get(row["id"])["status"] != "done"
    assert app.ideas.get(row["id"])["data"].get("pending_draft")
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Example draft", status=status)
    await drain()
    stored = app.ideas.get(row["id"])
    assert stored["status"] == ("done" if status == "completed" else "new")
    assert not stored["data"].get("pending_draft")


async def test_stopped_queued_draft_remains_retryable(app, fake_engine):
    main = app.conversations.main_id()
    await app.conversations.send(main, "Hello")
    row = idea(app, "draft_email")
    await app.ideas.do_it(row["id"])
    await app.conversations.interrupt(main)
    await drain()
    assert app.ideas.get(row["id"])["status"] == "new"
    assert not app.ideas.get(row["id"])["data"].get("pending_draft")


async def test_brief_reports_are_acknowledged_only_after_publication(app, fake_engine):
    owner = bot(app)
    report = app.reports.file(owner, "done", "Example finished")
    await app.heartbeat.daily_brief()
    assert app.store.query_one("SELECT delivered_at FROM reports WHERE id=?", (report["id"],))["delivered_at"] is None
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Your example finished.")
    await drain()
    assert app.store.query_one("SELECT delivered_at FROM reports WHERE id=?", (report["id"],))["delivered_at"]


async def test_failed_brief_does_not_consume_reports_or_today(app, fake_engine):
    owner = bot(app)
    report = app.reports.file(owner, "done", "Example finished")
    await app.heartbeat.daily_brief()
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], status="failed")
    await drain()
    assert not app.store.kv_get("last_brief_date")
    assert app.store.query_one("SELECT delivered_at FROM reports WHERE id=?", (report["id"],))["delivered_at"] is None


async def test_digest_policy_cursor_keeps_overflow_with_identical_timestamps(app):
    for i in range(12):
        app.store.journal("policy", f"Decision {i}")
    app.store.execute("UPDATE journal SET created_at=100 WHERE kind='policy'")
    app.bots.digest()
    second = app.bots.digest()
    assert "Decision 10" in second and "Decision 11" in second
    assert "Decision 0\n" not in second


async def test_day_sheet_discovers_new_related_facts_without_rebuilding_unrelated_rows(app):
    app.daysheet.weather = None
    event = add_event(app, "review", "Example design review", tomorrow_at(9), tomorrow_at(10), attendees=["sam@example.test"])
    add_event(app, "unrelated", "Dentist", tomorrow_at(18), tomorrow_at(19))
    await app.daysheet.build()
    app.facts.repository.upsert("threads", {"external_id": "mail", "subject": "Example design review",
                                "participants": ["sam@example.test"], "last_message_at": time.time()}, source="app:mail")
    app.facts.repository.upsert("people", {"name": "Sam", "emails": ["sam@example.test"]}, source="app:people")
    assert (await app.daysheet.build())["built"] == 1
    row = next(r for r in app.daysheet.rows() if r["event_id"] == event["id"])
    assert row["threads"][0]["subject"] == "Example design review" and row["people"][0]["name"] == "Sam"


async def test_day_sheet_refreshes_expired_forecasts(app):
    calls = []
    async def weather(*args):
        calls.append(args)
        return {"source": "test:weather", "summary": "Cloudy", "fetched_at": time.time()}
    app.daysheet.weather = weather
    event = add_event(app, "visit", "Visit", tomorrow_at(10), tomorrow_at(11), location="Office")
    await app.daysheet.build()
    app.store.execute("UPDATE facts_artifacts SET updated_at=0 WHERE id=?", ("daysheet:" + event["id"],))
    assert (await app.daysheet.build())["built"] == 1 and len(calls) == 2


async def test_unknown_event_locations_are_not_claimed_to_be_the_same_place(app):
    app.daysheet.weather = None
    add_event(app, "first", "Review", tomorrow_at(10), tomorrow_at(11))
    second = add_event(app, "second", "Visit", tomorrow_at(11), tomorrow_at(12), location="Clinic")
    await app.daysheet.build()
    row = next(r for r in app.daysheet.rows() if r["event_id"] == second["id"])
    assert row["travel"]["status"] == "unknown"


async def test_travel_idea_explains_missing_locations_instead_of_blaming_a_connector(app):
    app.daysheet.weather = None
    add_event(app, "first", "Review", tomorrow_at(10), tomorrow_at(11))
    add_event(app, "second", "Visit", tomorrow_at(11), tomorrow_at(12), location="Clinic")
    made = await app.ideas.generate()
    travel = next(row for row in made if row["kind"] == "travel")
    assert "locations are missing" in travel["data"]["statement"]
    assert "no directions source" not in travel["data"]["statement"]


async def test_commitment_idea_attributes_the_assistants_promise_correctly(app):
    app.facts.repository.upsert("commitments", {"text": "send the example deck", "owner": "assistant",
                                "due": time.time()+3600, "source_ref": "chat:example"}, source="local:commitments")
    made = await app.ideas.generate()
    assert made and "You said" not in made[0]["data"]["statement"]


async def test_digest_timestamp_migration_does_not_replay_acknowledged_decisions(app):
    app.store.journal("policy", "An old decision")
    app.store.execute("UPDATE journal SET created_at=100 WHERE kind='policy'")
    app.store.kv_set("bots.last_digest", 200)
    assert "An old decision" not in app.bots.digest()
    assert "An old decision" not in app.bots.digest()


async def test_restart_recovers_pending_draft_and_brief_receipts(app):
    row = idea(app, "draft_email")
    await app.ideas.do_it(row["id"])
    await app.heartbeat.daily_brief()
    assert app.store.kv_get("brief.pending")
    app.ideas.recover_after_restart()
    app.heartbeat.recover_brief()
    assert app.ideas.get(row["id"])["status"] == "new"
    assert not app.ideas.get(row["id"])["data"].get("pending_draft")
    assert not app.store.kv_get("brief.pending") and not app.store.kv_get("last_brief_date")


async def test_late_draft_completion_cannot_reopen_a_dismissed_idea(app, fake_engine):
    row = idea(app, "draft_email")
    await app.ideas.do_it(row["id"])
    app.ideas.not_useful(row["id"])
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Example draft")
    assert app.ideas.get(row["id"])["status"] == "dismissed"
    assert not app.ideas.get(row["id"])["data"].get("pending_draft")


async def test_brief_receipt_does_not_consume_reports_arriving_during_the_turn(app, fake_engine):
    owner = bot(app)
    first = app.reports.file(owner, "done", "First example finished")
    await app.heartbeat.daily_brief()
    second = app.reports.file(owner, "done", "Second example finished")
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "The first example finished.")
    assert app.store.query_one("SELECT delivered_at FROM reports WHERE id=?", (first["id"],))["delivered_at"]
    assert app.store.query_one("SELECT delivered_at FROM reports WHERE id=?", (second["id"],))["delivered_at"] is None


async def test_stopped_queued_brief_keeps_its_reports_and_allows_retry(app):
    owner = bot(app)
    report = app.reports.file(owner, "done", "Example finished")
    main = app.conversations.main_id()
    await app.conversations.send(main, "Hello")
    await app.heartbeat.daily_brief()
    await app.conversations.interrupt(main)
    assert not app.store.kv_get("brief.pending") and not app.store.kv_get("last_brief_date")
    assert app.store.query_one("SELECT delivered_at FROM reports WHERE id=?", (report["id"],))["delivered_at"] is None


async def test_deleting_chat_releases_its_draft_and_brief_receipts(app):
    row = idea(app, "draft_email")
    await app.ideas.do_it(row["id"])
    await app.heartbeat.daily_brief()
    await app.conversations.delete(app.conversations.main_id())
    assert app.ideas.get(row["id"])["status"] == "new"
    assert not app.ideas.get(row["id"])["data"].get("pending_draft")
    assert not app.store.kv_get("brief.pending")


class CalendarService:
    """No OAuth or network: preserves the exact event sent, with optional read-back corruption."""
    def __init__(self, change=None):
        self.change = change
        self.body = None
        self.window = None

    @staticmethod
    def reply(value):
        return type("Call", (), {"execute": lambda self: value})()

    def calendars(self):
        return type("Calendars", (), {"get": lambda _, **kw: self.reply({"timeZone": "America/Chicago"})})()

    def events(self):
        return self

    def list(self, **kwargs):
        self.window = kwargs
        return self.reply({"items": []})

    def insert(self, **kwargs):
        self.body = copy.deepcopy(kwargs["body"])
        return self.reply({"id": "example"})

    def get(self, **kwargs):
        stored = {**copy.deepcopy(self.body), "id": "example", "status": "confirmed"}
        if self.change:
            self.change(stored)
        return self.reply(stored)


def calendar_fake(monkeypatch, service):
    async def call(fn):
        return fn(service)
    monkeypatch.setattr(gcalendar, "_call", call)


@pytest.mark.parametrize("field", ["start", "end", "description", "id"])
async def test_calendar_verification_checks_the_whole_booking(app, monkeypatch, field):
    def corrupt(stored):
        stored[field] = {"dateTime": "2026-11-02T19:00:00-06:00"} if field in ("start", "end") else "wrong"
    service = CalendarService(corrupt)
    calendar_fake(monkeypatch, service)
    with pytest.raises(gcalendar.IntegrationError, match="isn't confirmed"):
        await gcalendar.schedule_event(app, "Example visit", "2026-11-02T15:00:00", description="Bring the example")


@pytest.mark.parametrize("value", ["2026-03-08T02:30:00", "2026-11-01T01:30:00"])
async def test_calendar_refuses_ambiguous_or_missing_dst_times_before_writing(app, monkeypatch, value):
    service = CalendarService()
    calendar_fake(monkeypatch, service)
    with pytest.raises(gcalendar.IntegrationError, match="ambiguous|does not exist"):
        await gcalendar.schedule_event(app, "Example visit", value)
    assert service.body is None


async def test_calendar_duration_crossing_dst_keeps_real_elapsed_minutes(app, monkeypatch):
    service = CalendarService()
    calendar_fake(monkeypatch, service)
    await gcalendar.schedule_event(app, "Example visit", "2026-03-08T01:30:00", duration_mins=120)
    start = datetime.fromisoformat(service.body["start"]["dateTime"])
    end = datetime.fromisoformat(service.body["end"]["dateTime"])
    assert start.utcoffset() is not None and end.utcoffset() is not None
    assert end.timestamp() - start.timestamp() == 120 * 60
    assert end.hour == 4


async def test_calendar_day_uses_user_timezone_and_next_midnight(app, monkeypatch):
    app.settings.update({"user.timezone": "Pacific/Auckland"})
    service = CalendarService()
    calendar_fake(monkeypatch, service)
    await gcalendar.check_calendar(app, "2026-09-27")
    assert service.window["timeMin"] == "2026-09-27T00:00:00+12:00"
    assert service.window["timeMax"] == "2026-09-28T00:00:00+13:00"
