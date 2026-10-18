"""Weebo keeps the Codex app tidy: follow-up work continues the agent that did the job (same Codex thread), and
threads Weebo is done with are put away in Codex's archive instead of piling up in the user's thread list."""

import asyncio

import pytest

from tests.weebo.conftest import drain
from weebo.brain.conversation import tools_hash
from weebo.brain.threads import SWEEP_KEY, SWEEP_VERSION
from weebo.brain.tools import ToolContext, registry
from weebo.codex.rpc import RpcError

pytestmark = pytest.mark.asyncio


def said(app):
    return ToolContext(app=app, thread_id="t", turn_id="turn-1", conversation_id=app.conversations.main_id(),
                       trigger="user")


async def turn_for(fake_engine, count):
    for _ in range(200):
        if len(fake_engine.turns) >= count:
            return fake_engine.turns[count - 1]
        await asyncio.sleep(0.01)
    raise AssertionError(f"only {len(fake_engine.turns)} turns started")


async def run_agent(app, fake_engine, title, instructions, report, **kwargs):
    count = len(fake_engine.turns) + 1
    task = await app.agents.start(title, instructions, conversation_id=app.conversations.main_id(), **kwargs)
    turn = await turn_for(fake_engine, count)
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], report)
    finished = await app.agents.wait(task["id"], timeout=2)
    await drain(10)
    return finished, turn


@pytest.fixture
def quiet(app):
    app.settings.update({"agents.auto_followup": False})  # no chat turn after each report: fewer turns to track
    return app


# ---------------------------------------------------------------- finished threads are put away
async def test_a_finished_agents_thread_is_named_and_archived(quiet, fake_engine):
    app = quiet
    finished, turn = await run_agent(app, fake_engine, "Compare cabins", "Find the best Tahoe cabin.", "Pine Hollow.")
    assert finished["status"] == "completed" and finished["thread_id"] == turn["thread_id"]
    assert turn["thread_id"] in fake_engine.archived  # out of the Codex app's list (restorable there)


async def test_an_ephemeral_agent_leaves_nothing_to_archive(quiet, fake_engine):
    app = quiet
    finished, turn = await run_agent(app, fake_engine, "Self-audit: memory", "Read the memory code.", "[]",
                                     kind="audit", meta={"ephemeral": True, "tools_scope": "none"})
    assert fake_engine.threads[-1]["ephemeral"] is True
    assert turn["thread_id"] in fake_engine.released and turn["thread_id"] not in fake_engine.archive_log


# ---------------------------------------------------------------- follow-up work continues the same thread
async def test_follow_up_work_continues_the_agents_own_thread(quiet, fake_engine):
    app = quiet
    first, first_turn = await run_agent(app, fake_engine, "Compare cabins", "Find the best Tahoe cabin for July.",
                                        "Pine Hollow, $210/night.")
    thread = first_turn["thread_id"]
    threads_before = len([t for t in fake_engine.threads if not t.get("resumed")])

    result = await registry.call(said(app), "start_agent", {"title": "Book it", "instructions": "Check July 8-15.",
                                                            "continue_agent": first["id"]})
    assert result.success and "picking up where" in result.text
    turn = await turn_for(fake_engine, 2)
    assert turn["thread_id"] == thread  # the same Codex thread, so it remembers the cabins it compared
    assert turn["input"][0]["text"] == "Check July 8-15." and "continues earlier work" not in turn["context"]
    assert len([t for t in fake_engine.threads if not t.get("resumed")]) == threads_before  # no new thread
    assert fake_engine.threads[-1].get("resumed")  # brought back from the archive, then resumed
    second = [t for t in app.store.list_tasks(limit=5) if t["id"] != first["id"]][0]
    assert second["meta"]["continues"] == first["id"] and second["thread_id"] == thread
    assert app.store.get_message(f"agent:{second['id']}")  # its own card; the first one's stays as it was
    assert app.store.get_task(first["id"])["summary"] == "Pine Hollow, $210/night."
    assert "Continues" in app.agents.describe(second["id"])

    await fake_engine.finish_turn(thread, turn["turn_id"], "Booked.")
    assert (await app.agents.wait(second["id"], timeout=2))["status"] == "completed"
    await drain(10)
    assert thread in fake_engine.archived


async def test_continuing_an_agent_that_is_still_working_tells_it_instead(quiet, fake_engine):
    app = quiet
    task = await app.agents.start("Compare cabins", "Find the best Tahoe cabin.", conversation_id=app.conversations.main_id())
    await turn_for(fake_engine, 1)
    result = await registry.call(said(app), "start_agent", {"title": "x", "instructions": "Only lakefront ones.",
                                                            "continue_agent": task["id"]})
    assert result.success and "passed this on" in result.text
    assert fake_engine.steers[-1]["input"][0]["text"] == "Only lakefront ones."
    assert len(app.store.list_tasks(limit=10)) == 1


async def test_one_continuation_at_a_time(quiet, fake_engine):
    app = quiet
    first, _ = await run_agent(app, fake_engine, "Compare cabins", "Find the best Tahoe cabin.", "Pine Hollow.")
    await app.agents.start("Book it", "Book Pine Hollow.", continue_from=first["id"])
    with pytest.raises(ValueError, match="already continuing"):  # two turns at once would collide in one thread
        await app.agents.start("Also", "Check parking.", continue_from=first["id"])


async def test_a_finished_agent_cant_be_messaged_but_can_be_continued(quiet, fake_engine):
    app = quiet
    first, _ = await run_agent(app, fake_engine, "Compare cabins", "Find the best Tahoe cabin.", "Pine Hollow.")
    result = await registry.call(said(app), "message_agent", {"task_id": first["id"], "message": "Book it"})
    assert not result.success and f"continue_agent={first['id']}" in result.text


async def test_a_thread_that_cant_be_reopened_hands_over_to_a_fresh_one(quiet, fake_engine, monkeypatch):
    app = quiet
    first, first_turn = await run_agent(app, fake_engine, "Compare cabins", "Find the best Tahoe cabin.",
                                        "Pine Hollow, $210/night.")

    async def gone(thread_id, **params):
        raise RpcError(-32600, f"no rollout found for thread id {thread_id}")

    monkeypatch.setattr(fake_engine, "resume_thread", gone)
    second = await app.agents.start("Book it", "Check July 8-15.", continue_from=first["id"])
    turn = await turn_for(fake_engine, 2)
    assert turn["thread_id"] != first_turn["thread_id"]
    assert "continues earlier work" in turn["context"] and "Pine Hollow" in turn["context"]  # what it did, for context
    saved = app.store.get_task(second["id"])
    assert saved["thread_id"] == turn["thread_id"] and saved["meta"]["tools_hash"] == tools_hash("agent")


async def test_a_thread_whose_tools_changed_is_not_reopened(quiet, fake_engine):
    app = quiet
    first, first_turn = await run_agent(app, fake_engine, "Compare cabins", "Find the best Tahoe cabin.", "Pine Hollow.")
    meta = {**first["meta"], "tools_hash": "from-an-older-version"}  # an upgrade changed the agent tools since
    app.store.update_task(first["id"], meta=meta)
    await app.agents.start("Book it", "Check July 8-15.", continue_from=first["id"])
    turn = await turn_for(fake_engine, 2)
    assert turn["thread_id"] != first_turn["thread_id"]  # a fresh thread gets today's tools


async def test_putting_a_thread_away_never_races_a_continuation(quiet, fake_engine):
    app = quiet
    task = await app.agents.start("Compare cabins", "Find the best Tahoe cabin.", conversation_id=app.conversations.main_id())
    turn = await turn_for(fake_engine, 1)
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Pine Hollow.")
    await app.agents.wait(task["id"], timeout=2)
    # Continued the moment it finished (the report's follow-up does this), before its thread was put away.
    await app.agents.start("Book it", "Book Pine Hollow.", continue_from=task["id"])
    second = await turn_for(fake_engine, 2)
    assert second["thread_id"] == turn["thread_id"]
    assert turn["thread_id"] not in fake_engine.archive_log  # never archived out from under the continuation


# ---------------------------------------------------------------- the main chat keeps one thread in Codex
async def test_a_replaced_main_chat_thread_is_put_away(app, fake_engine):
    main = app.conversations.main_id()
    old = "thread-old-main"
    app.store.update_conversation(main, thread_id=old, meta={"tools_hash": "before-an-upgrade"})
    await app.conversations.send(main, "Hi")
    await drain(10)
    new = app.store.get_conversation(main)["thread_id"]
    assert new != old and old in fake_engine.archived and new not in fake_engine.archived


async def test_an_archived_main_chat_thread_is_brought_back_not_replaced(app, fake_engine):
    main = app.conversations.main_id()
    app.store.update_conversation(main, thread_id="thread-main", meta={"tools_hash": tools_hash("chat")})
    fake_engine.archived.add("thread-main")  # put away in the Codex app by hand
    await app.conversations.send(main, "Hi")
    await drain(5)
    assert app.store.get_conversation(main)["thread_id"] == "thread-main"
    assert fake_engine.turns[-1]["thread_id"] == "thread-main" and "thread-main" not in fake_engine.archived


async def test_adding_an_api_key_doesnt_start_a_new_chat_thread(monkeypatch):
    before = tools_hash("chat")
    monkeypatch.setenv("OPENWEATHER_API_KEY", "abc123")
    assert tools_hash("chat") == before


# ---------------------------------------------------------------- tidying up what's already there
async def test_weebos_older_threads_are_swept_once(app, fake_engine):
    main = app.conversations.main_id()
    app.store.update_conversation(main, thread_id="thread-main")
    older = app.conversations.create(title="Trip planning")
    app.store.update_conversation(older["id"], thread_id="thread-older-chat")
    done = app.store.create_task("Compare cabins", "x", conversation_id=main)
    app.store.update_task(done["id"], status="completed", thread_id="thread-done-agent")
    busy = app.store.create_task("Fix the build", "x", conversation_id=main)
    app.store.update_task(busy["id"], status="running", thread_id="thread-busy-agent")
    fake_engine.codex_threads = [
        {"id": "thread-main", "name": "Weebo · Weebo"},
        {"id": "thread-replaced-main", "name": "Weebo · Weebo", "originator": "weebo"},
        {"id": "thread-old-agent", "name": "Weebo agent · Build snake game", "originator": "weebo"},
        {"id": "thread-users-own", "name": "Refactor the parser"},  # the user's own Codex work: never touched
        {"id": "thread-busy-agent", "name": "Weebo agent · Fix the build"},
    ]
    app.bus.publish("engine.status", {"status": "ready"})
    for _ in range(100):
        if app.store.kv_get(SWEEP_KEY):
            break
        await asyncio.sleep(0.01)
    assert set(fake_engine.archive_log) == {"thread-older-chat", "thread-done-agent", "thread-replaced-main",
                                            "thread-old-agent"}
    fake_engine.archive_log.clear()
    app.bus.publish("engine.status", {"status": "ready"})  # once is enough
    await drain(10)
    assert fake_engine.archive_log == [] and len(fake_engine.listed) == 1


async def test_an_agent_cut_off_by_a_restart_is_put_away_once_codex_is_ready(app, fake_engine):
    task = app.store.create_task("Fix the build", "x", conversation_id=app.conversations.main_id())
    app.store.update_task(task["id"], status="running", thread_id="thread-cut-off")
    app.store.kv_set(SWEEP_KEY, SWEEP_VERSION)  # already tidied once: only the restart's leftovers
    app.agents.recover_after_restart()
    assert fake_engine.archive_log == []  # Codex isn't up yet at this point of a start
    app.bus.publish("engine.status", {"status": "ready"})
    await drain(10)
    assert fake_engine.archive_log == ["thread-cut-off"]


async def test_a_revision_with_no_session_to_continue_starts_a_fresh_one(quiet, tmp_path):
    app = quiet
    proposal = await app.evolution.propose("Fix value", "Set VALUE correctly.", source="user")
    earlier = app.store.create_task("Evolve: Fix value", "x", kind="evolution", cwd=str(tmp_path))
    app.store.update_task(earlier["id"], status="completed")  # never got a thread
    task = await app.evolution._build_agent(app.store.get_proposal(proposal["id"]), 2, "Fix these problems.",
                                            tmp_path, earlier["id"])
    assert task["kind"] == "evolution" and "continues" not in task["meta"]


# ---------------------------------------------------------------- nothing is left behind when something fails
async def test_a_thread_brought_back_but_not_reopened_is_put_away_again(quiet, fake_engine, monkeypatch):
    app = quiet
    first, first_turn = await run_agent(app, fake_engine, "Compare cabins", "Find the best Tahoe cabin.", "Pine Hollow.")
    old = first_turn["thread_id"]
    assert old in fake_engine.archived

    async def broken(thread_id, **params):
        raise RpcError(-32603, "could not load the session")

    monkeypatch.setattr(fake_engine, "resume_thread", broken)  # un-archived for it, then it won't load
    finished, turn = await run_agent(app, fake_engine, "Book it", "Check July 8-15.", "Booked.",
                                     continue_from=first["id"])
    assert turn["thread_id"] != old and old in fake_engine.archived  # back in the archive, not in the user's list
    assert turn["thread_id"] in fake_engine.archived


async def test_an_archive_cut_short_by_codex_going_down_happens_after_a_restart(quiet, fake_engine, monkeypatch):
    from weebo.brain.threads import PENDING_KEY, ThreadKeeper

    app = quiet

    async def down(thread_id):
        return None  # Codex couldn't be reached

    monkeypatch.setattr(fake_engine, "archive_thread", down)
    await app.threads.put_away("thread-agent", "Weebo agent · Compare cabins")
    assert app.store.kv_get(PENDING_KEY) == {"thread-agent": "Weebo agent · Compare cabins"}
    monkeypatch.undo()
    app.threads = ThreadKeeper(app)  # Weebo restarted before Codex came back
    app.store.kv_set(SWEEP_KEY, SWEEP_VERSION)
    app.bus.publish("engine.status", {"status": "ready"})
    await drain(10)
    assert fake_engine.archive_log == ["thread-agent"] and app.store.kv_get(PENDING_KEY) == {}


async def test_the_sweep_leaves_the_users_copies_of_weebo_threads_alone(app, fake_engine):
    fake_engine.codex_threads = [
        {"id": "thread-weebo", "name": "Weebo agent · Build snake game", "originator": "weebo"},
        {"id": "thread-fork", "name": "Weebo agent · Build snake game", "originator": "weebo",
         "forkedFromId": "thread-weebo"},  # forked in Codex to keep working on it: the user's now
        {"id": "thread-elsewhere", "name": "Weebo · Weebo", "originator": "codex_desktop"},
        {"id": "thread-old-codex", "name": "Weebo · Weebo"},  # unknown owner: never guess from the name
    ]
    app.bus.publish("engine.status", {"status": "ready"})
    for _ in range(100):
        if app.store.kv_get(SWEEP_KEY):
            break
        await asyncio.sleep(0.01)
    assert set(fake_engine.archive_log) == {"thread-weebo"}


async def test_the_sweep_tries_again_when_codex_cant_list_threads(app, fake_engine, monkeypatch):
    from weebo.brain.threads import SWEEP_TRIES, ThreadKeeper

    async def slow(**params):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(fake_engine, "list_threads", slow)
    for attempt in range(1, SWEEP_TRIES + 1):
        app.threads = ThreadKeeper(app)  # a start
        app.threads.on_engine_ready()
        await app.threads._sweep
        assert bool(app.store.kv_get(SWEEP_KEY)) == (attempt == SWEEP_TRIES)  # gives up listing after a few starts


async def test_a_background_thought_that_timed_out_is_stopped_and_released(app, fake_engine):
    from weebo.codex import oneshot

    with pytest.raises(oneshot.OneShotError):
        await oneshot.ask(fake_engine, "Think about it.", timeout=0.05)
    thread = fake_engine.threads[-1]["id"]
    assert fake_engine.interrupts[-1][0] == thread
    assert thread in fake_engine.released and thread not in fake_engine.held


async def test_releasing_a_thread_unsubscribes_only_once_its_turn_is_over():
    from weebo.codex.engine import CodexEngine
    from weebo.events import EventBus

    engine = CodexEngine(EventBus(), None)
    engine.status = "ready"
    sent = []

    async def request(method, params, timeout=None):
        sent.append((method, params["threadId"]))
        return {}

    engine.request = request
    engine.release_thread("thread-running", finished=False)
    engine.release_thread("thread-done")
    await drain(5)
    assert sent == [("thread/unsubscribe", "thread-done")]


async def test_an_agent_from_before_tools_were_recorded_gets_a_fresh_thread(quiet, fake_engine):
    app = quiet
    first, first_turn = await run_agent(app, fake_engine, "Compare cabins", "Find the best Tahoe cabin.", "Pine Hollow.")
    meta = {k: v for k, v in first["meta"].items() if k != "tools_hash"}  # finished before Weebo recorded them
    app.store.update_task(first["id"], meta=meta)
    second = await app.agents.start("Book it", "Check July 8-15.", continue_from=first["id"])
    turn = await turn_for(fake_engine, 2)
    assert turn["thread_id"] != first_turn["thread_id"] and "Pine Hollow" in turn["context"]
    assert app.store.get_task(second["id"])["meta"]["tools_hash"] == tools_hash("agent")
