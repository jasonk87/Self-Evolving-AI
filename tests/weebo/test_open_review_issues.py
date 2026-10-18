"""Regressions for the remaining PR review findings (all Codex calls are fake)."""
import asyncio
from pathlib import Path

import pytest

from tests.weebo.conftest import drain
from weebo.codex import oneshot

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("method", ["start_thread", "ensure_ready"])
async def test_stop_does_not_wait_for_engine_startup(app, fake_engine, monkeypatch, method):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(fake_engine, method, blocked)
    conv_id = app.conversations.main_id()
    app.conversations.add_context(conv_id, "Keep this report for the next turn.")
    sending = asyncio.create_task(app.conversations.send(conv_id, "Hello"))
    await asyncio.wait_for(entered.wait(), 1)
    await app.conversations.interrupt(conv_id)
    await asyncio.wait_for(asyncio.shield(sending), 0.5)
    assert cancelled.is_set() and not app.conversations.busy(conv_id)
    assert not fake_engine.turns
    assert "Keep this report" in str(app.conversations.session(conv_id).pending_context)


async def test_lost_reply_interrupts_the_confirmed_turn(app, fake_engine, monkeypatch):
    async def lost(thread_id, *args, **kwargs):
        await fake_engine.emit(thread_id, "turn/started", {"turn": {"id": "lost-turn"}})
        raise asyncio.TimeoutError("Reply lost")

    monkeypatch.setattr(fake_engine, "start_turn", lost)
    conv_id = app.conversations.main_id()
    await app.conversations.send(conv_id, "Hello")
    thread_id = app.store.get_conversation(conv_id)["thread_id"]
    assert fake_engine.interrupts == [(thread_id, "lost-turn")]
    assert not app.conversations.busy(conv_id)


async def test_chat_name_is_only_recorded_after_success(app, fake_engine, monkeypatch):
    conv_id = app.conversations.main_id()
    conv = app.store.update_conversation(conv_id, thread_id="chat-thread")
    calls = []

    async def name(thread_id, title):
        calls.append((thread_id, title))
        return len(calls) > 1

    monkeypatch.setattr(fake_engine, "set_thread_name", name)
    app.conversations._name_thread_once(conv)
    await drain(10)
    assert not app.store.get_conversation(conv_id)["meta"].get("named_thread")
    app.conversations._name_thread_once(app.store.get_conversation(conv_id))
    await drain(10)
    assert app.store.get_conversation(conv_id)["meta"]["named_thread"] == "chat-thread"
    assert len(calls) == 2


async def test_refused_naming_job_is_retried(app, fake_engine, monkeypatch):
    conv_id = app.conversations.main_id()
    conv = app.store.update_conversation(conv_id, thread_id="chat-thread")
    app._stopping = True
    app.conversations._name_thread_once(conv)
    app._stopping = False
    assert not app.store.get_conversation(conv_id)["meta"].get("named_thread")
    app.conversations._name_thread_once(app.store.get_conversation(conv_id))
    await drain(10)
    assert app.store.get_conversation(conv_id)["meta"]["named_thread"] == "chat-thread"


@pytest.mark.parametrize("kind", ["thought", "review"])
async def test_background_timeout_interrupts_and_releases(app, fake_engine, monkeypatch, kind):
    original_wait = asyncio.wait_for

    async def short_wait(awaitable, timeout):
        return await original_wait(awaitable, 0.01 if timeout == 1200 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", short_wait)
    if kind == "thought":
        with pytest.raises(oneshot.OneShotError):
            await oneshot.ask(fake_engine, "Think", timeout=0.01)
    else:
        result = await app.evolution._codex_review(Path.cwd(), "HEAD", count=False)
        assert result["blocking"] and result["error"]
    thread_id = fake_engine.threads[-1]["id"]
    assert fake_engine.interrupts and fake_engine.interrupts[-1][0] == thread_id
    assert thread_id in fake_engine.released and thread_id not in fake_engine.held


async def test_archiving_rechecks_ownership_after_naming(app, fake_engine, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def naming(*args):
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(fake_engine, "set_thread_name", naming)
    archive = app.threads.put_away("claimed-thread", "Weebo agent · Work")
    await entered.wait()
    app.store.update_conversation(app.conversations.main_id(), thread_id="claimed-thread")
    release.set()
    await archive
    assert "claimed-thread" not in fake_engine.archived


async def test_sweep_retains_failed_archives_for_retry(app, fake_engine, monkeypatch):
    from weebo.brain.threads import PENDING_KEY

    fake_engine.codex_threads = [{"id": "owned-thread", "name": "Weebo · Old", "originator": "weebo"}]

    async def unavailable(thread_id):
        return None

    monkeypatch.setattr(fake_engine, "archive_thread", unavailable)
    await app.threads._sweep_once()
    assert "owned-thread" in app.store.kv_get(PENDING_KEY)


async def test_active_older_chat_is_not_archived(app, fake_engine):
    conv = app.conversations.create("Still in use")
    await app.conversations.send(conv["id"], "Hello")
    thread_id = app.store.get_conversation(conv["id"])["thread_id"]
    await app.threads.put_away(thread_id)
    assert thread_id not in fake_engine.archived


async def test_agent_commentary_never_becomes_a_command_in_status(app):
    from weebo.agents.manager import AgentRun
    from weebo.brain import activity

    task = app.store.create_task("Example", "Work")
    run = AgentRun(task["id"])
    app.agents.runs[task["id"]] = run
    app.agents._on_event(run, "item/completed", {"item": {"type": "agentMessage", "phase": "commentary",
                                                       "text": "```powershell\nRemove-Item something\n```"}})
    assert run.progress == "Working on it"
    assert "Remove-Item" not in app.agents.live(task["id"])["progress"]
    assert activity.describe_tool("mcp", "tool", server="cmd /c erase") == ("Using a connected app", "researching")


async def test_late_background_completion_event_finally_unsubscribes():
    from weebo.codex.engine import CodexEngine
    from weebo.events import EventBus

    engine = CodexEngine(EventBus(), None)
    engine.status = "ready"
    requests = []

    async def request(method, params, **kwargs):
        requests.append((method, params))
        return {}

    engine.request = request
    engine.release_thread("late-thread", finished=False)
    assert "late-thread" in engine._routes
    await engine._on_notification("turn/completed", {"threadId": "late-thread", "turn": {"id": "late"}})
    await drain(10)
    assert requests == [("thread/unsubscribe", {"threadId": "late-thread"})]
    assert "late-thread" not in engine._routes
    await engine.stop()


async def test_cancelled_queued_continuation_puts_its_thread_away(app, fake_engine):
    app.settings.update({"agents.auto_followup": False})
    source = app.store.create_task("Original", "Work", cwd=str(Path.cwd()))
    app.store.update_task(source["id"], status="completed", thread_id="prior-thread")
    fake_engine.status = "starting"  # keep the continuation queued
    queued = await app.agents.start("Follow up", "Continue", continue_from=source["id"])
    await app.agents.stop(queued["id"])
    await drain(5)
    fake_engine.status = "ready"
    app.threads.on_engine_ready()
    await drain(15)
    assert "prior-thread" in fake_engine.archived


async def test_agent_lost_start_reply_is_stopped_before_archive(app, fake_engine, monkeypatch):
    app.settings.update({"agents.auto_followup": False})

    async def lost(thread_id, *args, **kwargs):
        await fake_engine.emit(thread_id, "turn/started", {"turn": {"id": "lost-agent"}})
        raise asyncio.TimeoutError("Reply lost")

    monkeypatch.setattr(fake_engine, "start_turn", lost)
    task = await app.agents.start("Do work", "Check it")
    finished = await app.agents.wait(task["id"], timeout=2)
    await drain(10)
    assert finished["status"] == "failed"
    assert fake_engine.interrupts == [(finished["thread_id"], "lost-agent")]
    assert finished["thread_id"] in fake_engine.archived


async def test_unconfirmed_stop_cannot_be_archived_or_continued(app, fake_engine):
    from weebo.agents.manager import AgentRun
    from weebo.brain.conversation import tools_hash
    task = app.store.create_task("Work", "Check it", cwd=str(Path.cwd()), meta={"tools_hash": tools_hash("agent")})
    task = app.store.update_task(task["id"], status="failed", thread_id="still-running")
    run = AgentRun(task["id"], thread_id="still-running", turn_id="remote-turn", fresh=True)
    app.agents._put_away(run, task)
    await app.threads.put_away("still-running")
    assert "still-running" not in fake_engine.archived
    with pytest.raises(ValueError, match="not confirmed"):
        await app.agents.start("Continue", "Continue it", continue_from=task["id"])
    await fake_engine.emit("still-running", "turn/completed", {"turn": {"id": "remote-turn"}})
    await drain(10)
    assert "still-running" in fake_engine.archived
    assert not app.store.get_task(task["id"])["meta"].get("unsettled_thread")


async def test_thread_running_in_another_client_is_not_archived(app, fake_engine, monkeypatch):
    from weebo.brain.threads import PENDING_KEY

    async def busy(thread_id):
        return False

    monkeypatch.setattr(fake_engine, "thread_idle", busy)
    await app.threads.put_away("externally-active")
    assert "externally-active" not in fake_engine.archived
    assert "externally-active" in app.store.kv_get(PENDING_KEY)


@pytest.mark.parametrize("status, expected", [("active", False), ("idle", True), ("notLoaded", True), ("unknown", False)])
async def test_archive_safety_uses_runtime_status_without_turn_history(status, expected):
    from weebo.codex.engine import CodexEngine
    from weebo.events import EventBus
    engine = CodexEngine(EventBus(), None)

    async def request(method, params, **kwargs):
        assert method == "thread/read" and params == {"threadId": "owned", "includeTurns": False}
        return {"thread": {"status": {"type": status}}}

    engine.request = request
    assert await engine.thread_idle("owned") is expected


async def test_cancelled_background_call_stops_before_unsubscribing(app, fake_engine):
    task = asyncio.create_task(oneshot.ask(fake_engine, "Think"))
    while not fake_engine.turns:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    thread_id = fake_engine.turns[-1]["thread_id"]
    assert fake_engine.interrupts[-1][0] == thread_id
    assert thread_id in fake_engine.released


async def test_cancelled_chat_naming_is_not_saved(app, fake_engine, monkeypatch):
    entered = asyncio.Event()

    async def naming(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(fake_engine, "set_thread_name", naming)
    conv_id = app.conversations.main_id()
    conv = app.store.update_conversation(conv_id, thread_id="thread-name")
    app.conversations._name_thread_once(conv)
    await entered.wait()
    task = next(task for task in app._jobs if task.get_name() == "name-chat")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not app.store.get_conversation(conv_id)["meta"].get("named_thread")


async def test_large_sweep_resumes_after_pages_it_cannot_archive(app, fake_engine, monkeypatch):
    from weebo.brain import threads
    monkeypatch.setattr(threads, "SWEEP_PAGES", 1)
    cursors = []

    async def listing(**params):
        cursors.append(params.get("cursor"))
        if params.get("cursor") is None:
            return {"data": [{"id": "users-thread", "name": "Weebo · Notes", "originator": "codex_desktop"}],
                    "nextCursor": "page-two"}
        return {"data": [{"id": "older-owned", "name": "Weebo · Old work", "originator": "weebo"}]}

    monkeypatch.setattr(fake_engine, "list_threads", listing)
    await app.threads._sweep_once()
    assert not app.store.kv_get(threads.SWEEP_KEY)
    assert app.store.kv_get(threads.SWEEP_CURSOR_KEY) == "page-two"
    await app.threads._sweep_once()
    assert cursors == [None, "page-two"]
    assert fake_engine.archived == {"older-owned"}
    assert app.store.kv_get(threads.SWEEP_KEY) == threads.SWEEP_VERSION
