import asyncio
import time

import pytest

from tests.weebo.conftest import drain
from weebo.app import WeeboApp
from weebo.codex.rpc import EngineClosed, RpcError
from weebo.proactive.scheduler import MISSED_GRACE, ROUTINE_MAX_ATTEMPTS
from weebo.store import SCHEMA_VERSION, Store

pytestmark = pytest.mark.asyncio


def due_routine(app, conv_id, recurrence="", due_at=None):
    return app.store.add_reminder("Check the weather", due_at or time.time() - 1, recurrence,
                                  conv_id, "prompt", meta={"original": "metadata"})


def occurrences(app):
    return app.store.query("SELECT * FROM routine_occurrences ORDER BY created_at, rowid")


def routine_journal(app):
    return [r["title"] for r in app.store.query("SELECT * FROM journal WHERE kind='routine' ORDER BY seq")]


async def test_confirmed_start_failure_is_durable_and_retries_with_backoff(app, fake_engine, monkeypatch):
    conv = app.conversations.create()
    reminder = due_routine(app, conv["id"], "daily")
    original_start = fake_engine.start_turn

    async def reject(*args, **kwargs):
        raise RpcError(-32000, "Startup rejected")

    monkeypatch.setattr(fake_engine, "start_turn", reject)
    await app.scheduler.tick()
    occurrence = occurrences(app)[0]
    assert occurrence["status"] == "failed" and occurrence["attempts"] == 1
    assert occurrence["due_at"] == reminder["due_at"]
    assert "Startup rejected" in occurrence["error"] and occurrence["next_attempt_at"] > time.time()
    assert routine_journal(app) == ["Queued a routine"]
    next_due = app.store.get_reminder(reminder["id"])["due_at"]
    await app.scheduler.tick()
    assert occurrences(app)[0] == occurrence

    # Read the persisted failure from a new connection, then recover and retry it.
    reopened = Store(app.store.path)
    assert reopened.get_routine(occurrence["id"]) == occurrence
    reopened.close()
    app.scheduler.recover_after_restart()
    monkeypatch.setattr(fake_engine, "start_turn", original_start)
    app.store.update_routine(occurrence["id"], next_attempt_at=0)
    await app.scheduler.tick()
    saved = occurrences(app)[0]
    assert saved["status"] == "started" and saved["attempts"] == 2 and saved["started_at"]
    assert saved["data"]["turn_id"] == fake_engine.turns[-1]["turn_id"]
    assert routine_journal(app) == ["Queued a routine", "Started a routine"]
    assert app.store.get_reminder(reminder["id"])["due_at"] == next_due


async def test_failures_before_turn_request_are_retryable(app, fake_engine, monkeypatch):
    conv = app.conversations.create()
    due_routine(app, conv["id"])

    async def unavailable(**kwargs):
        raise EngineClosed("Engine unavailable")

    monkeypatch.setattr(fake_engine, "start_thread", unavailable)
    await app.scheduler.tick()
    assert occurrences(app)[0]["status"] == "failed"
    assert not fake_engine.turns


async def test_failure_exhaustion_surfaces_once_and_stops_retrying(app, fake_engine, monkeypatch):
    conv = app.conversations.create()
    due_routine(app, conv["id"])
    attempts = 0

    async def reject(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise RpcError(-32000, "Rejected")

    monkeypatch.setattr(fake_engine, "start_turn", reject)
    for i in range(ROUTINE_MAX_ATTEMPTS):
        if i:
            app.store.update_routine(occurrences(app)[0]["id"], next_attempt_at=0)
        await app.scheduler.tick()
    saved = occurrences(app)[0]
    assert saved["status"] == "failed" and saved["attempts"] == ROUTINE_MAX_ATTEMPTS
    app.scheduler.recover_after_restart()
    await app.scheduler.tick()
    assert attempts == ROUTINE_MAX_ATTEMPTS
    assert routine_journal(app) == ["Queued a routine", "Routine delivery failed"]
    messages = app.store.list_messages(conv["id"])
    assert sum("could not start after" in m["content"] for m in messages) == 1
    assert app.store.query("SELECT * FROM notifications WHERE kind='routine'")


async def test_busy_routine_survives_restart_and_keeps_original_metadata(fake_engine):
    original = WeeboApp()
    original.engine = fake_engine
    conv = original.conversations.create()
    await original.conversations.send(conv["id"], "Busy foreground work")
    reminder = due_routine(original, conv["id"], "daily")
    await original.scheduler.tick()
    queued = occurrences(original)[0]
    assert queued["status"] == "queued" and queued["attempts"] == 0
    assert len(original.conversations.session(conv["id"]).followups) == 1
    original.store.close()

    restarted = WeeboApp()
    restarted.engine = fake_engine
    try:
        await restarted.start(with_engine=False, with_background=False)
        assert restarted.store.get_routine(queued["id"]) == queued
        await restarted.scheduler.tick()
        await restarted.scheduler.tick()
        assert len(fake_engine.turns) == 2
        saved = restarted.store.get_routine(queued["id"])
        assert saved["status"] == "started" and saved["data"]["reminder"] == reminder
        assert restarted.conversations.session(conv["id"]).turn.trigger == "routine"
        notices = [m for m in restarted.store.list_messages(conv["id"]) if m["kind"] == "reminder"]
        assert len(notices) == 1
        assert notices[0]["data"] == queued["data"]["notice_data"]
        assert notices[0]["data"]["reminder_id"] == reminder["id"]
        assert notices[0]["data"]["due_at"] == reminder["due_at"]
        assert notices[0]["data"]["routine"] and notices[0]["data"]["recurrence"] == "daily"
    finally:
        await restarted.stop()


async def test_busy_duplicate_ticks_and_deferred_dispatch_start_only_once(app, fake_engine):
    conv = app.conversations.create()
    await app.conversations.send(conv["id"], "Busy")
    foreground = fake_engine.turns[0]
    reminder = due_routine(app, conv["id"])
    await asyncio.gather(app.scheduler.tick(), app.scheduler.tick(), app.scheduler.tick())
    assert len(occurrences(app)) == 1
    assert len(app.conversations.session(conv["id"]).followups) == 1
    assert routine_journal(app) == ["Queued a routine"]
    await fake_engine.finish_turn(foreground["thread_id"], foreground["turn_id"])
    await asyncio.gather(app.scheduler.tick(), app.scheduler.tick())
    await drain(20)
    assert len(fake_engine.turns) == 2
    assert occurrences(app)[0]["status"] == "started"
    assert app.conversations.session(conv["id"]).turn.trigger == "routine"
    notice = next(m for m in app.store.list_messages(conv["id"]) if m["kind"] == "reminder")
    assert notice["data"]["reminder_id"] == reminder["id"] and notice["data"]["routine"]
    assert routine_journal(app) == ["Queued a routine", "Started a routine"]


async def test_starting_is_persisted_before_engine_acceptance(app, fake_engine, monkeypatch):
    conv = app.conversations.create()
    due_routine(app, conv["id"])
    entered, release = asyncio.Event(), asyncio.Event()
    original_start = fake_engine.start_turn

    async def delayed(*args, **kwargs):
        assert occurrences(app)[0]["status"] == "starting"
        entered.set()
        await release.wait()
        return await original_start(*args, **kwargs)

    monkeypatch.setattr(fake_engine, "start_turn", delayed)
    first = asyncio.create_task(app.scheduler.tick())
    await entered.wait()
    await app.scheduler.tick()
    assert routine_journal(app) == ["Queued a routine"] and not fake_engine.turns
    release.set()
    await first
    assert len(fake_engine.turns) == 1
    assert routine_journal(app) == ["Queued a routine", "Started a routine"]


@pytest.mark.parametrize("state", ["starting", "started"])
async def test_inflight_restart_is_interrupted_and_never_replayed(app, fake_engine, state):
    conv = app.conversations.create()
    reminder = due_routine(app, conv["id"])
    await app.scheduler.tick()
    occurrence = occurrences(app)[0]
    app.store.update_routine(occurrence["id"], status=state)
    app.conversations.sessions.clear()
    app.scheduler.recover_after_restart()
    app.scheduler.recover_after_restart()
    await app.scheduler.tick()
    saved = occurrences(app)[0]
    assert saved["status"] == "interrupted" and saved["finished_at"]
    assert len(fake_engine.turns) == 1
    messages = app.store.list_messages(conv["id"])
    notices = [m for m in messages if "will not be retried automatically" in m["content"]]
    assert len(notices) == 1 and notices[0]["data"]["reminder_id"] == reminder["id"]


async def test_completed_start_is_not_interrupted_or_replayed_on_restart(app, fake_engine):
    conv = app.conversations.create()
    due_routine(app, conv["id"])
    await app.scheduler.tick()
    turn = fake_engine.turns[0]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"])
    saved = occurrences(app)[0]
    assert saved["status"] == "started" and saved["finished_at"]
    app.scheduler.recover_after_restart()
    await app.scheduler.tick()
    assert occurrences(app)[0] == saved and len(fake_engine.turns) == 1


@pytest.mark.parametrize("confirmed", [False, True])
async def test_lost_start_response_never_replays_and_confirmation_is_recorded(app, fake_engine, monkeypatch, confirmed):
    conv = app.conversations.create()
    due_routine(app, conv["id"])

    async def lost_response(thread_id, *args, **kwargs):
        if confirmed:
            await fake_engine.emit(thread_id, "turn/started", {"turn": {"id": "accepted-turn"}})
        raise asyncio.TimeoutError("Lost response")

    monkeypatch.setattr(fake_engine, "start_turn", lost_response)
    await app.scheduler.tick()
    saved = occurrences(app)[0]
    assert saved["status"] == "interrupted"
    assert bool(saved["started_at"]) == confirmed
    assert ("Started a routine" in routine_journal(app)) == confirmed
    assert saved["attempts"] == 1
    app.scheduler.recover_after_restart()
    await app.scheduler.tick()
    assert occurrences(app)[0]["attempts"] == 1


async def test_stale_repeat_skips_delivery_but_keeps_cadence(app, fake_engine):
    conv = app.conversations.create()
    due_at = time.time() - MISSED_GRACE - 60
    reminder = due_routine(app, conv["id"], "every 1800s", due_at)
    await app.scheduler.tick()
    updated = app.store.get_reminder(reminder["id"])
    assert updated["status"] == "pending" and updated["due_at"] > time.time()
    intervals = (updated["due_at"] - due_at) / 1800
    assert intervals == pytest.approx(round(intervals))
    assert not occurrences(app) and not fake_engine.turns and not routine_journal(app)


async def test_existing_database_gets_occurrence_table(tmp_path):
    path = tmp_path / "v1.db"
    old = Store(path)
    reminder = old.add_reminder("Existing routine", time.time() - 1, action="prompt")
    old.execute("DROP TABLE routine_occurrences")
    old.execute("PRAGMA user_version=1")
    old.close()
    migrated = Store(path)
    try:
        assert migrated.get_reminder(reminder["id"]) == reminder
        assert migrated.query("SELECT * FROM routine_occurrences") == []
        assert migrated.query_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    finally:
        migrated.close()


async def test_version_2_database_gets_eval_and_vector_tables(tmp_path):
    path = tmp_path / "v2.db"
    old = Store(path)
    memory = old.add_memory("User likes tea", "preference")
    old.execute("DROP TABLE eval_cases")
    old.execute("DROP TABLE memory_vectors")
    old.execute("PRAGMA user_version=2")
    old.close()
    migrated = Store(path)
    try:
        assert migrated.get_memory(memory["id"])["text"] == "User likes tea"
        assert migrated.list_eval_cases() == [] and migrated.memory_vectors("m") == []
        assert migrated.query_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    finally:
        migrated.close()


async def test_event_dispatch_reports_startup_outcomes(app, fake_engine, monkeypatch):
    conv = app.conversations.create()

    async def reject(*args, **kwargs):
        raise RpcError(-32000, "Rejected")

    with monkeypatch.context() as patch:
        patch.setattr(fake_engine, "start_turn", reject)
        assert await app.conversations.run_event(conv["id"], "Fail", "Fail") == "failed"
    assert await app.conversations.run_event(conv["id"], "Start", "Start") == "started"
    assert await app.conversations.run_event(conv["id"], "Wait", "Wait") == "queued"


async def test_failed_occurrence_is_retried_after_real_restart(fake_engine, monkeypatch):
    original = WeeboApp()
    original.engine = fake_engine
    conv = original.conversations.create()
    due_routine(original, conv["id"])

    async def reject(*args, **kwargs):
        raise RpcError(-32000, "Rejected")

    with monkeypatch.context() as patch:
        patch.setattr(fake_engine, "start_turn", reject)
        await original.scheduler.tick()
    failed = occurrences(original)[0]
    original.store.close()
    restarted = WeeboApp()
    restarted.engine = fake_engine
    try:
        await restarted.start(with_engine=False, with_background=False)
        assert restarted.store.get_routine(failed["id"]) == failed
        await restarted.scheduler.tick()
        assert not fake_engine.turns  # The persisted backoff still applies.
        restarted.store.update_routine(failed["id"], next_attempt_at=0)
        await restarted.scheduler.tick()
        assert occurrences(restarted)[0]["status"] == "started" and len(fake_engine.turns) == 1
    finally:
        await restarted.stop()


async def test_restart_surfaces_exhaustion_if_notice_was_not_yet_written(app, fake_engine, monkeypatch):
    conv = app.conversations.create()
    due_routine(app, conv["id"])

    async def reject(*args, **kwargs):
        raise RpcError(-32000, "Rejected")

    with monkeypatch.context() as patch:
        patch.setattr(fake_engine, "start_turn", reject)
        await app.scheduler.tick()
    occurrence = occurrences(app)[0]
    # Simulate a crash after the final failed state was committed, before notice delivery.
    app.store.update_routine(occurrence["id"], attempts=ROUTINE_MAX_ATTEMPTS)
    app.scheduler.recover_after_restart()
    app.scheduler.recover_after_restart()
    assert sum("could not start after" in m["content"] for m in app.store.list_messages(conv["id"])) == 1
    await app.scheduler.tick()
    assert not fake_engine.turns


async def test_completed_notification_before_lost_response_is_still_a_confirmed_start(app, fake_engine, monkeypatch):
    conv = app.conversations.create()
    due_routine(app, conv["id"])

    async def completed_then_lost(thread_id, *args, **kwargs):
        await fake_engine.finish_turn(thread_id, "fast-turn")
        raise asyncio.TimeoutError("Lost response after completion")

    monkeypatch.setattr(fake_engine, "start_turn", completed_then_lost)
    await app.scheduler.tick()
    saved = occurrences(app)[0]
    assert saved["status"] == "started" and saved["started_at"] and saved["finished_at"]
    assert routine_journal(app) == ["Queued a routine", "Started a routine"]
    app.scheduler.recover_after_restart()
    assert occurrences(app)[0] == saved
