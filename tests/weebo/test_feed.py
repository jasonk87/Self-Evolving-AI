"""Feed keeps offers, chosen outcomes, and actual work distinct and source-backed."""
import asyncio
import time

import pytest

from weebo.brain.tools import ToolContext, registry
from weebo.store import Store

from .test_connectors_growth import new_bot
from .test_server import client  # noqa: F401

pytestmark = pytest.mark.asyncio


def memory(app, text="Build a birdhouse with Rowan", kind="project"):
    return app.store.add_memory(text, kind, source="chat:test")


async def test_feed_populates_without_connectors_and_never_auto_accepts(app, fake_engine):
    mem = memory(app)
    await app.ideas.generate()
    feed = app.feed.snapshot()
    assert not feed["goals"] and not feed["tracking"] and not fake_engine.turns
    idea = feed["ideas"][0]
    assert "birdhouse" in idea["data"]["statement"]
    evidence = idea["data"]["evidence"][0]
    assert evidence["id"] == mem["id"] and evidence["source"] == "local:memories"
    assert evidence["fetched_at"] and evidence["version"]
    assert await app.ideas.generate() == []


async def test_start_is_idempotent_durable_and_preserves_source(app, fake_engine):
    mem = memory(app, kind="goal")
    idea = (await app.ideas.generate())[0]
    goal = app.feed.start_idea(idea["id"])
    assert goal["memory_id"] == mem["id"] and goal["idea_id"] == idea["id"]
    assert app.ideas.get(idea["id"])["data"]["goal_id"] == goal["id"]
    assert app.feed.start_idea(idea["id"])["id"] == goal["id"]
    assert app.ideas.weights()[idea["kind"]] == 1.25  # one acceptance, even on a double tap
    assert not fake_engine.turns and not app.store.list_reminders()
    app.feed.update(goal["id"], {"status": "done"})
    assert app.feed.start_idea(idea["id"])["status"] == "done"
    store = Store(app.store.path)
    try:
        assert store.query_one("SELECT * FROM goals")["status"] == "done"
        assert store.get_memory(mem["id"])
    finally:
        store.close()


async def test_v5_migration_preserves_data_and_is_repeatable(tmp_path):
    path = tmp_path / "migration.db"
    store = Store(path)
    mem = store.add_memory("Keep this project", "project")
    store.execute("DROP TABLE goals")
    store.execute("PRAGMA user_version=5")
    store.close()
    for _ in range(2):
        store = Store(path)
        assert store.get_memory(mem["id"])["text"] == "Keep this project"
        assert store.query_one("PRAGMA user_version")["user_version"] == 6
        assert store.query("SELECT * FROM goals") == []
        store.close()


@pytest.mark.parametrize("edit", ["change", "archive", "delete"])
async def test_local_evidence_is_revalidated_before_start_discuss_or_action(app, edit):
    mem = memory(app)
    idea = (await app.ideas.generate())[0]
    if edit == "change":
        app.store.update_memory(mem["id"], text="Build a planter instead")
    elif edit == "archive":
        app.store.update_memory(mem["id"], status="archived")
    else:
        app.store.delete_memory(mem["id"])
    assert app.ideas.list()[0]["stale"]
    with pytest.raises(ValueError, match="changed"):
        app.feed.start_idea(idea["id"])
    assert not app.feed.goals()
    assert app.ideas.get(idea["id"])["status"] == "expired"
    fresh = await app.ideas.generate()
    assert len(fresh) == (1 if edit == "change" else 0)


async def test_memory_ideas_obey_cap_dismissal_and_snooze(app, monkeypatch):
    app.settings.update({"ideas.max_per_day": 1})
    memory(app)
    memory(app, "Learn landscape photography", "goal")
    idea = (await app.ideas.generate())[0]
    assert len(app.ideas.list()) == 1
    app.ideas.snooze(idea["id"])
    assert app.ideas.list() == []
    assert await app.ideas.generate() == []
    future = time.time() + 90000
    monkeypatch.setattr("weebo.ideas.time.time", lambda: future)
    assert app.ideas.list()[0]["id"] == idea["id"]
    app.ideas.not_useful(idea["id"])
    assert app.ideas.list() == []
    assert not [i for i in await app.ideas.generate() if i["key"] == idea["key"]]


async def test_chief_can_cite_actual_user_messages_and_goals_not_invented_evidence(app):
    main = app.conversations.main_id()
    user = app.store.add_message(main, "user", "I'd like to learn to sail.")
    assistant = app.store.add_message(main, "assistant", "Maybe you own a yacht.")
    goal = app.feed.create("Learn to sail")
    for entity, ref in (("messages", user["id"]), ("goals", goal["id"])):
        idea = app.ideas.add(f"learn_{entity}", "Plan a first lesson", "I can help compare beginner sailing lessons.",
                             [{"entity": entity, "id": ref}], [])
        assert idea["data"]["evidence"][0]["source"].startswith("local:")
    for entity, ref in (("messages", assistant["id"]), ("memories", "missing"), ("tables", "goals")):
        with pytest.raises(ValueError, match="evidence|Evidence"):
            app.ideas.add("invented", "No", "This would be an invented factual claim.", [{"entity": entity, "id": ref}], [])


async def test_goal_mutation_requires_user_turn_and_does_not_grant_tools(app):
    from weebo.brain import feed_tools  # noqa: F401
    for trigger in ("routine", "chief", "job", "brief", "bot_report"):
        result = await registry.call(ToolContext(app, "t", trigger=trigger), "goal_save", {"title": "Make a budget"})
        assert not result.success
    assert not app.feed.goals()
    result = await registry.call(ToolContext(app, "t", trigger="user"), "goal_save", {"title": "Make a budget"})
    assert result.success and len(app.feed.goals()) == 1
    assert "goal_save" not in registry.names("bot")


@pytest.mark.parametrize("patch", [{"title": ""}, {"status": "running"}, {"due": float("nan")},
                                    {"due": True}, {"owner": "missing"}, {"tools": ["send"]}, {"details": []}])
async def test_goal_validation(app, patch):
    goal = app.feed.create("A real goal")
    with pytest.raises(ValueError):
        app.feed.update(goal["id"], patch)
    assert app.feed.require(goal["id"])["title"] == "A real goal"


async def test_delegate_uses_existing_jobs_and_refuses_double_tap(app, fake_engine):
    bot = new_bot(app, name="Builder")
    goal = app.feed.create("Build a birdhouse", next_step="Draft a parts list")
    results = await asyncio.gather(app.feed.delegate(goal["id"], bot["id"]),
                                   app.feed.delegate(goal["id"], bot["id"]), return_exceptions=True)
    assert sum(isinstance(r, ValueError) for r in results) == 1
    jobs = app.bots.jobs(bot["id"])
    assert len(jobs) == 1 and jobs[0]["data"]["goal_id"] == goal["id"]
    assert len(fake_engine.turns) == 1
    assert app.conversations.sessions[bot["conversation_id"]].turn.trigger == "job"
    assert app.feed.public_goal(app.feed.require(goal["id"]))["jobs"][0]["id"] == jobs[0]["id"]
    assert not [t for t in app.feed.snapshot()["tracking"] if t["id"] == jobs[0]["id"]]
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Parts list drafted.")
    assert app.feed.require(goal["id"])["status"] == "active"  # job done != goal accomplished


async def test_delegate_honors_budget_and_paused_goal(app, fake_engine):
    bot = new_bot(app, name="Planner", turns_per_day=1)
    goal = app.feed.create("Plan the garden")
    app.feed.update(goal["id"], {"status": "paused"})
    with pytest.raises(ValueError, match="Resume"):
        await app.feed.delegate(goal["id"], bot["id"])
    app.feed.update(goal["id"], {"status": "active"})
    app.store.execute("INSERT INTO bot_usage(bot_id,day,turns) VALUES(?,?,1)", (bot["id"], app.bots._day()))
    with pytest.raises(ValueError, match="budget|turn|cap|limit"):
        await app.feed.delegate(goal["id"], bot["id"])
    assert not fake_engine.turns


async def test_tracking_only_describes_real_scheduled_work(app):
    app.feed.create("Exercise more")
    assert app.feed.snapshot()["tracking"] == []
    reminder = app.scheduler.add("Take a break", "in 1 hour")
    routine = app.scheduler.add("Review open goals", "in 2 hours", action="prompt")
    items = {r["id"]: r for r in app.feed.snapshot()["tracking"]}
    assert items[reminder["id"]]["kind"] == "reminder"
    assert "no background work" in items[reminder["id"]]["detail"]
    assert items[routine["id"]]["kind"] == "routine"
    assert items[routine["id"]]["next_at"] == routine["due_at"]
    app.scheduler.cancel(reminder["id"])
    assert reminder["id"] not in {r["id"] for r in app.feed.snapshot()["tracking"]}


async def test_feed_goal_api_and_auth(client, app):
    assert (await client.post("/api/goals", json={"title": "Not authorized"})).status == 401
    mem = memory(app)
    response = await client.get("/api/feed", headers=client.h)
    assert response.status == 200
    idea = (await response.json())["ideas"][0]
    response = await client.post(f"/api/ideas/{idea['id']}/start", json={}, headers=client.h)
    goal = (await response.json())["goal"]
    assert goal["memory_id"] == mem["id"]
    response = await client.patch(f"/api/goals/{goal['id']}", json={"next_step": "Choose lumber"}, headers=client.h)
    assert (await response.json())["goal"]["data"]["next_step"] == "Choose lumber"
    response = await client.get(f"/api/feed/evidence/memories/{mem['id']}", headers=client.h)
    assert (await response.json())["evidence"]["text"] == mem["text"]
    assert (await client.post("/api/goals", json={"title": ""}, headers=client.h)).status == 400


async def test_bot_list_contains_only_real_latest_chat_preview(app):
    bot = new_bot(app, name="Researcher")
    app.store.add_message(bot["conversation_id"], "user", "Find native plants")
    app.store.add_message(bot["conversation_id"], "assistant", "Three candidates found.")
    app.store.add_message(bot["conversation_id"], "event", "tool output", kind="tool")
    public = app.bots.public(bot)
    assert public["latest_message"]["content"] == "Three candidates found."


async def test_link_existing_work_is_idempotent_and_never_reschedules(app, fake_engine):
    goal = app.feed.create("Make more time to read")
    reminder = app.scheduler.add("Read a chapter", "in 1 hour")
    for _ in range(2):
        app.feed.link(goal["id"], "reminders", reminder["id"])
    public = app.feed.public_goal(app.feed.require(goal["id"]))
    assert len(public["links"]) == 1 and public["links"][0]["item"]["due_at"] == reminder["due_at"]
    assert len(app.store.list_reminders()) == 1 and not fake_engine.turns
    app.feed.update(goal["id"], {"status": "dropped"})
    assert app.store.get_reminder(reminder["id"])["status"] == "pending"
    with pytest.raises(ValueError):
        app.feed.link(goal["id"], "memories", "missing")


async def test_feed_tools_exact_lookup_goals_and_link_scope(app):
    from weebo.brain import feed_tools  # noqa: F401
    mem = memory(app)
    goal = app.feed.create("Read more")
    reminder = app.scheduler.add("Read", "in 1 hour")
    user = ToolContext(app, "t", trigger="user")
    result = await registry.call(user, "feed_source", {"entity": "memories", "id": mem["id"]})
    assert result.success and mem["text"] in result.text and "provenance" in result.text
    assert not (await registry.call(user, "feed_source", {"entity": "events", "id": "e"})).success
    result = await registry.call(user, "goals_list", {})
    assert result.success and goal["id"] in result.text
    args = {"id": goal["id"], "entity": "reminders", "source_id": reminder["id"]}
    assert not (await registry.call(ToolContext(app, "t", trigger="routine"), "goal_link", args)).success
    assert (await registry.call(user, "goal_link", args)).success


async def test_goal_link_survives_interruption_between_dispatch_and_ack(app, monkeypatch):
    bot = new_bot(app, name="Planner")
    goal = app.feed.create("Plan the garden")
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(app.conversations, "run_event", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await app.feed.delegate(goal["id"], bot["id"])
    assert len(app.feed.public_goal(goal)["jobs"]) == 1
    with pytest.raises(ValueError, match="progress"):
        await app.feed.delegate(goal["id"], bot["id"])


async def test_api_cannot_forge_goal_actor(client, app):
    response = await client.post("/api/goals", json={"title": "An outcome", "actor": "chief"}, headers=client.h)
    assert response.status == 400 and not app.feed.goals()


async def test_needs_you_links_actual_cards_and_does_not_approve(app):
    from weebo.brain.interactions import Pending
    bot = app.bots.propose({"name": "Planner", "job": "Helps plan", "tier": "fast", "why": "Example"})
    ask = Pending("ask-test", "question", "item/tool/requestUserInput", {}, "t", "turn",
                  conversation_id=app.conversations.main_id(), message_id="message-test")
    app.interactions.pending[ask.id] = ask
    items = {n["id"]: n for n in app.feed.snapshot()["needs_you"]}
    assert items[bot["id"]]["link"]["card"] == f"bot:{bot['id']}"
    assert items[ask.id]["link"]["message"] == "message-test"
    assert app.bots.get(bot["id"])["status"] == "proposed"
    app.interactions.pending.pop(ask.id)


async def test_paused_routine_never_claims_a_next_run(app):
    r = app.scheduler.add("Review the garden plan", "in 1 hour", action="prompt")
    app.store.update_reminder(r["id"], status="paused")
    item = app.feed.snapshot()["tracking"][0]
    assert item["status"] == "paused" and item["next_at"] is None
    assert "no work will run" in item["detail"]


async def test_opening_feed_never_waits_for_external_enrichment(client, app, monkeypatch):
    memory(app)
    async def unavailable():
        raise AssertionError("Weather/network enrichment must not block opening Feed")
    monkeypatch.setattr(app.daysheet, "build", unavailable)
    response = await client.get("/api/feed", headers=client.h)
    assert response.status == 200 and (await response.json())["ideas"]


async def test_delegation_carries_goal_constraints_blocker_and_deadline(app, fake_engine):
    from datetime import datetime, timezone
    bot = new_bot(app, name="Maker")
    due = time.time() + 86400
    goal = app.feed.create("Build a planter", details="Use only the lumber already in storage.",
                           blocker="Need the balcony measurements first.", next_step="Draft a plan", due=due)
    result = await app.feed.delegate(goal["id"], bot["id"])
    job = result["job"]
    assert goal["data"]["details"] in job["data"]["instructions"]
    assert goal["data"]["blocker"] in job["data"]["instructions"]
    assert job["data"]["deadline"] == datetime.fromtimestamp(due, timezone.utc).isoformat()
    assert goal["data"]["details"] in str(fake_engine.turns[-1]["input"])
    assert job["data"]["deadline"] in str(fake_engine.turns[-1]["input"])
