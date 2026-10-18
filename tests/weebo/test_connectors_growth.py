"""Weebo 3 Phases 4 and 6: connector onboarding, per-bot scope, and the growth loop."""

import sys
import time
from pathlib import Path

import pytest

from weebo.brain.tools import ToolContext, registry
from weebo.codex.rpc import RpcError
from weebo.connectors.registry import ConnectorError

from .conftest import drain

pytestmark = pytest.mark.asyncio

SERVER = str(Path(__file__).with_name("fake_mcp_server.py"))
ELICIT = "mcpServer/elicitation/request"


def manifest(*flags, name="Helpline"):
    return {"name": name, "kind": "mcp", "transport": "stdio", "command": [sys.executable, SERVER, *flags],
            "description": "Looks things up and calls businesses.",
            "tools": [{"name": "lookup", "risk": "read", "test_call": {"arguments": {"q": "dentist"}}},
                      {"name": "place_call", "risk": "contact_people"}]}


def ctx(app, trigger="user", **kw):
    return ToolContext(app, "t", "turn-1", conversation_id=app.conversations.main_id(), trigger=trigger, **kw)


def new_bot(app, name="Caller", **profile):
    bot = app.bots.propose({"name": name, "job": "Makes calls.", "tier": "fast", "why": "x", **profile})
    return app.bots.approve(bot["id"])


async def onboard(app, *flags, name="Helpline"):
    row = app.connectors.register(manifest(*flags, name=name), actor="chief")
    return await app.connectors.test(row["id"])


# ---------------------------------------------------------------- onboarding
async def test_register_test_and_risk_gated_approval(app):
    row = await onboard(app)
    assert row["status"] == "tested", row["data"].get("test")
    test = row["data"]["test"]
    assert test["found"] == ["lookup", "place_call"] and test["calls"][0]["text"] == "found dentist"
    card = app.store.get_message(f"connector:{row['id']}")
    assert card["status"] == "pending" and card["kind"] == "connector"
    with pytest.raises(ConnectorError, match="place_call"):  # contacting people needs a rule in the user's words
        app.connectors.approve(row["id"], actor="user")
    approved = app.connectors.approve(row["id"], actor="user", rules={"place_call": "Businesses only, never personal contacts."})
    assert approved["status"] == "approved" and app.store.get_message(card["id"])["status"] == "approved"


async def test_undeclared_tools_fail_the_test(app):
    row = await onboard(app, "--extra")
    assert row["status"] == "failed" and "wire_money" in row["data"]["test"]["error"]
    with pytest.raises(ConnectorError, match="pass its test"):
        app.connectors.approve(row["id"], actor="user")


async def test_failing_read_call_fails_the_test(app):
    row = await onboard(app, "--fail")
    assert row["status"] == "failed"


async def test_approving_a_connector_needs_the_user(app):
    row = await onboard(app)
    blocked = await registry.call(ctx(app, trigger="routine"), "approve_connector",
                                  {"connector": row["id"], "rules": {"place_call": "Businesses only."}})
    assert not blocked.success and app.connectors.get(row["id"])["status"] == "tested"
    ok = await registry.call(ctx(app, trigger="user"), "approve_connector",
                             {"connector": row["id"], "rules": {"place_call": "Businesses only."}})
    assert ok.success


async def test_assigning_a_connector_is_a_gated_bot_change(app):
    row = await onboard(app)
    app.connectors.approve(row["id"], actor="user", rules={"place_call": "Businesses only."})
    bot = new_bot(app)
    outcome, change = app.bots.change(bot["id"], {"connectors": ["Helpline"]}, actor="chief", user_said_yes=False)
    assert outcome == "needs_user" and "new connectors" in change["data"]["reasons"][0]
    assert not app.connectors.in_scope(app.bots.get(bot["id"]), "Helpline")
    app.bots.review_change(change["id"], True, actor="user", user_said_yes=True)
    assert app.connectors.in_scope(app.bots.get(bot["id"]), "helpline")


# ---------------------------------------------------------------- per-bot scope
async def test_connector_requests_outside_a_bots_scope_are_denied(app):
    row = await onboard(app)
    app.connectors.approve(row["id"], actor="user", rules={"place_call": "Businesses only."})
    outsider = new_bot(app, name="Outsider", autonomy=["read", "act", "contact_people"])
    params = {"serverName": "Helpline", "message": "Allow a call?", "mode": "form",
              "_meta": {"connector_name": "Helpline", "tool_name": "place_call"}}
    response = await app.interactions.ask(ELICIT, params, conversation_id=outsider["conversation_id"])
    assert response["action"] == "decline" and not app.approvals.open_items()
    insider = new_bot(app, name="Insider", autonomy=["read"])
    app.bots.change(insider["id"], {"connectors": [row["id"]]}, actor="user", user_said_yes=True)
    response = await app.interactions.ask(ELICIT, params, conversation_id=insider["conversation_id"])
    assert response["action"] == "decline"  # contacting people is a hard gate: it waits for the user
    item = app.approvals.open_items()[0]
    assert item["data"]["action"] == "contact_people" and item["data"]["hard"]


async def test_out_of_scope_connector_use_stops_the_bots_turn(app, fake_engine):
    row = await onboard(app)
    app.connectors.approve(row["id"], actor="user", rules={"place_call": "Businesses only."})
    bot = new_bot(app)
    await app.bots.message(bot["id"], "look it up", actor="user", hops=0)
    thread = app.store.get_conversation(bot["conversation_id"])["thread_id"]
    turn_id = fake_engine.turns[-1]["turn_id"]
    await fake_engine.emit(thread, "turn/started", {"turn": {"id": turn_id}})
    await fake_engine.emit(thread, "item/started", {"turnId": turn_id, "item": {
        "type": "mcpToolCall", "id": "m1", "server": "Helpline", "tool": "place_call", "status": "inProgress"}})
    await drain(10)
    assert (thread, turn_id) in fake_engine.interrupts
    assert "isn't one of its connectors" in app.reports.search(bot_id=bot["id"])[0]["data"]["summary"]


async def test_bot_threads_get_their_connectors_when_codex_supports_it(app, fake_engine):
    row = await onboard(app)
    app.connectors.approve(row["id"], actor="user", rules={"place_call": "Businesses only."})
    bot = new_bot(app)
    app.bots.change(bot["id"], {"connectors": [row["id"]]}, actor="user", user_said_yes=True)
    fake_engine.features.thread_config = True
    await app.conversations.send(bot["conversation_id"], "hi")
    config = fake_engine.threads[-1]["config"]
    assert config["mcp_servers"]["helpline"]["command"] == sys.executable
    args = config["mcp_servers"]["helpline"]["args"]
    assert args[:1] == ["-I"] and Path(args[1]).name == "launch.py"
    assert args[-3:] == ["--", sys.executable, SERVER]
    assert config["mcp_servers"]["helpline"]["env_vars"] == []


async def test_refused_thread_config_degrades_gracefully(app, fake_engine):
    row = await onboard(app)
    app.connectors.approve(row["id"], actor="user", rules={"place_call": "Businesses only."})
    bot = new_bot(app)
    app.bots.change(bot["id"], {"connectors": [row["id"]]}, actor="user", user_said_yes=True)
    fake_engine.features.thread_config = True
    original = fake_engine.start_thread

    async def picky(**params):
        if "config" in params:
            raise RpcError(-32602, "unknown field config")
        return await original(**params)

    fake_engine.start_thread = picky
    await app.conversations.send(bot["conversation_id"], "hi")
    assert fake_engine.threads == [] and app.connectors.config_failed
    assert any("no unprotected retry" in m["content"] for m in app.store.list_messages(bot["conversation_id"]))


async def test_a_connector_that_keeps_failing_is_flagged_to_the_chief(app):
    row = await onboard(app)
    app.connectors.approve(row["id"], actor="user", rules={"place_call": "Businesses only."})
    for _ in range(app.settings.get("connectors.max_failures")):
        app.connectors.record_use("Helpline", ok=False, error="timeout")
    flagged = app.connectors.get(row["id"])
    assert flagged["data"]["flagged"] and flagged["data"]["failures"] == 3
    main = app.conversations.sessions[app.conversations.main_id()]
    assert any("Helpline has failed" in note for note in main.pending_context)


async def test_apps_unavailable_is_reported_not_crashed(app):
    apps = await app.connectors.apps()
    assert apps["available"] is False and apps["reason"]


async def test_apps_are_listed_when_codex_offers_them(app, fake_engine):
    fake_engine.features.apps = True

    async def list_apps():
        return [{"id": "maps", "name": "Maps", "description": "Driving directions", "isAccessible": True}]

    fake_engine.list_apps = list_apps
    assert (await app.connectors.apps())["apps"][0]["id"] == "maps"
    row = app.connectors.register({"name": "Maps", "kind": "app", "app_id": "maps",
                                   "tools": [{"name": "directions", "risk": "read"}]}, actor="chief")
    assert (await app.connectors.test(row["id"]))["status"] == "tested"


# ---------------------------------------------------------------- growth: gaps, workflows, pruning
async def test_recurring_gaps_become_a_proposal(app):
    app.store.kv_set("connectors.apps_cache", [{"id": "maps", "name": "Maps", "description": "Driving directions",
                                                "installUrl": "https://example.invalid/maps"}])
    bot = new_bot(app)
    as_bot = ToolContext(app, "t", "turn", conversation_id=bot["conversation_id"], trigger="job", bot_id=bot["id"])
    first = await registry.call(as_bot, "report_capability_gap", {"need": "driving directions"})
    assert first.success and "1 time" in first.text
    await registry.call(as_bot, "report_capability_gap", {"need": "Driving directions", "detail": "for the 3pm"})
    gap = app.store.query_one("SELECT * FROM capability_gaps")
    assert gap["count"] == 2 and gap["status"] == "proposed"
    assert gap["data"]["option"] == {"kind": "app", "app_id": "maps", "name": "Maps",
                                     "install_url": "https://example.invalid/maps"}
    assert app.store.get_message(f"gap:{gap['id']}")["status"] == "pending"


async def test_gap_build_goes_through_self_evolution(app, monkeypatch):
    proposed = []

    async def propose(title, description, rationale="", source="user", conversation_id=None):
        proposed.append({"title": title, "source": source, "description": description})
        return {"id": "p1", "title": title}

    monkeypatch.setattr(app.evolution, "propose", propose)
    app.growth.log_gap("send faxes")
    gap = app.growth.log_gap("send faxes")
    await app.growth.resolve_gap(gap["id"], "build", actor="user")
    assert proposed and "template" in proposed[0]["description"] and proposed[0]["source"] == "user"


async def test_repeated_requests_propose_a_workflow(app):
    app.growth.note_request("Pull the sales numbers and email me a summary chart")
    app.growth.note_request("Make a report of unrelated gardening tips")
    assert app.growth.note_request("pull the sales numbers and email a summary chart please") is None
    workflow = app.growth.note_request("Pull sales numbers, email me the summary chart")
    assert workflow and workflow["status"] == "proposed"
    assert app.store.get_message(f"workflow:{workflow['id']}")["status"] == "pending"
    assert app.growth.note_request("Pull the sales numbers and email me a summary chart") is None  # not twice


async def test_workflows_run_step_by_step_through_bots(app, fake_engine):
    first, second = new_bot(app, name="Gatherer"), new_bot(app, name="Writer")
    workflow = app.growth.propose_workflow("Weekly digest", "Gather then write",
                                           [{"bot": "Gatherer", "instructions": "Collect this week's numbers."},
                                            {"bot": "Writer", "instructions": "Write them up."}], None, actor="chief")
    with pytest.raises(ValueError):
        await app.growth.run_workflow(workflow["id"])  # not until approved
    app.growth.set_workflow_status(workflow["id"], "active", actor="user")
    await app.growth.run_workflow(workflow["id"])
    job = app.bots.jobs(first["id"])[0]
    assert job["data"]["workflow"]["step"] == 0
    app.reports.file(app.bots.get(first["id"]), "done", "Revenue 42.", job_id=job["id"], hops=1)
    for _ in range(20):
        await drain(3)
        if app.bots.jobs(second["id"]):
            break
    step_two = app.bots.jobs(second["id"])[0]
    assert "Revenue 42." in step_two["data"]["instructions"]
    app.reports.file(app.bots.get(second["id"]), "done", "Written.", job_id=step_two["id"], hops=1)
    await drain(10)
    assert app.growth.get_workflow(workflow["id"])["data"]["runs"] == 1


async def test_scheduled_workflows_need_approval_first(app):
    blocked = await registry.call(ctx(app, trigger="routine"), "propose_workflow",
                                  {"name": "Morning check", "steps": [{"instructions": "Check the overnight builds."}],
                                   "when": "tomorrow 7am", "recurrence": "daily"})
    assert blocked.success
    workflow = app.growth.workflows()[0]
    refused = await registry.call(ctx(app, trigger="routine"), "set_workflow_status",
                                  {"workflow": workflow["id"], "status": "active"})
    assert not refused.success
    ok = await registry.call(ctx(app, trigger="user"), "set_workflow_status",
                             {"workflow": workflow["id"], "status": "active"})
    assert ok.success
    reminder = app.store.get_reminder(app.growth.get_workflow(workflow["id"])["data"]["routine_ids"][0])
    assert reminder["action"] == "workflow" and reminder["meta"]["workflow_id"] == workflow["id"]


async def test_pruning_questions_what_isnt_pulling_its_weight(app):
    bot = new_bot(app, name="Sleepy")
    app.store._update("bots", "id", bot["id"], {"created_at": time.time() - 90 * 86400,
                                               "data": {**bot["data"], "last_turn_at": time.time() - 60 * 86400}})
    row = await onboard(app)
    app.connectors.approve(row["id"], actor="user", rules={"place_call": "Businesses only."})
    app.store._update("connectors", "id", row["id"], {"data": {**app.connectors.get(row["id"])["data"],
                                                              "approved_at": time.time() - 90 * 86400}})
    workflow = app.growth.propose_workflow("Flaky", "x", [{"instructions": "x"}], None, actor="chief")
    app.growth.set_workflow_status(workflow["id"], "active", actor="user")
    app.store._update("workflows", "id", workflow["id"], {"data": {**app.growth.get_workflow(workflow["id"])["data"],
                                                                   "fail_streak": 4}})
    pruned = app.growth.prune()
    assert pruned == {"bots": [bot["id"]], "connectors": [row["id"]], "workflows": [workflow["id"]]}
    assert app.bots.get(bot["id"])["data"]["retire"]["reason"]  # proposed, not retired
    assert app.bots.get(bot["id"])["status"] == "active"
    assert app.growth.prune() == {"bots": [], "connectors": [], "workflows": []}  # asked once


async def test_capabilities_registry_lists_everything(app):
    row = await onboard(app)
    bot = new_bot(app)
    app.connectors.approve(row["id"], actor="user", rules={"place_call": "Businesses only."})
    app.bots.change(bot["id"], {"connectors": [row["id"]]}, actor="user", user_said_yes=True)
    app.growth.log_gap("translate voicemail")
    caps = app.growth.capabilities()
    connector = caps["connectors"][0]
    assert connector["used_by"] == ["Caller"] and connector["risk"] == "contact_people"
    assert caps["skills"] and caps["bots"][0]["name"] == "Caller"
    assert caps["gaps"][0]["data"]["need"] == "translate voicemail"
