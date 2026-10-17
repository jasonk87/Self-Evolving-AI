"""Regression checks from the post-release Weebo 3 audit (no live services)."""

import asyncio
import time

import pytest

from weebo.brain.tools import ToolContext, registry
from weebo.connectors.registry import ConnectorError
from weebo.facts import mail_ingest
from weebo.facts.sync import FactBatch

from .test_connectors_growth import manifest, onboard
from .test_daysheet_ideas import add_event, tomorrow_at
from .conftest import drain

pytestmark = pytest.mark.asyncio


def bot(app, name="Scout", **profile):
    row = app.bots.propose({"name": name, "job": "Review example work.", "tier": "fast", **profile})
    return app.bots.approve(row["id"])


def idea(app, kind="reminder", **kwargs):
    fact, _ = app.facts.repository.upsert("commitments", {
        "text": "Review the example deck", "owner": "assistant", "source_ref": "conversation:sample"},
        source="local:commitments")
    return app.ideas.add("commitment", "Review the example deck", "The example deck is ready for a review.",
                         [{"entity": "commitments", "id": fact["id"]}],
                         [{"kind": kind, "params": {"when": "in 2 hours"}}], **kwargs)


async def test_background_connector_test_does_not_launch_unapproved_program(app, monkeypatch):
    row = app.connectors.register(manifest(), actor="chief")
    calls = []

    async def run(ref):
        calls.append(ref)
        return {**row, "status": "tested"}

    monkeypatch.setattr(app.connectors, "test", run)
    ctx = ToolContext(app, "t", "turn", conversation_id=app.conversations.main_id(), trigger="routine")
    result = await registry.call(ctx, "test_connector", {"connector": row["id"]})
    assert not result.success and not calls


async def test_disabling_an_untested_connector_does_not_bypass_onboarding(app):
    row = app.connectors.register(manifest(), actor="chief")
    app.connectors.disable(row["id"], "user")
    with pytest.raises(ConnectorError, match="test"):
        app.connectors.approve(row["id"], "user", {"place_call": "Businesses only"})


async def test_unknown_tools_on_an_approved_connector_are_not_in_scope(app):
    row = await onboard(app)
    row = app.connectors.approve(row["id"], "user", {"place_call": "Businesses only"})
    owner = bot(app, connectors=[row["id"]])
    assert app.connectors.in_scope(owner, "helpline", "lookup")
    assert not app.connectors.in_scope(owner, "helpline", "wire_money")


async def test_connector_assignment_changes_the_thread_identity(app, fake_engine):
    owner = bot(app)
    before = app.bots.tools_hash(owner)
    row = await onboard(app)
    app.connectors.approve(row["id"], "user", {"place_call": "Businesses only"})
    _, owner = app.bots.change(owner["id"], {"connectors": [row["id"]]}, "user", True)
    assigned = app.bots.tools_hash(owner)
    assert assigned != before
    app.connectors.disable(row["id"], "user")
    assert app.bots.tools_hash(owner) != assigned


async def test_bot_background_turns_obey_the_existing_global_budget(app, fake_engine):
    owner = bot(app)
    app.settings.update({"autonomy.max_background_turns_per_day": 0})
    outcome = await app.conversations.run_event(owner["conversation_id"], "Review it", "", trigger="routine")
    assert outcome == "refused" and not fake_engine.turns


async def test_team_cap_is_rechecked_after_concurrent_thread_startup(app, fake_engine, monkeypatch):
    app.settings.update({"bots.max_turns_per_day": 1})
    owners = [bot(app, name=name) for name in ("Alpha", "Beta")]
    original = fake_engine.start_thread
    ready, both = 0, asyncio.Event()

    async def start(**params):
        nonlocal ready
        result = await original(**params)
        ready += 1
        if ready == 2:
            both.set()
        await both.wait()
        return result

    monkeypatch.setattr(fake_engine, "start_thread", start)
    results = await asyncio.gather(*(app.conversations.run_event(b["conversation_id"], "Review it", "",
                                                                  trigger="routine") for b in owners))
    assert sorted(results) == ["refused", "started"]
    assert app.bots.turns_today() == 1 and len(fake_engine.turns) == 1
    assert app.background_turns_today() == 1


async def test_stuck_escalation_is_not_repeated_without_new_progress(app):
    owner = bot(app)
    job = await app.bots.assign_job(owner["id"], "Review an example")
    for _ in range(2):
        app.store.execute("UPDATE bot_jobs SET updated_at=? WHERE id=?", (time.time() - 100000, job["id"]))
        app.bots.check_stuck()
    reports = app.reports.search(bot_id=owner["id"])
    assert len([r for r in reports if r["data"]["source"] == "escalation"]) == 1


async def test_refused_job_is_not_reopened_by_its_blocked_report(app):
    owner = bot(app)
    job = await app.bots.assign_job(owner["id"], "Review an example")
    app.bots.turn_refused(owner, "Budget used", "job", "Review", None, job["id"])
    assert app.bots.get_job(job["id"])["status"] == "failed"


async def test_digest_only_marks_the_reports_it_included_as_delivered(app):
    owner = bot(app)
    reports = [app.reports.file(owner, "done", f"Review {i}") for i in range(23)]
    app.bots.digest()
    pending = app.store.query("SELECT id FROM reports WHERE delivered_at IS NULL")
    assert {r["id"] for r in pending} == {r["id"] for r in reports[20:]}


async def test_new_calendar_event_updates_existing_conflict_rows(app):
    app.daysheet.weather = None
    first = add_event(app, "first", "Review one", tomorrow_at(10), tomorrow_at(11))
    await app.daysheet.build()
    second = add_event(app, "second", "Review two", tomorrow_at(10, 30), tomorrow_at(11, 30))
    await app.daysheet.build()
    rows = {r["event_id"]: r for r in app.daysheet.rows()}
    assert rows[first["id"]]["conflicts"][0]["event_id"] == second["id"]


async def test_day_sheet_does_not_keep_events_outside_the_current_horizon(app):
    app.daysheet.weather = None
    event = add_event(app, "first", "Review one", time.time() + 3 * 86400, time.time() + 3 * 86400 + 3600)
    await app.daysheet.build(7)
    assert app.daysheet.rows()
    await app.daysheet.build(1)
    assert not app.daysheet.rows()
    assert app.facts.repository.get("events", event["id"])  # source history is preserved


async def test_failed_draft_dispatch_does_not_mark_an_idea_done(app, monkeypatch):
    row = idea(app, "draft_email")

    async def failed(*args, **kwargs):
        return "failed"

    monkeypatch.setattr(app.conversations, "run_event", failed)
    with pytest.raises(ValueError, match="start|draft"):
        await app.ideas.do_it(row["id"])
    assert app.ideas.get(row["id"])["status"] == "new"


async def test_expired_idea_cannot_act_without_opening_the_list_first(app):
    row = idea(app, expires_at=time.time() - 1)
    with pytest.raises(ValueError, match="expir|open"):
        await app.ideas.do_it(row["id"])
    assert not app.store.list_reminders()


async def test_idea_rereads_the_source_before_acting(app):
    event = add_event(app, "visit", "Example visit", tomorrow_at(10), tomorrow_at(11))
    row = app.ideas.add("travel", "Example visit", "Leave early for the example visit tomorrow.",
                        [{"entity": "events", "id": event["id"]}],
                        [{"kind": "reminder", "params": {"when": "in 2 hours"}}])

    class ChangedCalendar:
        id, entity = "test:calendar", "events"

        async def fetch(self, cursor):
            return FactBatch([{"external_id": "visit", "status": "cancelled"}], "next")

    app.facts.sync.register(ChangedCalendar())
    with pytest.raises(ValueError, match="changed"):
        await app.ideas.do_it(row["id"])
    assert not app.store.list_reminders()


async def test_unread_email_is_not_automatically_a_reply_commitment():
    record = {"id": "newsletter", "subject": "Example newsletter", "timestamp": 1000, "unread": True}
    assert not mail_ingest.thread_records(record)[0]["needs_reply"]
    assert mail_ingest.thread_records({**record, "needs_reply": True})[0]["needs_reply"]


async def test_raw_structured_email_results_are_ingested_but_failed_results_are_not(app):
    raw = {"id": "thread-a", "subject": "Example", "timestamp": 1000}
    result = {"structuredContent": {"threads": [raw]}, "content": []}
    assert mail_ingest.ingest(app.facts.repository, "app:example", result) == 1
    assert mail_ingest.ingest(app.facts.repository, "app:example", {**result, "isError": True}) == 0


async def test_needs_you_updates_are_pushed_even_without_a_running_turn(app):
    owner = bot(app)
    seen = []
    app.bus.subscribe("activity.updated", lambda topic, data: seen.append(data["activity"]))
    await app.interactions.ask("item/commandExecution/requestApproval", {"command": "make deploy"},
                                conversation_id=owner["conversation_id"])
    await drain()
    assert seen and seen[-1]["now"]["id"] == "needs-you"
    await app.approvals.resolve(app.approvals.open_items()[0]["id"], "deny")
    await drain()
    assert seen[-1]["now"] is None


async def test_approval_activity_does_not_display_shell_commands(app):
    app.bus.publish("interaction.pending", {"interaction": {
        "id": "a", "kind": "command", "command": "curl -H Authorization:example-secret https://example.invalid"}})
    assert "curl" not in app.activity.snapshot()["now"]["words"]
    assert "example-secret" not in app.activity.snapshot()["now"]["words"]


async def test_bot_agent_inherits_the_profile_and_tool_boundary(app, fake_engine):
    from weebo.agents.manager import AgentRun
    owner = bot(app, tools=["start_agent", "recall"], effort="low", autonomy=["read"])
    app.settings.update({"autonomy.level": "full"})

    async def finish(engine, thread, turn, params):
        await engine.finish_turn(thread, turn)

    fake_engine.on_turn = finish
    task = await app.agents.start("Example research", "Review the example", effort="high",
                                   conversation_id=owner["conversation_id"])
    await asyncio.wait_for(app.agents.wait(task["id"]), 5)
    thread = next(t for t in fake_engine.threads if t.get("serviceName") == "weebo-agent")
    turn = next(t for t in fake_engine.turns if t["thread_id"] == thread["id"])
    assert thread["sandbox"] == "read-only" and turn["effort"] == "low"
    assert {t["name"] for t in thread["dynamicTools"]} <= set(app.bots.tool_names(owner))
    response = await app.agents._on_request(AgentRun(task["id"]), "item/tool/call", {
        "tool": "remember", "arguments": {"text": "Not granted", "kind": "fact"}})
    assert response["success"] is False
    assert app.bots.turns_today(owner["id"]) == 1


async def test_bot_cannot_steer_another_bots_agent(app, monkeypatch):
    owner = bot(app, tools=["message_agent"])
    other = bot(app, name="Other")
    task = app.store.create_task("Other work", "Review", conversation_id=other["conversation_id"])
    calls = []

    async def message(*args):
        calls.append(args)

    monkeypatch.setattr(app.agents, "message", message)
    ctx = ToolContext(app, "t", "turn", conversation_id=owner["conversation_id"], bot_id=owner["id"], trigger="job")
    result = await registry.call(ctx, "message_agent", {"task_id": task["id"], "message": "Change your job"})
    assert not result.success and not calls


async def test_bot_agent_is_refused_when_its_budget_is_spent(app, fake_engine):
    owner = bot(app, turns_per_day=1)
    app.bots.count_turn(owner)
    task = await app.agents.start("Review", "Review the example", conversation_id=owner["conversation_id"])
    result = await asyncio.wait_for(app.agents.wait(task["id"]), 5)
    assert result["status"] == "failed" and not fake_engine.turns


async def test_connector_send_is_inside_the_rpc_timeout():
    from weebo.connectors.mcp_client import MCPClient, MCPError
    client = MCPClient("http", url="https://example.invalid", timeout=0.01)
    cancelled = asyncio.Event()

    async def blocked(message):
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.set()

    client._io.send = blocked
    with pytest.raises(MCPError, match="timed out"):
        await asyncio.wait_for(client.request("tools/list"), 0.5)
    assert cancelled.is_set()


async def test_mail_facts_can_be_read_while_fresh_without_claiming_complete_sync(app):
    result = {"structuredContent": {"id": "mail", "subject": "Example", "timestamp": 1000}}
    mail_ingest.ingest(app.facts.repository, "app:example", result)
    queried = await app.facts.query("threads", thread_id="t", turn_id="turn")
    assert len(queried["facts"]) == 1 and not queried["complete"]
    assert queried["facts"][0]["provenance"]["source"] == "app:example"
    app.store.execute("UPDATE facts_provenance SET fetched_at=0")
    queried = await app.facts.query("threads")
    assert not queried["facts"] and queried["unverified"]


async def test_workflow_waits_for_a_chief_step_then_runs_the_next_step(app, fake_engine):
    owner = bot(app)
    workflow = app.growth.propose_workflow("Example", "Review then summarize", [
        {"instructions": "Review the example"}, {"bot": owner["id"], "instructions": "Summarize it"}],
        None, "user")
    app.growth.set_workflow_status(workflow["id"], "active", "user")
    await app.growth.run_workflow(workflow["id"])
    assert app.growth.get_workflow(workflow["id"])["data"].get("current")
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Found the example result.")
    await drain(30)
    jobs = app.bots.jobs(owner["id"])
    assert jobs and "Found the example result." in jobs[0]["data"]["instructions"]


async def test_workflow_cannot_overwrite_a_running_occurrence(app):
    owner = bot(app)
    workflow = app.growth.propose_workflow("Example", "Review", [
        {"bot": owner["id"], "instructions": "Review"}], None, "user")
    app.growth.set_workflow_status(workflow["id"], "active", "user")
    await app.growth.run_workflow(workflow["id"])
    with pytest.raises(ValueError, match="running"):
        await app.growth.run_workflow(workflow["id"])
    assert len(app.bots.jobs(owner["id"])) == 1


async def test_single_chief_workflow_is_not_successful_until_the_turn_finishes(app, fake_engine):
    workflow = app.growth.propose_workflow("Example", "Review", [{"instructions": "Review"}], None, "user")
    app.growth.set_workflow_status(workflow["id"], "active", "user")
    await app.growth.run_workflow(workflow["id"])
    assert app.growth.get_workflow(workflow["id"])["data"].get("current")
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], status="failed")
    await drain(15)
    data = app.growth.get_workflow(workflow["id"])["data"]
    assert not data.get("current") and data["failures"] == 1


async def test_workflow_recovery_does_not_leave_a_run_locked(app):
    workflow = app.growth.propose_workflow("Example", "Review", [{"instructions": "Review"}], None, "user")
    app.growth.set_workflow_status(workflow["id"], "active", "user")
    await app.growth.run_workflow(workflow["id"])
    app.growth.recover_after_restart()
    data = app.growth.get_workflow(workflow["id"])["data"]
    assert not data.get("current") and data["failures"] == 1
    app.growth.recover_after_restart()
    assert app.growth.get_workflow(workflow["id"])["data"]["failures"] == 1


async def test_stopping_a_chat_cancels_its_queued_workflow(app):
    main = app.conversations.main_id()
    await app.conversations.send(main, "Hello")
    workflow = app.growth.propose_workflow("Example", "Review", [{"instructions": "Review"}], None, "user")
    app.growth.set_workflow_status(workflow["id"], "active", "user")
    await app.growth.run_workflow(workflow["id"])
    await app.conversations.interrupt(main)
    await drain(15)
    data = app.growth.get_workflow(workflow["id"])["data"]
    assert not data.get("current") and data["failures"] == 1


async def test_pausing_a_workflow_releases_the_current_occurrence(app):
    workflow = app.growth.propose_workflow("Example", "Review", [{"instructions": "Review"}], None, "user")
    app.growth.set_workflow_status(workflow["id"], "active", "user")
    await app.growth.run_workflow(workflow["id"])
    app.growth.set_workflow_status(workflow["id"], "paused", "user")
    assert not app.growth.get_workflow(workflow["id"])["data"].get("current")


async def test_verbose_mcp_server_cannot_block_on_its_stderr_pipe():
    import sys
    from weebo.connectors.mcp_client import _Stdio
    transport = _Stdio([sys.executable, "-c", "import sys; sys.stderr.write('x'*1000000); "
                        "sys.stderr.flush(); print('{\"id\":1,\"result\":{}}', flush=True)"], None, None)
    await transport.open()
    try:
        response = await asyncio.wait_for(transport.receive(1), 5)
        assert response["result"] == {}
    finally:
        await transport.close()


async def test_connector_template_includes_a_valid_manifest(app):
    import json
    from pathlib import Path
    from weebo.connectors import registry as connectors
    path = Path(connectors.__file__).with_name("template") / "manifest.json"
    example = json.loads(path.read_text(encoding="utf-8"))
    registered = app.connectors.register(example, "user")
    assert registered["status"] == "registered"
    assert {t["name"]: t["risk"] for t in registered["data"]["tools"]} == {"lookup": "read", "start_job": "act"}


async def test_concurrent_idea_clicks_dispatch_only_one_draft(app, monkeypatch):
    row = idea(app, "draft_email")
    dispatched = []

    async def started(*args, **kwargs):
        dispatched.append(args)
        await asyncio.sleep(0.02)
        return "started"

    monkeypatch.setattr(app.conversations, "run_event", started)
    results = await asyncio.gather(app.ideas.do_it(row["id"]), app.ideas.do_it(row["id"]), return_exceptions=True)
    assert len(dispatched) == 1
    assert sum(isinstance(r, ValueError) for r in results) == 1


async def test_idea_reminder_preserves_the_second_occurrence_of_a_dst_hour(app):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    app.settings.update({"user.timezone": "America/Chicago"})
    when = datetime(2037, 11, 1, 1, 30, tzinfo=ZoneInfo("America/Chicago"), fold=1)
    row = idea(app)
    data = row["data"]
    data["actions"][0]["params"] = {"at": when.timestamp()}
    app.store._update("ideas", "id", row["id"], {"data": data})
    await app.ideas.do_it(row["id"])
    assert app.store.list_reminders()[0]["due_at"] == when.timestamp()


async def test_duplicate_workflow_completion_does_not_dispatch_twice(app):
    first, second = bot(app, name="First"), bot(app, name="Second")
    workflow = app.growth.propose_workflow("Example", "Review", [
        {"bot": first["id"], "instructions": "Review"},
        {"bot": second["id"], "instructions": "Summarize"}], None, "user")
    app.growth.set_workflow_status(workflow["id"], "active", "user")
    await app.growth.run_workflow(workflow["id"])
    job = app.bots.jobs(first["id"])[0]
    await asyncio.gather(app.growth.on_job_finished(job, "done", "Example reviewed"),
                         app.growth.on_job_finished(job, "done", "Example reviewed"))
    assert len(app.bots.jobs(second["id"])) == 1


async def test_workflow_routines_have_the_same_minimum_interval(app):
    with pytest.raises(ValueError, match="15 minutes"):
        app.scheduler.add("Example workflow", "in 1 minute", "every 1 minute", action="workflow",
                          meta={"workflow_id": "example"})


async def test_chief_workflow_turn_counts_against_background_cap(app):
    await app.conversations.run_event(app.conversations.main_id(), "Review", "", trigger="workflow")
    assert app.background_turns_today() == 1


@pytest.mark.parametrize("limit", ["autonomy.max_background_turns_per_day", "autonomy.usage_ceiling_percent"])
async def test_chief_workflow_cannot_bypass_background_limits(app, fake_engine, limit):
    app.settings.update({limit: 0})
    outcome = await app.conversations.run_event(app.conversations.main_id(), "Review", "", trigger="workflow")
    assert outcome == "refused" and not fake_engine.turns
