"""Transport + engine tests against a tiny fake app-server process (no Codex needed)."""

import asyncio
import sys
import textwrap

import pytest

from weebo.codex.engine import CodexEngine, _thread_id_of
from weebo.codex.rpc import EngineClosed, JsonRpcProcess, RpcError
from weebo.events import EventBus

pytestmark = pytest.mark.asyncio

FAKE_SERVER = textwrap.dedent(r'''
    import json, sys
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
    def finish():
        for i in range(5):
            send({"method": "final/delta", "params": {"threadId": "t1", "n": i}})
        send({"method": "turn/completed", "params": {"threadId": "t1"}})
    for line in sys.stdin:
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if method == "echo":
            for i in range(50):
                send({"method": "tick", "params": {"threadId": "t1", "n": i}})
            send({"id": mid, "result": msg.get("params")})
        elif method == "fail":
            send({"id": mid, "error": {"code": -32000, "message": "nope"}})
        elif method == "ask":
            send({"id": 99, "method": "item/tool/call", "params": {"threadId": "t1", "tool": "x"}})
        elif method == "die":
            finish()
            sys.exit(3)
        elif method == "crash":
            sys.exit(3)
        elif mid is not None and method is None:
            send({"method": "answered", "params": {"threadId": "t1", "result": msg.get("result")}})
    finish()
''')


@pytest.fixture
def server_script(tmp_path):
    path = tmp_path / "fake_server.py"
    path.write_text(FAKE_SERVER)
    return str(path)


async def test_requests_notifications_and_ordering(server_script):
    rpc = JsonRpcProcess([sys.executable, server_script])
    seen = []
    rpc.on_notification(lambda method, params: seen.append(params.get("n")))
    await rpc.start()
    try:
        assert await rpc.request("echo", {"a": 1}) == {"a": 1}
        await asyncio.sleep(0.05)
        assert seen == list(range(50))
        with pytest.raises(RpcError, match="nope"):
            await rpc.request("fail")
    finally:
        await rpc.stop()


async def test_server_requests_are_answered(server_script):
    rpc = JsonRpcProcess([sys.executable, server_script])
    answered = asyncio.Event()
    results = []

    async def handler(method, params):
        return {"handled": method}

    def notification(method, params):
        if method == "answered":
            results.append(params["result"])
            answered.set()

    rpc.on_request(handler)
    rpc.on_notification(notification)
    await rpc.start()
    try:
        await rpc.notify("ask")
        await asyncio.wait_for(answered.wait(), 5)
        assert results == [{"handled": "item/tool/call"}]
    finally:
        await rpc.stop()


async def test_process_exit_fails_pending_requests(server_script):
    rpc = JsonRpcProcess([sys.executable, server_script])
    await rpc.start()
    with pytest.raises(EngineClosed):
        await rpc.request("die", timeout=5)
    await asyncio.wait_for(rpc.closed.wait(), 5)
    assert rpc.returncode == 3
    with pytest.raises(EngineClosed):
        await rpc.request("echo")


@pytest.mark.parametrize("shutdown", ["exit", "stop", "crash"])
async def test_teardown_awaits_blocked_tool_and_preserves_terminal_notifications(server_script, shutdown):
    rpc = JsonRpcProcess([sys.executable, server_script])
    started = asyncio.Event()
    finished = asyncio.Event()
    release = asyncio.Event()
    continued = []
    seen = []

    async def handler(method, params):
        assert method == "item/tool/call"
        started.set()
        try:
            await release.wait()
            continued.append(method)
        finally:
            await asyncio.sleep(0.01)
            finished.set()

    async def notification(method, params):
        await asyncio.sleep(0.01)
        seen.append((method, params.get("n")))

    rpc.on_request(handler)
    rpc.on_notification(notification)
    await rpc.start()
    pending = asyncio.create_task(rpc.request("ask", timeout=None))
    try:
        await asyncio.wait_for(started.wait(), 5)
        tasks = [*rpc._tasks, *rpc._handler_tasks]
        if shutdown != "stop":
            await rpc.notify("crash" if shutdown == "crash" else "die")
            await asyncio.wait_for(rpc.closed.wait(), 5)
        else:
            await asyncio.wait_for(asyncio.gather(rpc.stop(), rpc.stop()), 5)
        with pytest.raises(EngineClosed):
            await pending
        assert all(task.done() for task in tasks)
        assert finished.is_set()
        assert not rpc._handler_tasks and not rpc._pending
        assert rpc._teardown_task.done()
        expected = [] if shutdown == "crash" else [("final/delta", i) for i in range(5)] + [("turn/completed", None)]
        assert seen == expected
        release.set()
        await asyncio.sleep(0)
        assert continued == []
        await rpc.stop()  # repeated stop also waits for the same teardown
    finally:
        await rpc.stop()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("shutdown", ["exit", "stop"])
async def test_teardown_cancels_a_blocked_notification_worker(server_script, shutdown):
    rpc = JsonRpcProcess([sys.executable, server_script])
    started = asyncio.Event()
    finished = asyncio.Event()

    async def notification(method, params):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)
            finished.set()

    rpc.on_notification(notification)
    await rpc.start()
    try:
        await rpc.request("echo")
        await asyncio.wait_for(started.wait(), 5)
        tasks = list(rpc._tasks)
        if shutdown == "exit":
            await rpc.notify("die")
            await asyncio.wait_for(rpc.closed.wait(), 5)
        else:
            await asyncio.wait_for(rpc.stop(), 5)
        assert finished.is_set()
        assert all(task.done() for task in tasks)
    finally:
        await rpc.stop()


async def test_engine_restart_does_not_keep_old_tool_handlers(server_script, monkeypatch):
    engine = CodexEngine(EventBus())
    started = asyncio.Event()
    finished = asyncio.Event()
    restarted = asyncio.Event()
    release = asyncio.Event()
    continued = []
    transports = []

    async def handler(method, params):
        generation = engine.generation
        started.set()
        try:
            await release.wait()
            continued.append(generation)
            return {"success": True}
        finally:
            finished.set()

    async def launch():
        rpc = JsonRpcProcess([sys.executable, server_script])
        rpc.on_request(engine._on_request)
        rpc.on_notification(engine._on_notification)
        await rpc.start()
        transports.append(rpc)
        engine.rpc = rpc
        engine.generation += 1
        engine._ready.set()
        if engine.generation == 2:
            restarted.set()

    monkeypatch.setattr(engine, "_launch", launch)
    engine.route("t1", request_handler=handler)
    await engine.start()
    pending = None
    try:
        assert await engine.wait_ready(5)
        old = engine.rpc
        pending = asyncio.create_task(engine.request("ask", timeout=None))
        await asyncio.wait_for(started.wait(), 5)
        tasks = [*old._tasks, *old._handler_tasks]
        await old.notify("die")
        with pytest.raises(EngineClosed):
            await asyncio.wait_for(pending, 5)
        await asyncio.wait_for(restarted.wait(), 5)
        assert engine.rpc is not old and engine.generation == 2
        assert finished.is_set() and all(task.done() for task in tasks)
        assert old._teardown_task.done() and not old._handler_tasks
        release.set()
        await asyncio.sleep(0)
        assert continued == []
        assert await engine.request("echo", {"generation": 2}) == {"generation": 2}
    finally:
        await engine.stop()
        await asyncio.gather(engine._supervisor, return_exceptions=True)
        for rpc in transports:
            await rpc.stop()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)


async def test_engine_defaults_for_unrouted_requests():
    engine = CodexEngine(EventBus())
    assert await engine._on_request("item/commandExecution/requestApproval", {"threadId": "zzz"}) == {"decision": "decline"}
    assert await engine._on_request("execCommandApproval", {"conversationId": "zzz"}) == {"decision": "denied"}
    assert (await engine._on_request("item/tool/call", {"threadId": "zzz"}))["success"] is False
    with pytest.raises(RpcError):
        await engine._on_request("brand/new/method", {"threadId": "zzz"})


async def test_engine_model_helpers():
    engine = CodexEngine(EventBus())
    engine.models = [
        {"id": "a", "isDefault": False, "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]},
        {"id": "b", "isDefault": True, "supportedReasoningEfforts": []},
    ]
    assert engine.default_model() == "b"
    assert engine.default_model("a") == "a"
    assert engine.default_model("not-listed") == "b"
    assert engine.clamp_effort("a", "medium") in ("low", "high")
    assert engine.clamp_effort("a", "xhigh") == "high"
    engine.rate_limits = {"primary": {"usedPercent": 12}, "secondary": {"usedPercent": 40}}
    assert engine.usage_percent() == 40


async def test_thread_id_extraction():
    assert _thread_id_of({"threadId": "a"}) == "a"
    assert _thread_id_of({"conversationId": "b"}) == "b"
    assert _thread_id_of({"thread": {"id": "c"}}) == "c"
    assert _thread_id_of({}) is None


async def test_start_turn_falls_back_to_inline_context(server_script):
    engine = CodexEngine(EventBus())
    engine.status = "ready"
    sent = {}

    class Capture:
        running = True

        async def request(self, method, params=None, timeout=None):
            sent.update(params)
            return {"turn": {"id": "t"}}

    engine.rpc = Capture()
    engine._ready.set()
    engine.features.additional_context = False
    await engine.start_turn("th", [{"type": "text", "text": "hi", "text_elements": []}], context="Now: noon")
    assert sent["input"][0]["text"].startswith("<weebo_context>") and "additionalContext" not in sent
    engine.features.additional_context = True
    await engine.start_turn("th", [{"type": "text", "text": "hi", "text_elements": []}], context="Now: noon")
    assert sent["additionalContext"]["weebo"]["kind"] == "application"


async def test_rerouting_a_thread_never_duplicates_events():
    """Regression: conversations re-route their thread on every turn; events must still arrive once."""
    engine = CodexEngine(EventBus())
    seen = []
    for turn in range(3):
        engine.route("t1", listener=lambda m, p, n=turn: seen.append((n, m)))
    await engine._on_notification("item/agentMessage/delta", {"threadId": "t1", "delta": "hi"})
    assert seen == [(2, "item/agentMessage/delta")]


async def test_conversation_deltas_publish_once_across_turns(app, fake_engine):
    from weebo.codex.engine import CodexEngine as RealEngine
    real = RealEngine(app.bus)
    fake_engine.route = real.route  # use the real routing table
    fake_engine.unroute = real.unroute
    fake_engine.routes = real._routes

    async def emit(thread_id, method, params):
        await real._on_notification(method, {"threadId": thread_id, **params})

    fake_engine.emit = emit
    deltas = []
    app.bus.subscribe("conv.delta", lambda t, d: deltas.append(d["delta"]))
    conv = app.conversations.create()
    for text in ("one", "two", "three"):
        await app.conversations.send(conv["id"], text)
        turn = fake_engine.turns[-1]
        await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], f"reply {text}")
    assert deltas == ["reply one", "reply two", "reply three"]
