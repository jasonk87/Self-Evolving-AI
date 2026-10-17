"""Edge cases around Stop, deleting a chat, shutdown and restarts that the audit fixes left open."""

import asyncio

import pytest

from tests.weebo.conftest import drain
from weebo.codex.rpc import EngineClosed

pytestmark = pytest.mark.asyncio


def hold(fake_engine, monkeypatch, method, fail=None):
    """Make an engine call wait until released (then fail with ``fail``, if given)."""
    entered, release = asyncio.Event(), asyncio.Event()
    original = getattr(fake_engine, method)

    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        if fail:
            raise fail
        return await original(*args, **kwargs)

    monkeypatch.setattr(fake_engine, method, held)
    return entered, release


async def test_a_stopped_start_that_then_fails_is_stopped_not_failed(app, fake_engine, monkeypatch):
    conv_id = app.conversations.main_id()
    entered, release = hold(fake_engine, monkeypatch, "start_thread", fail=EngineClosed("Codex engine is still starting"))
    sending = asyncio.create_task(app.conversations.send(conv_id, "Hello"))
    await asyncio.wait_for(entered.wait(), 2)
    assert await app.conversations.interrupt(conv_id)
    release.set()
    await asyncio.wait_for(sending, 2)
    kinds = [m["kind"] for m in app.store.list_messages(conv_id)]
    assert "error" not in kinds and not app.conversations.busy(conv_id)  # no "could not start" for a Stop


async def test_a_message_sent_after_stop_waits_for_its_own_turn(app, fake_engine, monkeypatch):
    conv_id = app.conversations.main_id()
    sending = asyncio.create_task(app.conversations.send(conv_id, "First"))
    for _ in range(100):
        if fake_engine.turns:
            break
        await asyncio.sleep(0.01)
    session = app.conversations.session(conv_id)
    session.turn.cancelled = True  # Stop pressed; the turn hasn't reported back as finished yet
    await asyncio.wait_for(sending, 2)
    second = await app.conversations.send(conv_id, "Second")
    assert second["data"].get("queued") and fake_engine.steers == []  # not steered into the ending turn


async def test_deleting_a_chat_during_start_runs_no_turn_for_it(app, fake_engine, monkeypatch):
    older = app.conversations.create(title="Trip planning")
    entered, release = hold(fake_engine, monkeypatch, "start_thread")
    sending = asyncio.create_task(app.conversations.send(older["id"], "Hello"))
    await asyncio.wait_for(entered.wait(), 2)
    await app.conversations.delete(older["id"])
    release.set()
    await asyncio.wait_for(sending, 2)
    await drain(10)
    assert fake_engine.turns == []
    thread = fake_engine.threads[-1]["id"]
    assert thread not in fake_engine.routes and thread in fake_engine.archived  # put away, not left behind


async def test_stopping_before_start_keeps_what_the_next_turn_should_know(app, fake_engine, monkeypatch):
    conv_id = app.conversations.main_id()
    app.conversations.add_context(conv_id, "Agent t_1 finished; its report is in the chat.")
    entered, release = hold(fake_engine, monkeypatch, "start_thread")
    event = asyncio.create_task(app.conversations.run_event(conv_id, "Tell the user", "", trigger="agent_report",
                                                            fallback="Agent t_2 finished too."))
    await asyncio.wait_for(entered.wait(), 2)
    assert await app.conversations.interrupt(conv_id)
    release.set()
    assert await asyncio.wait_for(event, 2) == "interrupted"
    pending = app.conversations.session(conv_id).pending_context
    assert any("t_1" in c for c in pending) and any("t_2" in c for c in pending)


async def test_no_queued_agent_starts_while_weebo_is_stopping(app, fake_engine):
    app.settings.update({"agents.max_parallel": 1, "agents.auto_followup": False})
    first = await app.agents.start("First", "Work.", conversation_id=app.conversations.main_id())
    second = await app.agents.start("Second", "Work.", conversation_id=app.conversations.main_id())
    for _ in range(100):
        if fake_engine.turns:
            break
        await asyncio.sleep(0.01)
    app._stopping = True  # app.stop() has begun; agents.shutdown() hasn't run yet
    try:
        turn = fake_engine.turns[0]
        await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "Done.")
        await app.agents.wait(first["id"], timeout=2)
        await drain(10)
        assert app.store.get_task(second["id"])["status"] == "queued"  # still queued for the next start
        assert len(fake_engine.turns) == 1
    finally:
        app._stopping = False


async def test_a_message_waiting_when_weebo_stopped_is_not_left_hanging(app):
    conv_id = app.conversations.main_id()
    message = app.store.add_message(conv_id, "user", "Also check the weather", data={"queued": True})
    app.conversations.recover_after_restart()
    saved = app.store.get_message(message["id"])
    assert "queued" not in saved["data"] and saved["data"]["delivery"] == "dropped"
    assert "Send it again" in app.store.list_messages(conv_id)[-1]["content"]
