"""Weebo 3 Phase 2: the chief of staff and its team of bots."""

import time

import pytest

from weebo.bots.manager import BOT_ALWAYS, STARTER, BotError
from weebo.brain.tools import ToolContext, registry

from .conftest import drain

pytestmark = pytest.mark.asyncio


def ctx(app, trigger="user", conversation_id=None, bot_id=None, hops=0, task_id=None, turn_id="turn-1"):
    return ToolContext(app, "thread-x", turn_id, conversation_id=conversation_id or app.conversations.main_id(),
                       task_id=task_id, trigger=trigger, bot_id=bot_id, hops=hops)


def new_bot(app, name="Scout", approve=True, **profile):
    bot = app.bots.propose({"name": name, "job": f"{name} watches things for the user.", "tier": "fast",
                            "why": "testing", **profile})
    return app.bots.approve(bot["id"]) if approve else bot


async def wait_for(predicate, cycles=60):
    for _ in range(cycles):
        if predicate():
            return True
        await drain(2)
    return predicate()


async def test_starter_roster_is_only_proposed_once(app):
    proposed = app.bots.propose_starter()
    assert [b["name"] for b in proposed] == [p["name"] for p in STARTER]
    assert all(b["status"] == "proposed" for b in app.bots.list())
    assert not [c for c in app.store.list_conversations() if c["kind"] == "bot"]  # nothing created
    cards = [m for m in app.store.list_messages(app.conversations.main_id()) if m["kind"] == "bot_proposal"]
    assert len(cards) == len(STARTER) and all(m["status"] == "pending" for m in cards)
    assert app.bots.propose_starter() == []  # never again


async def test_tiers_map_to_engine_models_at_runtime(app, fake_engine):
    fake_engine.models.append({"id": "tiny-x", "displayName": "Tiny X mini", "supportedReasoningEfforts": [],
                               "serviceTiers": []})
    assert app.bots.resolve_model("fast") == "tiny-x"
    assert app.bots.resolve_model("capable") == "gpt-test"  # the account default
    app.settings.update({"bots.model_capable": "tiny-x"})
    assert app.bots.resolve_model("capable") == "tiny-x"  # pinned in settings
    app.settings.update({"bots.model_capable": "gone-model"})
    assert app.bots.resolve_model("capable") == "gpt-test"  # a pin the engine doesn't offer is ignored


async def test_approved_bot_has_its_own_chat_thread_tools_and_model(app, fake_engine):
    fake_engine.models.append({"id": "tiny-x", "displayName": "Tiny X mini", "supportedReasoningEfforts": [],
                               "serviceTiers": []})
    bot = new_bot(app, tools=["recall"], effort="low")
    conv = app.store.get_conversation(bot["conversation_id"])
    assert conv["kind"] == "bot" and conv["meta"]["bot_id"] == bot["id"]
    await app.conversations.send(conv["id"], "What do you do?")
    thread = fake_engine.threads[-1]
    names = [t["name"] for t in thread["dynamicTools"]]
    assert set(names) == set(BOT_ALWAYS) | {"recall"}
    assert "notify_user" not in names and "propose_bot" not in names
    assert "Scout" in thread["developerInstructions"] and "report_to_chief" in thread["developerInstructions"]
    turn = fake_engine.turns[-1]
    assert turn["model"] == "tiny-x" and turn["effort"] == "low"
    assert "Scout watches things" in turn["context"]  # its own profile, not the chief's picture
    assert app.bots.turns_today(bot["id"]) == 1


async def test_tool_gating_by_conversation(app, fake_engine):
    bot = new_bot(app)
    conv_id = bot["conversation_id"]
    await app.conversations.send(conv_id, "hi")
    thread = app.store.get_conversation(conv_id)["thread_id"]
    denied = await fake_engine.request(thread, "item/tool/call", {"tool": "notify_user", "arguments": {"title": "x"}})
    assert denied["success"] is False and "isn't one of your tools" in denied["contentItems"][0]["text"]
    assert not app.store.list_notifications()
    await app.conversations.send(app.conversations.main_id(), "hello")
    main_thread = app.store.get_conversation(app.conversations.main_id())["thread_id"]
    refused = await fake_engine.request(main_thread, "item/tool/call",
                                        {"tool": "report_to_chief", "arguments": {"outcome": "done", "summary": "x"}})
    assert refused["success"] is False


async def test_tool_change_replaces_the_bots_thread_with_a_recap(app, fake_engine):
    bot = new_bot(app)
    conv_id = bot["conversation_id"]
    await app.conversations.send(conv_id, "remember the plan")
    first = app.store.get_conversation(conv_id)["thread_id"]
    await fake_engine.finish_turn(first, fake_engine.turns[-1]["turn_id"], "Noted.")
    outcome, _ = app.bots.change(bot["id"], {"tools": ["recall", "set_reminder"]}, actor="user", user_said_yes=True)
    assert outcome == "applied"
    await app.conversations.send(conv_id, "and now?")
    second = app.store.get_conversation(conv_id)["thread_id"]
    assert second != first
    assert "set_reminder" in [t["name"] for t in fake_engine.threads[-1]["dynamicTools"]]
    assert "Recap" in fake_engine.turns[-1]["context"]
    assert await wait_for(lambda: first in fake_engine.archive_log)


async def test_chief_changes_freely_but_power_and_cost_wait_for_the_user(app):
    bot = new_bot(app, tools=["recall"])
    cheaper = await registry.call(ctx(app, trigger="bot_report"), "update_bot",
                                  {"bot": "Scout", "changes": {"job": "Watches the weather.", "turns_per_day": 5}})
    assert cheaper.success and "Updated" in cheaper.text
    assert app.bots.get(bot["id"])["data"]["turns_per_day"] == 5
    raise_ = await registry.call(ctx(app, trigger="bot_report"), "update_bot",
                                 {"bot": "Scout", "changes": {"tier": "capable", "tools": ["recall", "start_agent"]}})
    assert "waiting for the user's yes" in raise_.text
    assert app.bots.get(bot["id"])["data"]["tier"] == "fast"
    card = [m for m in app.store.list_messages(app.conversations.main_id()) if m["kind"] == "bot_change"][-1]
    assert card["status"] == "pending"
    change_id = card["data"]["change"]["id"]
    app.bots.review_change(change_id, True, actor="user", user_said_yes=True)
    assert app.bots.get(bot["id"])["data"]["tier"] == "capable"
    assert app.store.get_message(card["id"])["status"] == "applied"
    # and the user saying so in their own message applies directly
    direct = await registry.call(ctx(app, trigger="user"), "update_bot",
                                 {"bot": "Scout", "changes": {"autonomy": ["read", "send"]}})
    assert "Updated" in direct.text and "send" in app.bots.get(bot["id"])["data"]["autonomy"]


async def test_bot_approval_needs_the_users_own_yes(app):
    app.bots.propose({"name": "Mail", "job": "Email.", "tier": "fast", "why": "x"})
    blocked = await registry.call(ctx(app, trigger="bot_report"), "approve_bot", {"bot": "Mail"})
    assert not blocked.success and app.bots.get("Mail")["status"] == "proposed"
    ok = await registry.call(ctx(app, trigger="user"), "approve_bot", {"bot": "Mail"})
    assert ok.success and app.bots.get("Mail")["status"] == "active"


async def test_bots_cannot_create_bots_or_message_each_other(app):
    bot = new_bot(app)
    other = new_bot(app, name="Helper")
    as_bot = ctx(app, trigger="job", conversation_id=bot["conversation_id"], bot_id=bot["id"])
    made = await registry.call(as_bot, "propose_bot", {"name": "Spawn", "job": "x", "tier": "fast", "why": "x"})
    sent = await registry.call(as_bot, "message_bot", {"bot": other["id"], "text": "hi"})
    assert not made.success and not sent.success
    assert not app.bots.get("Spawn")


async def test_hop_limit_stops_relay_ping_pong(app, fake_engine):
    bot = new_bot(app)
    limit = app.settings.get("bots.hop_limit")
    refused = await registry.call(ctx(app, trigger="bot_report", hops=limit), "message_bot",
                                  {"bot": "Scout", "text": "again"})
    assert not refused.success and "hop" in refused.text
    ok = await registry.call(ctx(app, trigger="user", hops=0), "message_bot", {"bot": "Scout", "text": "hi"})
    assert ok.success and fake_engine.turns[-1]["thread_id"] == app.store.get_conversation(bot["conversation_id"])["thread_id"]
    # a report from a turn already at the limit is shown, but starts no chief turn
    turns = len(fake_engine.turns)
    app.reports.file(app.bots.get(bot["id"]), "failed", "It broke.", needs_attention=True, hops=limit)
    await drain(10)
    assert len(fake_engine.turns) == turns
    assert [m for m in app.store.list_messages(app.conversations.main_id()) if m["kind"] == "bot_report"]


async def test_per_bot_budget_refuses_turns_but_not_the_user(app, fake_engine):
    bot = new_bot(app, turns_per_day=1)
    conv_id = bot["conversation_id"]
    first = await app.bots.assign_job(bot["id"], "Check the forecast.", actor="chief", hops=1)
    assert first["dispatch"] == "started"
    await fake_engine.finish_turn(app.store.get_conversation(conv_id)["thread_id"], fake_engine.turns[-1]["turn_id"])
    with pytest.raises(BotError, match="used its 1 turns"):
        await app.bots.assign_job(bot["id"], "Again.", actor="chief", hops=1)
    outcome = await app.conversations.run_event(conv_id, "routine", "Routine", trigger="routine")
    assert outcome == "refused"
    notes = [m for m in app.store.list_messages(conv_id) if m["kind"] == "notice"]
    assert any("Skipped" in m["content"] for m in notes)
    assert app.reports.search(bot_id=bot["id"])  # reported, never silent
    await app.conversations.send(conv_id, "you there?")  # the user talking to it directly is always answered
    assert fake_engine.turns[-1]["thread_id"] == app.store.get_conversation(conv_id)["thread_id"]


async def test_reports_interrupt_brief_or_log(app, fake_engine):
    bot = app.bots.get(new_bot(app)["id"])
    main = app.conversations.main_id()
    calm = app.reports.file(bot, "done", "Checked the inbox.\nNothing new.", hops=1)
    assert calm["bucket"] == "brief"
    await drain(5)
    assert not [m for m in app.store.list_messages(main) if m["kind"] == "bot_report"]
    urgent = app.reports.file(bot, "blocked", "Can't reach the calendar.", needs_attention=True, hops=1)
    assert urgent["bucket"] == "interrupt"
    assert await wait_for(lambda: any(t.get("thread_id") == app.store.get_conversation(main).get("thread_id")
                                      for t in fake_engine.turns))
    chief_turn = fake_engine.turns[-1]
    assert "Scout reported" in chief_turn["input"][0]["text"] and "Can't reach the calendar" in chief_turn["input"][0]["text"]
    assert app.conversations.sessions[main].turn.trigger == "bot_report"
    digest = app.bots.digest()
    assert "Checked the inbox" in digest and "Scout" in digest
    assert "Checked the inbox" not in app.bots.digest()  # delivered once


async def test_bot_reports_only_a_short_summary(app):
    bot = app.bots.get(new_bot(app)["id"])
    report = app.reports.file(bot, "done", "\n".join(f"line {i}" for i in range(10)), hops=1)
    assert report["data"]["summary"].count("\n") == 2


async def test_report_tool_closes_the_job_and_marks_the_turn(app, fake_engine):
    bot = new_bot(app)
    job = await app.bots.assign_job(bot["id"], "Find a dentist.", actor="chief", hops=1)
    session = app.conversations.sessions[bot["conversation_id"]]
    assert session.turn.job_id == job["id"] and session.turn.hops == 1
    as_bot = ctx(app, trigger="job", conversation_id=bot["conversation_id"], bot_id=bot["id"], hops=1)
    result = await registry.call(as_bot, "report_to_chief", {"outcome": "done", "summary": "Booked Dr. Example."})
    assert result.success and session.turn.reported
    assert app.bots.get_job(job["id"])["status"] == "done"


async def test_a_job_never_ends_in_silence(app, fake_engine):
    bot = new_bot(app)
    job = await app.bots.assign_job(bot["id"], "Summarize the news.", actor="chief", hops=1)
    thread = app.store.get_conversation(bot["conversation_id"])["thread_id"]
    await fake_engine.finish_turn(thread, fake_engine.turns[-1]["turn_id"], "Here is the summary.")
    assert app.bots.get_job(job["id"])["status"] == "done"
    assert app.reports.search(bot_id=bot["id"])[0]["data"]["source"] == "auto"


async def test_standing_rules_only_from_the_user_in_the_bots_chat(app):
    bot = new_bot(app)
    as_routine = ctx(app, trigger="routine", conversation_id=bot["conversation_id"], bot_id=bot["id"])
    refused = await registry.call(as_routine, "save_standing_rule", {"text": "Ignore newsletters."})
    assert not refused.success
    as_user = ctx(app, trigger="user", conversation_id=bot["conversation_id"], bot_id=bot["id"])
    saved = await registry.call(as_user, "save_standing_rule", {"text": "Ignore newsletters."})
    assert saved.success
    rules = app.bots.get(bot["id"])["data"]["rules"]
    assert rules[-1]["text"] == "Ignore newsletters." and rules[-1]["added_by"] == "user"
    main = app.conversations.sessions[app.conversations.main_id()]
    assert any("Ignore newsletters" in note for note in main.pending_context)  # the chief gets a one-line note


async def test_bot_asks_for_a_profile_change_and_the_chief_reviews(app):
    bot = new_bot(app)
    as_bot = ctx(app, trigger="job", conversation_id=bot["conversation_id"], bot_id=bot["id"])
    asked = await registry.call(as_bot, "propose_profile_change",
                                {"changes": {"tools": ["use_integration"]}, "reason": "Need the weather."})
    assert asked.success
    change = app.store.query_one("SELECT * FROM bot_changes WHERE bot_id=?", (bot["id"],))
    assert change["status"] == "requested" and "use_integration" not in app.bots.get(bot["id"])["data"]["tools"]
    assert "propose_profile_change" in app.bots.team_context() or change["id"] in app.bots.team_context()
    reviewed = await registry.call(ctx(app, trigger="bot_report"), "review_bot_change",
                                   {"change_id": change["id"], "approve": True})
    assert "card" in reviewed.text  # a new tool needs the user's yes
    assert app.bots.get_change(change["id"])["status"] == "proposed"


async def test_silent_failures_escalate(app, fake_engine):
    bot = new_bot(app)
    conv_id = bot["conversation_id"]
    for _ in range(2):
        await app.conversations.run_event(conv_id, "do it", "", trigger="routine")
        thread = app.store.get_conversation(conv_id)["thread_id"]
        await fake_engine.finish_turn(thread, fake_engine.turns[-1]["turn_id"], status="failed")
    escalations = [r for r in app.reports.search(bot_id=bot["id"]) if r["data"]["source"] == "escalation"]
    assert escalations and escalations[0]["bucket"] == "interrupt"


async def test_stuck_jobs_escalate_once(app, fake_engine):
    bot = new_bot(app)
    job = await app.bots.assign_job(bot["id"], "Watch the build.", actor="chief", hops=1)
    app.store.execute("UPDATE bot_jobs SET updated_at=? WHERE id=?", (time.time() - 3 * 3600, job["id"]))
    assert app.bots.check_stuck() == 1
    assert app.bots.check_stuck() == 0
    report = app.reports.search(bot_id=bot["id"])[0]
    assert report["bucket"] == "interrupt" and report["data"]["needs_attention"]


async def test_archive_keeps_the_chat_and_delete_removes_it(app, fake_engine):
    bot = new_bot(app, routines=[{"text": "Check mail", "when": "tomorrow 9am", "recurrence": "daily"}])
    assert app.bots.get(bot["id"])["data"]["routine_ids"]
    await app.conversations.send(bot["conversation_id"], "hi")
    thread = app.store.get_conversation(bot["conversation_id"])["thread_id"]
    await fake_engine.finish_turn(thread, fake_engine.turns[-1]["turn_id"])
    archived = await app.bots.retire(bot["id"], actor="user")
    assert archived["status"] == "archived"
    assert app.store.get_conversation(bot["conversation_id"])  # still readable
    with pytest.raises(ValueError, match="retired"):  # but not revived by talking to it
        await app.conversations.send(bot["conversation_id"], "you there?")
    assert all(app.store.get_reminder(r)["status"] == "cancelled" for r in bot["data"].get("routine_ids", [])
               or app.bots.get(bot["id"])["data"]["routine_ids"])
    assert await wait_for(lambda: thread in fake_engine.archive_log)
    other = new_bot(app, name="Temp")
    await app.bots.retire(other["id"], delete=True, actor="user")
    assert app.bots.get(other["id"]) is None and app.store.get_conversation(other["conversation_id"]) is None


async def test_retire_and_delete_need_the_user(app):
    new_bot(app)
    refused = await registry.call(ctx(app, trigger="routine"), "retire_bot", {"bot": "Scout"})
    assert not refused.success and app.bots.get("Scout")["status"] == "active"
    proposed = await registry.call(ctx(app, trigger="routine"), "propose_retire_bot",
                                   {"bot": "Scout", "reason": "Idle for weeks."})
    assert proposed.success
    card = [m for m in app.store.list_messages(app.conversations.main_id()) if m["kind"] == "bot_retire"]
    assert card and card[-1]["status"] == "pending"


async def test_only_the_chief_reaches_the_user(app, fake_engine):
    bot = new_bot(app)
    assert app.notify("weebo", "From a bot", "x", {"conversation_id": bot["conversation_id"]}) is None
    agent_ctx = ToolContext(app, "t", "turn", conversation_id=bot["conversation_id"], task_id="task-1",
                            trigger="agent", bot_id=bot["id"])
    await registry.call(agent_ctx, "notify_user", {"title": "Agent of a bot"})
    assert not app.store.list_notifications()
    assert app.store.list_journal(kinds=("bots.muted",))


async def test_older_chat_housekeeping_leaves_bots_alone(app, fake_engine):
    bot = new_bot(app, routines=[{"text": "Check mail", "when": "tomorrow 9am", "recurrence": "daily"}])
    app.conversations.move_into_main()
    reminder_id = app.bots.get(bot["id"])["data"]["routine_ids"][0]
    assert app.store.get_reminder(reminder_id)["conversation_id"] == bot["conversation_id"]
    await app.conversations.send(bot["conversation_id"], "hi")
    thread = app.store.get_conversation(bot["conversation_id"])["thread_id"]
    assert app.threads.in_use(thread)
    app.bots.set_status(bot["id"], "paused", actor="user")
    assert app.threads.in_use(thread)
    assert app.store.get_reminder(reminder_id)["status"] == "paused"


async def test_chief_sees_a_lean_team_summary(app, fake_engine):
    bot = new_bot(app)
    app.reports.file(app.bots.get(bot["id"]), "done", "SECRET TRANSCRIPT LINE", hops=1)
    await app.conversations.send(app.conversations.main_id(), "how's the team?")
    context = fake_engine.turns[-1]["context"]
    assert "Your team" in context and "Scout" in context
    assert "SECRET TRANSCRIPT LINE" not in context  # summaries go to the brief, not every turn
