"""Weebo 3 Phase 7: the activity registry behind the "now showing" slot."""

import time

import pytest

from weebo.brain.activity_registry import BACKGROUND, REPLY, REQUESTED, WAITING, Entry

from .conftest import drain

pytestmark = pytest.mark.asyncio


def new_bot(app, name="Scout"):
    bot = app.bots.propose({"name": name, "job": "Watches things.", "tier": "fast", "why": "x"})
    return app.bots.approve(bot["id"])


async def test_priority_order_and_fall_back(app, fake_engine):
    main = app.conversations.main_id()
    bot = new_bot(app)
    await app.conversations.run_event(bot["conversation_id"], "routine", "", trigger="routine")
    await app.conversations.send(main, "hi there")
    snap = app.activity.snapshot()
    assert [e["priority"] for e in snap["entries"]] == [REPLY, BACKGROUND]
    assert snap["now"]["words"] == "Writing you a reply" and snap["state"] == "working" and snap["more"] == 1
    # an approval waiting on the user outranks background work, but not the reply being written
    app.bus.publish("interaction.pending", {"interaction": {"id": "ask1", "kind": "command", "command": "make deploy",
                                                             "conversation_id": main}})
    assert [e["priority"] for e in app.activity.snapshot()["entries"]] == [REPLY, WAITING, BACKGROUND]
    thread = app.store.get_conversation(main)["thread_id"]
    await fake_engine.finish_turn(thread, fake_engine.turns[-1]["turn_id"])
    snap = app.activity.snapshot()
    assert snap["now"]["waiting_on_user"] and snap["state"] == "waiting"  # falls back to the next one
    app.bus.publish("interaction.resolved", {"id": "ask1"})
    assert app.activity.snapshot()["now"]["owner_name"] == "Scout"


async def test_work_items_change_the_words_never_the_command(app, fake_engine):
    main = app.conversations.main_id()
    await app.conversations.send(main, "run the tests")
    app.bus.publish("conv.message", {"conversation_id": main, "message": {
        "kind": "command", "status": "running", "role": "tool", "content": "python -m pytest -q",
        "data": {"doing": "Running the tests", "activity": "reviewing"}}})
    now = app.activity.snapshot()["now"]
    assert now["words"] == "Running the tests" and now["kind"] == "reviewing" and "pytest" not in now["display"]


async def test_the_teams_requests_wait_prominently(app):
    bot = new_bot(app)
    await app.interactions.ask("item/commandExecution/requestApproval",
                               {"threadId": "x", "turnId": "t", "command": "make deploy", "cwd": "/tmp"},
                               conversation_id=bot["conversation_id"])
    snap = app.activity.snapshot()
    assert snap["now"]["id"] == "needs-you" and snap["now"]["waiting_on_user"]
    assert snap["now"]["words"] == "1 thing from the team needs you"


async def test_quiet_work_says_so(app):
    app.activity.put(Entry("task:x", "x", "Agent", REQUESTED, "reviewing", "Running the tests"))
    entry = app.activity.entries["task:x"]
    entry.last_event_at = time.time() - 10 * 60
    entry.started_at = time.time() - 12 * 60
    now = app.activity.snapshot()["now"]
    assert now["stale"] and now["display"].startswith("Still running the tests · 12 min")
    entry.last_event_at = time.time() - 60 * 60
    assert "(no word for 1 h 0 min)" in app.activity.snapshot()["now"]["display"]
    app.activity.touch("task:x")
    assert not app.activity.snapshot()["now"]["stale"]


async def test_idle_and_sleeping(app, monkeypatch):
    snap = app.activity.snapshot()
    assert (snap["state"], snap["now"], snap["entries"], snap["more"]) == ("idle", None, [], 0)
    monkeypatch.setattr(app.heartbeat, "quiet", lambda: True)
    assert app.activity.snapshot()["state"] == "sleeping"


async def test_agents_and_dreams_show_up(app):
    app.bus.publish("task.updated", {"task": {"id": "t1", "kind": "agent", "status": "running", "title": "Fix the build",
                                              "conversation_id": app.conversations.main_id()}})
    app.bus.publish("task.progress", {"task_id": "t1", "progress": "Running the tests", "activity": "reviewing"})
    app.bus.publish("weebo.activity", {"activity": "dream", "state": "started"})
    snap = app.activity.snapshot()
    assert snap["now"]["words"] == "Agent: Running the tests" and snap["now"]["priority"] == REQUESTED
    assert snap["entries"][1]["kind"] == "dreaming"
    app.bus.publish("task.updated", {"task": {"id": "t1", "kind": "agent", "status": "completed"}})
    app.bus.publish("weebo.activity", {"activity": "dream", "state": "finished"})
    assert app.activity.snapshot()["state"] == "idle"


async def test_updates_are_pushed_and_listed(app):
    seen = []
    app.bus.subscribe("activity.updated", lambda topic, data: seen.append(data["activity"]))
    app.activity.put(Entry("system:x", "system", "Weebo", BACKGROUND, "syncing", "Syncing your calendar"))
    await drain()
    assert seen and seen[-1]["now"]["words"] == "Syncing your calendar"
    from aiohttp.test_utils import TestClient, TestServer

    from weebo.server.app import TOKEN_KEY, create_app
    web_app = create_app(app)
    client = TestClient(TestServer(web_app))
    await client.start_server()
    try:
        data = await (await client.get("/api/activity", headers={"Host": "127.0.0.1",
                                                                 "X-Weebo-Token": web_app[TOKEN_KEY]})).json()
        assert data["now"]["kind"] == "syncing"
    finally:
        await client.close()
