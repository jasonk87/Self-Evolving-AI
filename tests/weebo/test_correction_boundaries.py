"""Corrections must replay the user exchange, not an unrelated autonomous turn."""

import asyncio

import pytest

from weebo.codex.rpc import EngineClosed, RpcError


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("kind", ["error", "notice", "agent_report", "reminder"])
@pytest.mark.parametrize("event_turn", [None, "routine-turn"])
async def test_correction_does_not_cross_event_boundary(app, kind, event_turn):
    conv = app.conversations.create("Desk", kind="desk")
    app.store.add_message(conv["id"], "user", "Plan a weekend trip for me")
    app.store.add_message(conv["id"], "event", "Unrelated routine", kind=kind, turn_id=event_turn)
    app.store.add_message(conv["id"], "assistant", "The build failed", turn_id="routine-turn")
    app.store.add_message(conv["id"], "user", "No, the build passed")

    assert app.evals.capture_correction(conv["id"], "No, the build passed") is None
    assert app.store.list_eval_cases() == []


async def test_failed_user_request_then_routine_does_not_become_a_case(app, fake_engine, monkeypatch):
    conv = app.conversations.create("Desk", kind="desk")

    async def unavailable(*args, **kwargs):
        raise RuntimeError("mocked engine unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(fake_engine, "ensure_ready", unavailable)
        await app.conversations.send(conv["id"], "Plan a weekend trip for me")
    assert await app.conversations.run_event(
        conv["id"], "Report on the build", "Routine report", trigger="routine"
    ) == "started"
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "The build failed")

    assert app.evals.capture_correction(conv["id"], "No, the build passed") is None
    assert app.store.list_eval_cases() == []


async def test_error_after_reply_is_not_hidden_from_correction(app):
    conv = app.conversations.create("Build")
    app.store.add_message(conv["id"], "user", "Did the build pass?")
    app.store.add_message(conv["id"], "assistant", "The build failed", turn_id="user-turn")
    app.store.add_message(conv["id"], "event", "Engine stopped mid-turn", kind="error")

    assert app.evals.capture_correction(conv["id"], "No, the build passed") is None


async def test_multipart_user_reply_keeps_same_turn_artifacts(app, fake_engine):
    conv = app.conversations.create("Weather")
    prompt = "What's the weather in Paris tomorrow?"
    await app.conversations.send(conv["id"], prompt)
    turn = fake_engine.turns[-1]
    thread_id, turn_id = turn["thread_id"], turn["turn_id"]
    await fake_engine.emit(thread_id, "turn/started", {"turn": {"id": turn_id}})
    await fake_engine.emit(thread_id, "item/completed", {"turnId": turn_id, "item": {
        "type": "agentMessage", "id": "commentary", "text": "Checking the forecast.", "phase": "commentary",
    }})
    await fake_engine.emit(thread_id, "item/completed", {"turnId": turn_id, "item": {
        "type": "commandExecution", "id": "weather", "command": "get_weather Paris",
        "status": "completed", "exitCode": 0, "aggregatedOutput": "sunny",
    }})
    await fake_engine.emit(thread_id, "turn/plan/updated", {"turnId": turn_id, "plan": [
        {"step": "Check the forecast", "status": "completed"},
    ]})
    await fake_engine.finish_turn(thread_id, turn_id, "Paris, Texas: sunny.")
    app.store.add_message(conv["id"], "event", "1 file changed", kind="diff", turn_id=turn_id)

    case = app.evals.capture_correction(conv["id"], "No, I meant Paris, France")
    assert case and case["prompt"] == prompt
    assert "Paris, France" in case["rubric"]
    assert case["data"]["dialogue"] == []


async def test_autonomous_reply_cannot_cross_a_previous_assistant_turn(app):
    conv = app.conversations.create("Desk", kind="desk")
    app.store.add_message(conv["id"], "user", "Plan a weekend trip for me")
    app.store.add_message(conv["id"], "assistant", "Visit the coast", turn_id="user-turn")
    app.store.add_message(conv["id"], "assistant", "The build failed", turn_id="routine-turn")

    assert app.evals.capture_correction(conv["id"], "No, the build passed") is None


async def test_autonomous_reply_without_notice_keeps_its_trigger(app, fake_engine):
    conv = app.conversations.create("Desk", kind="desk")
    app.store.add_message(conv["id"], "user", "Plan a weekend trip for me")
    assert await app.conversations.run_event(
        conv["id"], "Report on the build", "", trigger="routine"
    ) == "started"
    turn = fake_engine.turns[-1]
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "The build failed")

    reply = app.store.recent_dialogue(conv["id"])[-1]
    assert reply["data"]["trigger"] == "routine"
    assert app.evals.capture_correction(conv["id"], "No, the build passed") is None
    assert app.store.list_eval_cases() == []


async def test_streamed_user_reply_keeps_its_trigger(app, fake_engine):
    conv = app.conversations.create("Build")
    await app.conversations.send(conv["id"], "Did the build pass?")
    turn = fake_engine.turns[-1]
    await fake_engine.emit(turn["thread_id"], "item/started", {"turnId": turn["turn_id"], "item": {
        "type": "agentMessage", "id": "reply", "phase": "final_answer",
    }})
    reply = app.store.recent_dialogue(conv["id"])[-1]
    assert reply["data"]["trigger"] == "user"
    await fake_engine.emit(turn["thread_id"], "item/completed", {"turnId": turn["turn_id"], "item": {
        "type": "agentMessage", "id": "reply", "text": "The build failed", "phase": "final_answer",
    }})
    await fake_engine.emit(turn["thread_id"], "turn/completed", {"turn": {
        "id": turn["turn_id"], "status": "completed",
    }})
    reply = app.store.recent_dialogue(conv["id"])[-1]
    assert reply["data"]["trigger"] == "user"
    case = app.evals.capture_correction(conv["id"], "No, the build passed")
    assert case and case["prompt"] == "Did the build pass?"


async def test_dream_note_does_not_borrow_an_unanswered_user_message(app, monkeypatch):
    conv = app.conversations.desk()
    app.store.add_message(conv["id"], "user", "Plan a weekend trip for me")
    monkeypatch.setattr(app, "notify", lambda *args, **kwargs: None)
    app.heartbeat._apply_dream({"message_to_user": "The build failed"})
    reply = app.store.recent_dialogue(conv["id"])[-1]
    assert reply["data"]["source"] == "dream"

    assert app.evals.capture_correction(conv["id"], "No, the build passed") is None
    assert app.store.list_eval_cases() == []


async def test_rejected_steer_does_not_replace_the_replayed_request(app, fake_engine, monkeypatch):
    conv = app.conversations.create("Build")
    await app.conversations.send(conv["id"], "Did the build pass?")
    turn = fake_engine.turns[-1]

    async def rejected(*args, **kwargs):
        raise RpcError(-32600, "mocked steer rejected")

    monkeypatch.setattr(fake_engine, "steer", rejected)
    with pytest.raises(ValueError, match="Could not add your message"):
        await app.conversations.send(conv["id"], "Plan a weekend trip for me")
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "The build failed")

    case = app.evals.capture_correction(conv["id"], "No, the build passed")
    assert case and case["prompt"] == "Did the build pass?"
    rejected = next(row for row in app.store.recent_dialogue(conv["id"])
                    if row["content"] == "Plan a weekend trip for me")
    assert rejected["turn_id"] is None and rejected["data"]["delivery"] == "rejected"


async def test_successful_steer_is_linked_to_the_answered_turn(app, fake_engine):
    conv = app.conversations.create("Build")
    first = await app.conversations.send(conv["id"], "Did the build pass?")
    turn = fake_engine.turns[-1]
    second = await app.conversations.send(conv["id"], "And did the tests pass?")
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "The tests failed")

    assert first["turn_id"] == second["turn_id"] == turn["turn_id"]
    assert second["data"]["steered"] is True
    case = app.evals.capture_correction(conv["id"], "No, the tests passed")
    assert case and case["prompt"] == "And did the tests pass?"


async def test_user_link_survives_completion_before_start_response(app, fake_engine, monkeypatch):
    conv = app.conversations.create("Build")
    start = fake_engine.start_turn

    async def complete_before_response(*args, **kwargs):
        result = await start(*args, **kwargs)
        turn = fake_engine.turns[-1]
        await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "The build failed")
        return result

    monkeypatch.setattr(fake_engine, "start_turn", complete_before_response)
    message = await app.conversations.send(conv["id"], "Did the build pass?")
    assert message["turn_id"] == fake_engine.turns[-1]["turn_id"]
    case = app.evals.capture_correction(conv["id"], "No, the build passed")
    assert case and case["prompt"] == "Did the build pass?"


@pytest.mark.parametrize("failure", [asyncio.TimeoutError(), EngineClosed("mocked engine closed")])
async def test_uncertain_steer_delivery_is_not_skipped(app, fake_engine, monkeypatch, failure):
    conv = app.conversations.create("Build")
    await app.conversations.send(conv["id"], "Did the build pass?")
    turn = fake_engine.turns[-1]

    async def uncertain(*args, **kwargs):
        raise failure

    monkeypatch.setattr(fake_engine, "steer", uncertain)
    with pytest.raises(ValueError, match="Could not add your message"):
        await app.conversations.send(conv["id"], "Plan a weekend trip for me")
    await fake_engine.finish_turn(turn["thread_id"], turn["turn_id"], "The build failed")

    uncertain_message = next(row for row in app.store.recent_dialogue(conv["id"])
                             if row["content"] == "Plan a weekend trip for me")
    assert uncertain_message["data"]["delivery"] == "unconfirmed"
    assert app.evals.capture_correction(conv["id"], "No, the build passed") is None


async def test_rejected_input_does_not_hide_an_error_boundary(app):
    conv = app.conversations.create("Build")
    app.store.add_message(conv["id"], "user", "Did the build pass?", turn_id="user-turn")
    app.store.add_message(conv["id"], "event", "Engine failed", kind="error", turn_id="user-turn")
    app.store.add_message(conv["id"], "user", "Plan a weekend trip for me", data={"delivery": "rejected"})
    app.store.add_message(conv["id"], "assistant", "The build failed", turn_id="user-turn",
                          data={"trigger": "user"})

    assert app.evals.capture_correction(conv["id"], "No, the build passed") is None
