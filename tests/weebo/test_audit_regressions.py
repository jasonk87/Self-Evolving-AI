"""Failure cases found while auditing the October Weebo release."""

import asyncio
import threading

import pytest

from tests.weebo.conftest import drain
from weebo.config import Settings, SettingsError


@pytest.mark.asyncio
async def test_cancelled_approval_expires_persisted_card(app):
    conv_id = app.conversations.main_id()
    resolved = []
    app.bus.subscribe("interaction.resolved", lambda _, data: resolved.append(data))
    ask = asyncio.create_task(app.interactions.ask(
        "item/commandExecution/requestApproval",
        {"threadId": "thread-approval", "turnId": "turn-approval", "command": "echo hello"},
        conversation_id=conv_id,
    ))
    await drain()
    message_id = app.interactions.list()[0]["message_id"]
    ask.cancel()  # The RPC transport cancels request handlers when the engine exits.
    await asyncio.gather(ask, return_exceptions=True)
    assert app.interactions.list() == []
    assert app.store.get_message(message_id)["status"] == "expired"
    assert len(resolved) == 1 and resolved[0]["status"] == "expired"


@pytest.mark.asyncio
@pytest.mark.parametrize("finished_by", ["turn", "task"])
async def test_finishing_work_does_not_overwrite_an_accepted_approval(app, finished_by):
    ask = asyncio.create_task(app.interactions.ask(
        "item/commandExecution/requestApproval",
        {"threadId": "thread-approval", "turnId": "turn-approval"},
        conversation_id=app.conversations.main_id(), task_id="task-approval",
    ))
    await drain()
    pending = app.interactions.list()[0]
    assert app.interactions.resolve(pending["id"], "accept")
    if finished_by == "turn":
        app.interactions.cancel_turn("thread-approval", "turn-approval")
    else:
        app.interactions.cancel_task("task-approval")
    assert await ask == {"decision": "accept"}
    assert app.store.get_message(pending["message_id"])["status"] == "accept"


@pytest.mark.asyncio
async def test_stop_during_agent_start_interrupts_the_accepted_turn(app, fake_engine, monkeypatch):
    app.settings.update({"agents.auto_followup": False})
    starting, release = asyncio.Event(), asyncio.Event()
    start_turn = fake_engine.start_turn

    async def delayed_start(*args, **kwargs):
        starting.set()
        await release.wait()
        return await start_turn(*args, **kwargs)

    monkeypatch.setattr(fake_engine, "start_turn", delayed_start)
    task = await app.agents.start("Stop before acknowledgement", "Do some work")
    await asyncio.wait_for(starting.wait(), 2)
    await app.agents.stop(task["id"])
    release.set()
    finished = await app.agents.wait(task["id"], timeout=2)
    assert finished["status"] == "cancelled"
    turn = fake_engine.turns[0]
    assert (turn["thread_id"], turn["turn_id"]) in fake_engine.interrupts
    await drain()


def test_failed_settings_save_does_not_change_live_configuration(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "settings.json")
    settings.update({"autonomy.level": "cautious"})
    notifications = []
    settings.on_change(notifications.append)

    def disk_full(data):
        raise OSError("Disk full")

    monkeypatch.setattr(settings, "_save", disk_full)
    with pytest.raises(OSError, match="Disk full"):
        settings.update({"autonomy.level": "full"})
    assert settings.get("autonomy.level") == "cautious"
    assert notifications == []
    assert Settings(tmp_path / "settings.json").get("autonomy.level") == "cautious"


@pytest.mark.parametrize("value", [float("inf"), 3.5, True])
def test_integer_settings_do_not_accept_invalid_numbers(tmp_path, value):
    settings = Settings(tmp_path / "settings.json")
    with pytest.raises(SettingsError):
        settings.update({"agents.max_parallel": value})


@pytest.mark.asyncio
async def test_shutdown_settles_thread_archiving_before_closing_storage(app, fake_engine, monkeypatch):
    from weebo.brain.threads import PENDING_KEY

    entered = asyncio.Event()
    never = asyncio.Event()

    async def slow_archive(thread_id):
        entered.set()
        await never.wait()

    monkeypatch.setattr(fake_engine, "archive_thread", slow_archive)
    archive = app.threads.put_away("thread-finished", "Weebo agent · Finished work")
    await asyncio.wait_for(entered.wait(), 2)
    closed_with_pending = []
    # Observe the close boundary but leave fixture-owned storage open for assertions.
    close = app.store.close
    monkeypatch.setattr(app.store, "close", lambda: closed_with_pending.append(not archive.done()))
    try:
        await app.stop()
        assert closed_with_pending == [False]
        assert app.store.kv_get(PENDING_KEY) == {"thread-finished": "Weebo agent · Finished work"}
    finally:
        archive.cancel()
        await asyncio.gather(archive, return_exceptions=True)
        monkeypatch.setattr(app.store, "close", close)


@pytest.mark.asyncio
async def test_agent_pump_cannot_start_work_after_shutdown(app, fake_engine):
    fake_engine.status = "starting"
    task = await app.agents.start("Waiting", "Wait for the engine")
    await app.agents.shutdown()
    fake_engine.status = "ready"
    app.agents._pump()
    try:
        assert app.agents.runs == {}
        assert app.store.get_task(task["id"])["status"] == "queued"
    finally:
        await app.agents.shutdown()


@pytest.mark.asyncio
async def test_agent_wait_timeout_removes_the_waiter(app, fake_engine):
    fake_engine.status = "starting"
    task = await app.agents.start("Queued", "Wait for the engine")
    with pytest.raises(asyncio.TimeoutError):
        await app.agents.wait(task["id"], timeout=0.001)
    assert app.agents._waiters == {}


@pytest.mark.asyncio
async def test_wait_for_missing_agent_fails_without_hanging(app):
    with pytest.raises(ValueError, match="No agent"):
        await app.agents.wait("missing", timeout=0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "x", "x" * 1201, 123, ["not text"]])
async def test_memory_edit_obeys_the_same_text_rules_as_creation(app, text):
    from weebo.memory.memory import MemoryError_

    memory, _ = app.memory.remember("The user enjoys walking in the morning")
    with pytest.raises(MemoryError_):
        app.memory.update(memory["id"], text=text)
    assert app.store.get_memory(memory["id"])["text"] == memory["text"]


@pytest.mark.asyncio
async def test_remembering_existing_memory_can_pin_it(app):
    memory, _ = app.memory.remember("The user enjoys walking in the morning")
    pinned, action = app.memory.remember(memory["text"], pinned=True)
    assert action == "updated" and pinned["id"] == memory["id"]
    assert pinned["pinned"]


@pytest.mark.asyncio
@pytest.mark.parametrize("component_name,task_attr", [
    ("scheduler", "_task"), ("heartbeat", "_task"), ("evolution", "_worker"),
    ("evolution", "_restart_task"),
])
async def test_background_stop_waits_for_owned_jobs(app, component_name, task_attr):
    component = getattr(app, component_name)
    started, cleaned_up = asyncio.Event(), asyncio.Event()

    async def job():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned_up.set()

    task = asyncio.create_task(job())
    setattr(component, task_attr, task)
    await started.wait()
    try:
        await component.stop()
        assert task.done() and cleaned_up.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["thread", "turn"])
async def test_chat_stop_during_start_never_leaves_a_remote_turn_running(app, fake_engine, monkeypatch, stage):
    conv_id = app.conversations.main_id()
    entered, release = asyncio.Event(), asyncio.Event()
    method = "start_thread" if stage == "thread" else "start_turn"
    original = getattr(fake_engine, method)

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(fake_engine, method, delayed)
    sending = asyncio.create_task(app.conversations.send(conv_id, "Start then stop"))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        assert await app.conversations.interrupt(conv_id)
        release.set()
        await asyncio.wait_for(sending, 2)
        if stage == "thread":
            assert fake_engine.turns == []
        else:
            turn = fake_engine.turns[0]
            assert (turn["thread_id"], turn["turn_id"]) in fake_engine.interrupts
        assert not app.conversations.busy(conv_id)
    finally:
        release.set()
        await asyncio.gather(sending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["warm", "embed"])
async def test_slow_embedding_worker_does_not_touch_storage_after_stop(app, monkeypatch, phase):
    from weebo.memory.memory import Memory

    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    late_access = []

    class SlowEmbedder:
        name = "audit-model"

        def warm(self):
            if phase == "warm":
                entered.set()
                release.wait(5)

        def embed(self, texts):
            if phase == "embed":
                entered.set()
                release.wait(5)
            return [[1.0, 0.0] for _ in texts]

    for method in ("memory_vectors", "memories_needing_vectors", "set_memory_vector"):
        original = getattr(app.store, method)

        def observe(*args, _method=method, _original=original, **kwargs):
            if stopped.is_set():
                late_access.append(_method)
            return _original(*args, **kwargs)

        monkeypatch.setattr(app.store, method, observe)
    memory = Memory(app.store, app.bus, embedder=SlowEmbedder())
    memory.remember("The user enjoys walking in the morning")
    memory.start_indexing(True)
    worker = memory._worker
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        memory.stop(timeout=0.001)
        stopped.set()
        release.set()
        await asyncio.to_thread(worker.join, 2)
        assert not worker.is_alive()
        assert late_access == []
    finally:
        release.set()
        await asyncio.to_thread(worker.join, 2)


@pytest.mark.asyncio
async def test_cancelling_a_git_helper_terminates_and_reaps_its_child(tmp_path, monkeypatch):
    from weebo.evolution import git

    entered = asyncio.Event()

    class Process:
        returncode = None
        killed = False
        reaped = False

        async def communicate(self):
            entered.set()
            if not self.killed:
                await asyncio.Event().wait()
            self.reaped = True
            return b"", b""

        def kill(self):
            self.killed = True
            self.returncode = -9

    proc = Process()

    async def spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    running = asyncio.create_task(git.run(["git", "status"], tmp_path))
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert proc.killed and proc.reaped


@pytest.mark.asyncio
async def test_shutdown_settles_async_event_subscribers_before_storage_closes(app, monkeypatch):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def slow_sync():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    monkeypatch.setattr(app.skills, "sync_with_codex", slow_sync)
    app.bus.publish("engine.status", {"status": "ready"})
    await entered.wait()
    closed = []
    close = app.store.close
    monkeypatch.setattr(app.store, "close", lambda: closed.append(cleaned.is_set()))
    try:
        await app.stop()
        assert closed == [True]
        assert not app.bus._tasks
    finally:
        tasks = list(app.bus._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        monkeypatch.setattr(app.store, "close", close)


@pytest.mark.asyncio
async def test_shutdown_settles_a_settings_triggered_engine_restart(app, fake_engine, monkeypatch):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def slow_restart():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    monkeypatch.setattr(fake_engine, "restart", slow_restart)
    app.settings.update({"codex.binary": "audit-codex"})
    await entered.wait()
    jobs = [t for t in asyncio.all_tasks() if t.get_coro().__name__ == "slow_restart"]
    close = app.store.close
    closed = []
    monkeypatch.setattr(app.store, "close", lambda: closed.append(cleaned.is_set()))
    try:
        await app.stop()
        assert closed == [True]
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        monkeypatch.setattr(app.store, "close", close)


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", ["hello", ["hello"], {"q": "hello"}, {"q": [42]}])
async def test_malformed_question_answer_does_not_consume_the_pending_question(app, answers):
    ask = asyncio.create_task(app.interactions.ask(
        "item/tool/requestUserInput", {"threadId": "question-thread", "questions": [{"id": "q", "question": "Which?"}]},
        conversation_id=app.conversations.main_id(),
    ))
    await drain()
    pending = app.interactions.list()[0]
    try:
        with pytest.raises(ValueError, match="answers"):
            app.interactions.resolve(pending["id"], answers=answers)
        assert app.store.get_message(pending["message_id"])["status"] == "pending"
        assert app.interactions.resolve(pending["id"], answers={"q": ["Correct answer"]})
        assert await ask == {"answers": {"q": {"answers": ["Correct answer"]}}}
    finally:
        ask.cancel()
        await asyncio.gather(ask, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["[]", "null", '"text"', '{"pid": "invalid"}', '{"pid": []}', '{"pid": 1e999}'])
async def test_corrupt_tailnet_status_does_not_break_app_status(app, status):
    app.remote.status_path.write_text(status, encoding="utf-8")
    assert not app.remote.status()["running"]
    assert app.remote.owners() == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "timeout"])
async def test_tailscale_probe_reaps_its_child_on_interruption(monkeypatch, failure):
    from weebo import remote

    entered = asyncio.Event()

    class Process:
        killed = False
        reaped = False

        async def communicate(self):
            if self.killed:
                self.reaped = True
                return b"", b""
            entered.set()
            if failure == "timeout":
                raise asyncio.TimeoutError()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True

    proc = Process()

    async def spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(remote, "tailscale_cli", lambda: "audit-tailscale")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(remote.tailscale_owner())
    await entered.wait()
    if failure == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await task is None
    assert proc.killed and proc.reaped


def test_threadsafe_event_after_loop_close_is_ignored():
    from weebo.events import EventBus

    bus = EventBus()
    loop = asyncio.new_event_loop()
    bus.bind_loop(loop)
    loop.close()
    bus.publish_threadsafe("memory.index", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["queued_reply", "manual_activity"])
async def test_shutdown_settles_work_spawned_outside_the_main_loops(app, monkeypatch, source):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def work(*args):
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    if source == "queued_reply":
        monkeypatch.setattr(app.conversations, "_answer_waiting", work)
        conv_id = app.conversations.main_id()
        app.conversations.session(conv_id).waiting = [{"text": "Queued user message"}]
        app.conversations._finish_turn(conv_id, "completed", None)
    else:
        monkeypatch.setattr(app.heartbeat, "dream", work)
        await app.heartbeat.trigger("dream")
    await entered.wait()
    jobs = [t for t in asyncio.all_tasks() if t.get_coro().__name__ in ("work", "_run", "run_trigger")]
    closed = []
    close = app.store.close
    monkeypatch.setattr(app.store, "close", lambda: closed.append(cleaned.is_set()))
    try:
        await app.stop()
        assert closed == [True]
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        monkeypatch.setattr(app.store, "close", close)


@pytest.mark.asyncio
async def test_engine_shutdown_settles_supervisor_and_account_refresh(app, monkeypatch):
    from weebo.codex.engine import CodexEngine

    engine = CodexEngine(app.bus)
    supervisor_entered, refresh_entered = asyncio.Event(), asyncio.Event()
    cleaned = []

    async def supervisor():
        try:
            supervisor_entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.append("supervisor")

    async def refresh():
        try:
            refresh_entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.append("refresh")

    monkeypatch.setattr(engine, "_supervise", supervisor)
    monkeypatch.setattr(engine, "_account_changed", refresh)
    await engine.start()
    await engine._on_notification("account/updated", {})
    await supervisor_entered.wait()
    await refresh_entered.wait()
    jobs = [t for t in asyncio.all_tasks() if t.get_coro().__name__ in ("supervisor", "refresh")]
    try:
        await engine.stop()
        assert set(cleaned) == {"supervisor", "refresh"}
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
