import asyncio

import pytest

from weebo.brain.conversation import _error_text, auto_title, item_to_message, sandbox_for, tools_hash
from weebo.codex.rpc import EngineClosed, RpcError
from tests.weebo.conftest import drain

pytestmark = pytest.mark.asyncio


def collect(app, pattern="*"):
    events = []
    app.bus.subscribe(pattern, lambda topic, data: events.append((topic, data)))
    return events


async def test_send_starts_thread_with_tools_and_context(app, fake_engine):
    app.memory.remember("User's favorite color is teal", "preference", 5)
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "What's my favorite color?")
    thread = fake_engine.threads[0]
    assert {t["name"] for t in thread["dynamicTools"]} >= {"remember", "start_agent", "propose_improvement"}
    assert "You are Weebo" in thread["developerInstructions"]
    turn = fake_engine.turns[0]
    assert "teal" in turn["context"] and "Now:" in turn["context"]
    assert turn["approvalPolicy"] == "on-request" and turn["sandboxPolicy"]["type"] == "workspaceWrite"
    conv = app.store.get_conversation(conv["id"])
    assert conv["title"] == "What's my favorite color?" and conv["thread_id"] == thread["id"]


async def test_streaming_turn_is_persisted_and_finished(app, fake_engine):
    events = collect(app)
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "hi")
    turn = fake_engine.turns[0]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Hello there!")
    await drain()
    msgs = app.store.list_messages(conv["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "Hello there!" and msgs[1]["status"] == "done"
    topics = [t for t, _ in events]
    assert "conv.delta" in topics and topics.count("conv.turn.completed") == 1
    assert not app.conversations.busy(conv["id"])


async def test_message_during_turn_steers(app, fake_engine):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "start something long")
    turn = fake_engine.turns[0]
    await fake_engine.emit(turn["thread_id"], "turn/started", {"turn": {"id": turn["turn_id"]}})
    message = await app.conversations.send(conv["id"], "also add tests")
    assert fake_engine.steers and fake_engine.steers[0]["turn_id"] == turn["turn_id"]
    assert message["data"]["steered"] is True
    assert len(fake_engine.turns) == 1


@pytest.mark.parametrize("failure", [RpcError(-32600, "Turn is already completed"),
                                     asyncio.TimeoutError(), EngineClosed("Disconnected")])
async def test_failed_steer_preserves_active_turn(app, fake_engine, monkeypatch, failure):
    events = collect(app)
    conv = app.conversations.create()
    conv_id = conv["id"]
    await app.conversations.send(conv_id, "start something long")
    t = fake_engine.turns[0]
    tid, turn_id = t["thread_id"], t["turn_id"]
    item = {"type": "agentMessage", "id": "partial", "text": "", "phase": "final_answer"}
    await fake_engine.emit(tid, "item/started", {"turnId": turn_id, "item": item})
    await fake_engine.emit(tid, "item/agentMessage/delta", {"turnId": turn_id, "itemId": "partial", "delta": "Before "})
    original = app.conversations.session(conv_id).turn

    async def fail_steer(*args):
        # Notifications continue arriving while the steering RPC is outstanding.
        await fake_engine.emit(tid, "item/agentMessage/delta", {"turnId": turn_id, "itemId": "partial", "delta": "during "})
        raise failure

    monkeypatch.setattr(fake_engine, "steer", fail_steer)
    with pytest.raises(ValueError, match="Please retry after it finishes"):
        await app.conversations.send(conv_id, "also add tests")
    assert app.conversations.session(conv_id).turn is original
    assert app.conversations.live_state(conv_id)["streams"] == {f"{conv_id}:partial": "Before during "}
    assert app.conversations.busy(conv_id) and len(fake_engine.turns) == 1
    assert not any(topic == "conv.turn.completed" for topic, _ in events)

    await fake_engine.emit(tid, "item/agentMessage/delta", {"turnId": turn_id, "itemId": "partial", "delta": "after"})
    await fake_engine.emit(tid, "turn/completed", {"turn": {"id": turn_id, "status": "completed"}})
    assert not app.conversations.busy(conv_id)
    message = app.store.get_message(f"{conv_id}:partial")
    assert message["content"] == "Before during after" and message["status"] == "done"
    assert message["turn_id"] == turn_id
    assert sum(topic == "conv.turn.completed" for topic, _ in events) == 1


@pytest.mark.parametrize("failure", [RpcError(-32600, "Turn is already completed"), asyncio.TimeoutError()])
async def test_failed_steer_starts_new_turn_after_confirmed_completion(app, fake_engine, monkeypatch, failure):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "start something long")
    t = fake_engine.turns[0]

    async def finish_then_fail(*args):
        await fake_engine.finish_turn(t["thread_id"], t["turn_id"], "Original finished.")
        raise failure

    monkeypatch.setattr(fake_engine, "steer", finish_then_fail)
    message = await app.conversations.send(conv["id"], "also add tests")
    assert not message["data"].get("steered")
    assert len(fake_engine.turns) == 2
    assert fake_engine.turns[1]["input"][0]["text"] == "also add tests"
    assert app.conversations.session(conv["id"]).turn.turn_id == fake_engine.turns[1]["turn_id"]


async def test_late_events_cannot_mutate_or_complete_replacement_turn(app, fake_engine):
    conv = app.conversations.create()
    conv_id = conv["id"]
    await app.conversations.send(conv_id, "first")
    old = fake_engine.turns[0]
    tid, old_id = old["thread_id"], old["turn_id"]
    await fake_engine.finish_turn(tid, old_id)
    await app.conversations.send(conv_id, "second")
    turn = app.conversations.session(conv_id).turn
    events = collect(app)
    messages = app.store.list_messages(conv_id)
    stale_events = [
        ("turn/started", {"turn": {"id": old_id}}),
        ("item/started", {"item": {"id": "stale", "type": "agentMessage"}}),
        ("item/agentMessage/delta", {"itemId": "stale", "delta": "stale text"}),
        ("item/reasoning/summaryTextDelta", {"delta": "stale reasoning"}),
        ("item/reasoning/summaryPartAdded", {}),
        ("item/commandExecution/outputDelta", {"itemId": "stale", "delta": "stale output"}),
        ("item/completed", {"item": {"id": "stale", "type": "agentMessage", "text": "stale final"}}),
        ("turn/plan/updated", {"plan": [{"step": "stale plan", "status": "completed"}]}),
        ("turn/diff/updated", {"diff": "stale diff"}),
        ("turn/completed", {"turn": {"id": old_id, "status": "failed", "error": {"message": "stale error"}}}),
    ]
    for method, params in stale_events:
        await fake_engine.emit(tid, method, {"turnId": old_id, **params})
    assert app.conversations.session(conv_id).turn is turn
    assert turn.turn_id == fake_engine.turns[1]["turn_id"]
    assert not turn.streams and not turn.reasoning and not turn.outputs and not turn.diff and not turn.final_text
    assert app.store.list_messages(conv_id) == messages
    assert not events
    await fake_engine.finish_turn(tid, turn.turn_id, "Replacement finished.")
    assert not app.conversations.busy(conv_id)
    assert app.store.list_messages(conv_id)[-1]["content"] == "Replacement finished."


async def test_late_completion_ignored_while_replacement_start_is_pending(app, fake_engine, monkeypatch):
    conv = app.conversations.create()
    conv_id = conv["id"]
    await app.conversations.send(conv_id, "first")
    old = fake_engine.turns[0]
    await fake_engine.finish_turn(old["thread_id"], old["turn_id"])
    starting, release = asyncio.Event(), asyncio.Event()
    start_turn = fake_engine.start_turn

    async def slow_start(*args, **kwargs):
        starting.set()
        await release.wait()
        return await start_turn(*args, **kwargs)

    monkeypatch.setattr(fake_engine, "start_turn", slow_start)
    pending = asyncio.create_task(app.conversations.send(conv_id, "second"))
    try:
        await asyncio.wait_for(starting.wait(), 1)
        replacement = app.conversations.session(conv_id).turn
        assert replacement.turn_id is None
        await fake_engine.emit(old["thread_id"], "turn/completed",
                               {"turn": {"id": old["turn_id"], "status": "completed"}})
        assert app.conversations.session(conv_id).turn is replacement
    finally:
        release.set()
        await pending
    assert replacement.turn_id == fake_engine.turns[1]["turn_id"]


async def test_work_items_and_diff_become_messages(app, fake_engine):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "run tests")
    t = fake_engine.turns[0]
    tid, turn_id = t["thread_id"], t["turn_id"]
    await fake_engine.emit(tid, "turn/started", {"turn": {"id": turn_id}})
    cmd = {"type": "commandExecution", "id": "c1", "command": "pytest -q", "cwd": "/w", "status": "inProgress",
           "commandActions": [], "aggregatedOutput": None, "exitCode": None, "durationMs": None}
    await fake_engine.emit(tid, "item/started", {"turnId": turn_id, "item": cmd})
    await fake_engine.emit(tid, "item/commandExecution/outputDelta", {"turnId": turn_id, "itemId": "c1", "delta": "1 passed"})
    await fake_engine.emit(tid, "item/completed", {"turnId": turn_id, "item": {**cmd, "status": "completed", "exitCode": 0, "aggregatedOutput": "1 passed"}})
    await fake_engine.emit(tid, "turn/plan/updated", {"turnId": turn_id, "plan": [{"step": "Run tests", "status": "completed"}]})
    await fake_engine.emit(tid, "turn/diff/updated", {"turnId": turn_id, "diff": "diff --git a/x b/x\n+++ b/x.py\n+hello\n-bye\n"})
    await fake_engine.emit(tid, "turn/completed", {"turn": {"id": turn_id, "status": "completed"}})
    kinds = {m["kind"]: m for m in app.store.list_messages(conv["id"])}
    assert kinds["command"]["status"] == "done" and kinds["command"]["data"]["exitCode"] == 0
    assert kinds["plan"]["data"]["steps"][0]["status"] == "completed"
    assert kinds["diff"]["data"]["added"] == 1 and kinds["diff"]["data"]["removed"] == 1


async def test_failed_turn_records_error_and_diagnostic(app, fake_engine):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "do it")
    t = fake_engine.turns[0]
    await fake_engine.emit(t["thread_id"], "turn/completed", {"turn": {"id": t["turn_id"], "status": "failed", "error": {
        "message": '{"type":"error","status":400,"error":{"message":"model not supported"}}'}}})
    errors = [m for m in app.store.list_messages(conv["id"]) if m["kind"] == "error"]
    assert errors and errors[0]["content"] == "model not supported"
    assert app.diagnostics.open_issues()[0]["kind"] == "turn_failed"


async def test_dynamic_tool_call_and_approval_roundtrip(app, fake_engine):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "remember my birthday")
    tid = fake_engine.turns[0]["thread_id"]
    result = await fake_engine.request(tid, "item/tool/call", {"turnId": "t", "callId": "1", "tool": "remember",
                                                              "arguments": {"text": "User's birthday is May 4", "kind": "fact"}})
    assert result["success"] is True and "May 4" in result["contentItems"][0]["text"]

    async def approve_soon():
        for _ in range(50):
            await asyncio.sleep(0.01)
            pending = app.interactions.list()
            if pending:
                app.interactions.resolve(pending[0]["id"], "acceptForSession")
                return

    asyncio.create_task(approve_soon())
    response = await fake_engine.request(tid, "item/commandExecution/requestApproval",
                                         {"turnId": "t", "itemId": "i", "command": "rm -rf build", "reason": "cleanup"})
    assert response == {"decision": "acceptForSession"}
    card = [m for m in app.store.list_messages(conv["id"]) if m["kind"] == "approval"][0]
    assert card["status"] == "acceptForSession" and card["data"]["command"] == "rm -rf build"


async def test_turn_end_expires_pending_approvals(app, fake_engine):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "x")
    t = fake_engine.turns[0]
    await fake_engine.emit(t["thread_id"], "turn/started", {"turn": {"id": t["turn_id"]}})
    waiter = asyncio.create_task(fake_engine.request(t["thread_id"], "applyPatchApproval",
                                                     {"turnId": t["turn_id"], "conversationId": t["thread_id"]}))
    await drain(10)
    await fake_engine.emit(t["thread_id"], "turn/completed", {"turn": {"id": t["turn_id"], "status": "interrupted"}})
    assert await asyncio.wait_for(waiter, 1) == {"decision": "abort"}


async def test_engine_restart_resumes_thread(app, fake_engine):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "one")
    t = fake_engine.turns[0]
    await fake_engine.finish_turn(t["thread_id"], t["turn_id"])
    fake_engine.loaded_threads.clear()  # engine restarted
    await app.conversations.send(conv["id"], "two")
    assert fake_engine.threads[-1].get("resumed") and fake_engine.threads[-1]["id"] == t["thread_id"]


async def test_outdated_tools_start_fresh_thread_with_recap(app, fake_engine):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "remember this chat")
    t = fake_engine.turns[0]
    await fake_engine.finish_turn(t["thread_id"], t["turn_id"], "Got it.")
    app.store.update_conversation(conv["id"], meta={"tools_hash": "stale"})
    fake_engine.loaded_threads.clear()
    await app.conversations.send(conv["id"], "what did I say?")
    assert not fake_engine.threads[-1].get("resumed")
    assert "Recap of this conversation" in fake_engine.turns[-1]["context"]
    assert app.store.get_conversation(conv["id"])["meta"]["tools_hash"] == tools_hash()


async def test_event_turns_queue_behind_active_turn(app, fake_engine):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "busy")
    t = fake_engine.turns[0]
    await app.conversations.run_event(conv["id"], "[Event] agent done", "Agent finished")
    assert len(fake_engine.turns) == 1
    await fake_engine.finish_turn(t["thread_id"], t["turn_id"])
    await drain(20)
    assert len(fake_engine.turns) == 2 and "[Event] agent done" in fake_engine.turns[1]["input"][0]["text"]
    notices = [m for m in app.store.list_messages(conv["id"]) if m["kind"] == "notice"]
    assert notices and notices[0]["content"] == "Agent finished"


async def test_recover_after_restart_marks_stale_messages(app):
    conv = app.conversations.create()
    app.store.add_message(conv["id"], "assistant", "partial", status="streaming")
    app.store.add_message(conv["id"], "event", kind="approval", status="pending")
    app.conversations.recover_after_restart()
    statuses = sorted(m["status"] for m in app.store.list_messages(conv["id"]))
    assert statuses == ["expired", "interrupted"]


async def test_helpers():
    assert auto_title("  **Fix** the `my_file.py` bug please, it keeps crashing on start  ") == "Fix the my_file.py bug please, it keeps…"
    assert auto_title("") == "New chat"
    assert sandbox_for("full", [])[0] == "never"
    assert sandbox_for("cautious", [])[2]["type"] == "readOnly"
    assert _error_text({"message": "plain"}) == "plain"
    kind, role, content, data, status = item_to_message({"type": "dynamicToolCall", "id": "x", "tool": "remember",
                                                         "arguments": {}, "status": "completed", "success": False,
                                                         "contentItems": [{"type": "inputText", "text": "nope"}]})
    assert (kind, status, data["output"]) == ("tool", "failed", "nope")
    assert item_to_message({"type": "userMessage", "id": "u"}) is None


async def test_message_sent_while_thread_is_created_steers(app, fake_engine, monkeypatch):
    """Regression: a second message read the conversation before the lock and started a duplicate turn."""
    original = fake_engine.start_thread

    async def slow_start(**params):
        await asyncio.sleep(0.05)
        return await original(**params)

    monkeypatch.setattr(fake_engine, "start_thread", slow_start)
    conv = app.conversations.create()
    first = asyncio.create_task(app.conversations.send(conv["id"], "start something long"))
    await asyncio.sleep(0.01)  # the first send now holds the lock, waiting for its thread
    second = await app.conversations.send(conv["id"], "and also this")
    await first
    assert len(fake_engine.turns) == 1 and len(fake_engine.threads) == 1
    assert second["data"].get("steered") is True


COMPUTER_USE_ASK = {  # what Codex's Computer Use helper sends before touching an app for the first time
    "serverName": "node_repl", "turnId": "t", "mode": "form", "message": "Allow Codex to use Notepad?",
    "requestedSchema": {"type": "object", "properties": {}},
    "_meta": {"codex_approval_kind": "mcp_tool_call", "connector_id": "computer-use", "connector_name": "Computer Use",
              "persist": ["session", "always"], "riskLevel": "low", "tool_params": {"app": "notepad.exe"},
              "tool_params_display": [{"name": "app", "display_name": "App", "value": "Notepad"}]},
}


async def _answer(app, decision):
    for _ in range(100):
        await asyncio.sleep(0.01)
        pending = app.interactions.list()
        if pending:
            app.interactions.resolve(pending[0]["id"], decision)
            return pending[0]


async def test_computer_use_app_approval_reaches_the_user(app, fake_engine):
    """Regression: Weebo auto-declined every MCP elicitation, so Computer Use said 'not approved to use Notepad'."""
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "type hello in notepad")
    tid = fake_engine.turns[0]["thread_id"]
    asked = asyncio.create_task(_answer(app, "acceptAlways"))
    response = await fake_engine.request(tid, "mcpServer/elicitation/request", COMPUTER_USE_ASK)
    assert response == {"action": "accept", "content": {}, "_meta": {"persist": "always"}}
    summary = await asked
    assert summary["kind"] == "elicitation" and summary["title"] == "Allow Codex to use Notepad?"
    assert summary["server"] == "Computer Use" and summary["details"] == ["App: Notepad"]
    card = [m for m in app.store.list_messages(conv["id"]) if m["kind"] == "approval"][0]
    assert card["status"] == "acceptAlways"

    asyncio.create_task(_answer(app, "accept"))
    once = await fake_engine.request(tid, "mcpServer/elicitation/request", COMPUTER_USE_ASK)
    assert once == {"action": "accept", "content": {}}  # plain Allow: no persistence requested
    asyncio.create_task(_answer(app, "decline"))
    assert (await fake_engine.request(tid, "mcpServer/elicitation/request", COMPUTER_USE_ASK))["action"] == "decline"


async def test_elicitation_edge_cases(app, fake_engine):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "x")
    tid = fake_engine.turns[0]["thread_id"]
    # An app that can't be approved permanently never gets "always", even if asked.
    no_persist = {**COMPUTER_USE_ASK, "_meta": {**COMPUTER_USE_ASK["_meta"], "persist": ["session"]}}
    asyncio.create_task(_answer(app, "acceptAlways"))
    assert await fake_engine.request(tid, "mcpServer/elicitation/request", no_persist) == {"action": "accept", "content": {}}
    # Form fields get sensible values on accept.
    form = {"serverName": "x", "mode": "form", "message": "Confirm?", "requestedSchema": {"type": "object", "properties": {
        "ok": {"type": "boolean"}, "size": {"type": "string", "enum": ["small", "large"]}, "note": {"type": "string", "default": "hi"}}}}
    asyncio.create_task(_answer(app, "accept"))
    answer = await fake_engine.request(tid, "mcpServer/elicitation/request", form)
    assert answer["content"] == {"ok": True, "size": "small", "note": "hi"}
    # Device-verified approvals can only be proven by Codex's own apps: declined without a dead card.
    verify = {"serverName": "x", "mode": "openai/userVerification", "challenge": "c", "description": "d", "title": "t"}
    assert await fake_engine.request(tid, "mcpServer/elicitation/request", verify) == {"action": "decline", "content": None}
    assert app.interactions.list() == []
