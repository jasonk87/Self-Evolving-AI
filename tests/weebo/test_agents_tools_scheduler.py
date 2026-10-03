import asyncio
import time

import pytest

from weebo.brain.tools import ToolContext, registry
from tests.weebo.conftest import drain

pytestmark = pytest.mark.asyncio


def ctx(app, conversation_id=None, task_id=None, trigger="user"):
    return ToolContext(app, "thread-x", "turn-x", conversation_id=conversation_id, task_id=task_id, trigger=trigger)


# ---------------------------------------------------------------- tool registry
async def test_registry_validates_arguments(app):
    bad = await registry.call(ctx(app), "remember", {"txt": "oops"})
    assert not bad.success and "Unknown argument" in bad.text
    missing = await registry.call(ctx(app), "remember", {})
    assert not missing.success and "Missing required" in missing.text
    unknown = await registry.call(ctx(app), "teleport", {})
    assert not unknown.success
    parsed = await registry.call(ctx(app), "remember", '{"text": "User enjoys chess"}')
    assert parsed.success


async def test_memory_tools_roundtrip(app):
    await registry.call(ctx(app), "remember", {"text": "User's cat is called Miso", "kind": "person"})
    found = await registry.call(ctx(app), "recall", {"query": "cat name"})
    assert "Miso" in found.text
    memory_id = found.text.split("]")[0].strip("[")
    gone = await registry.call(ctx(app), "forget", {"memory_id": memory_id})
    assert gone.success and app.memory.stats()["total"] == 0


async def test_update_settings_blocks_safety_keys(app):
    blocked = await registry.call(ctx(app), "update_settings", {"changes": {"autonomy.level": "full"}})
    assert not blocked.success and "Settings" in blocked.text
    ok = await registry.call(ctx(app), "update_settings", {"changes": {"voice.speak_replies": True}})
    assert ok.success and app.settings.get("voice.speak_replies") is True


async def test_open_in_browser_rejects_non_http(app):
    result = await registry.call(ctx(app), "open_in_browser", {"url": "file:///C:/secret.txt"})
    assert not result.success


# ---------------------------------------------------------------- scheduler
async def test_reminder_tool_and_firing(app):
    conv = app.conversations.create()
    result = await registry.call(ctx(app, conv["id"]), "set_reminder", {"text": "Stretch", "when": "in 1 minute"})
    assert result.success and "Reminder" in result.text
    reminder = app.store.list_reminders()[0]
    app.store.update_reminder(reminder["id"], due_at=time.time() - 1)
    notes = []
    app.bus.subscribe("notify", lambda t, d: notes.append(d["notification"]))
    await app.scheduler.tick()
    assert app.store.get_reminder(reminder["id"])["status"] == "done"
    assert any(m["kind"] == "reminder" for m in app.store.list_messages(conv["id"]))
    assert notes and notes[0]["kind"] == "reminder"


async def test_recurring_routine_runs_and_reschedules(app, fake_engine):
    conv = app.conversations.create()
    app.scheduler.add("Check the weather", "in 1 minute", "daily", "prompt", conv["id"])
    reminder = app.store.list_reminders()[0]
    app.store.update_reminder(reminder["id"], due_at=time.time() - 1)
    await app.scheduler.tick()
    after = app.store.get_reminder(reminder["id"])
    assert after["status"] == "pending" and after["due_at"] > time.time() + 23 * 3600
    assert fake_engine.turns and "Check the weather" in fake_engine.turns[-1]["input"][0]["text"]


async def test_scheduler_rejects_past_and_too_frequent_routines(app):
    from weebo.timeparse import TimeParseError
    with pytest.raises(TimeParseError):
        app.scheduler.add("x", "2001-01-01T10:00")
    with pytest.raises(TimeParseError):
        app.scheduler.add("x", "in 5 minutes", "every 5 minutes", "prompt")


# ---------------------------------------------------------------- agents
async def test_agents_run_in_parallel_up_to_limit_and_report(app, fake_engine):
    app.settings.update({"agents.max_parallel": 2, "agents.auto_followup": False})
    conv = app.conversations.create()
    tasks = [await app.agents.start(f"Job {i}", f"Do job {i}", conversation_id=conv["id"]) for i in range(3)]
    assert [t["status"] for t in tasks] == ["running", "running", "queued"]
    await drain(10)
    first = fake_engine.turns[0]
    await fake_engine.emit(first["thread_id"], "turn/started", {"turn": {"id": first["turn_id"]}})
    await fake_engine.emit(first["thread_id"], "item/completed", {"turnId": first["turn_id"], "item": {
        "type": "fileChange", "id": "f", "status": "completed", "changes": [{"path": "game.html", "kind": {"type": "add"}, "diff": "+x"}]}})
    await fake_engine.finish_turn(first["thread_id"], first["turn_id"], "Built game.html")
    done = await app.agents.wait(tasks[0]["id"], timeout=2)
    assert done["status"] == "completed" and done["summary"] == "Built game.html"
    await drain(10)
    assert app.store.get_task(tasks[2]["id"])["status"] == "running"  # queue advanced
    report = [m for m in app.store.list_messages(conv["id"]) if m["kind"] == "agent_report"]
    assert report and report[0]["data"]["status"] == "completed"
    events = [e["kind"] for e in app.store.list_task_events(tasks[0]["id"])]
    assert "file_change" in events and "finished" in events


async def test_agent_auto_followup_runs_event_turn(app, fake_engine):
    conv = app.conversations.create()
    task = await app.agents.start("Research", "Find things", conversation_id=conv["id"])
    await drain(10)
    t = fake_engine.turns[0]
    await fake_engine.finish_turn(t["thread_id"], t["turn_id"], "Found 3 things")
    await app.agents.wait(task["id"], timeout=2)
    await drain(20)
    followups = [turn for turn in fake_engine.turns if "background agents just finished" in turn["input"][0]["text"]]
    assert followups


async def test_agent_stop_and_cwd_guard(app, fake_engine, tmp_path):
    with pytest.raises(ValueError):
        await app.agents.start("x", "y", cwd=str(tmp_path / "does-not-exist"))
    task = await app.agents.start("Long job", "work forever")
    await drain(10)
    await app.agents.stop(task["id"])
    done = await app.agents.wait(task["id"], timeout=2)
    assert done["status"] == "cancelled"


async def test_agent_tool_from_chat(app, fake_engine):
    conv = app.conversations.create()
    result = await registry.call(ctx(app, conv["id"]), "start_agent", {"title": "Snake", "instructions": "Build snake"})
    assert result.success and "Snake" in result.text
    status = await registry.call(ctx(app, conv["id"]), "agent_status", {})
    assert "Snake" in status.text


async def test_propose_tool_reports_the_true_state(app):
    """Regression: Weebo told the user 'it's already building' while nothing was being built."""
    app.settings.update({"evolution.mode": "propose"})
    asked = {"requested_by_user": True}
    idea = await registry.call(ctx(app), "propose_improvement", {"title": "Add a timer tool", "description": "A timer.", **asked})
    assert "saved as an idea; nothing is being built" in idea.text and "building now" not in idea.text
    app.settings.update({"evolution.mode": "build"})
    queued = await registry.call(ctx(app), "propose_improvement", {"title": "Add a stopwatch view", "description": "x", **asked})
    assert "queued" in queued.text
    status = await registry.call(ctx(app), "evolution_status", {})
    assert "Add a timer tool" in status.text and "saved as an idea" in status.text


async def test_proposal_origin_decides_who_vets_it(app, monkeypatch):
    """Only a live user request skips the Council; routines, briefs, agents and Weebo's own ideas don't."""
    seen = []

    async def fake_propose(title, description, rationale="", source="user", conversation_id=None, meta=None):
        seen.append(source)
        return {"id": f"p_{len(seen)}", "title": title, "status": "proposed", "meta": {}}

    monkeypatch.setattr(app.evolution, "propose", fake_propose)
    args = {"title": "Add a timer", "description": "A timer."}
    await registry.call(ctx(app), "propose_improvement", {**args, "requested_by_user": True})
    await registry.call(ctx(app), "propose_improvement", args)
    await registry.call(ctx(app, trigger="routine"), "propose_improvement", {**args, "requested_by_user": True})
    await registry.call(ctx(app, trigger="brief"), "propose_improvement", args)
    await registry.call(ctx(app, task_id="t_1"), "propose_improvement", {**args, "requested_by_user": True})
    assert seen == ["user", "conversation", "routine", "brief", "agent"]


async def test_agent_followup_respects_background_budget(app, fake_engine):
    started = []

    async def fake_run_event(conv_id, prompt, notice, **kwargs):
        started.append(kwargs.get("trigger"))

    app.conversations.run_event = fake_run_event
    app.settings.update({"agents.auto_followup": True, "autonomy.max_background_turns_per_day": 1})
    conv = app.conversations.create()

    def report():
        task = app.store.create_task("Build a game", "x", kind="agent", conversation_id=conv["id"])
        app.store.update_task(task["id"], status="completed", summary="Made snake.", finished_at=time.time())
        app.agents._report(app.store.get_task(task["id"]))

    report()
    await drain(5)
    assert started == ["agent_report"] and app.background_turns_today() == 1
    report()  # the daily cap is reached: the report is posted without an extra Codex turn
    await drain(5)
    assert started == ["agent_report"]
    assert any(m["kind"] == "agent_report" for m in app.store.list_messages(conv["id"]))
