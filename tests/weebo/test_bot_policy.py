"""Weebo 3 Phase 3: approval tiers, "ask once, remember", batching, and who hears about what."""

import asyncio

import pytest

from weebo.brain.tools import ToolContext, registry

from .conftest import drain

pytestmark = pytest.mark.asyncio

COMMAND = "item/commandExecution/requestApproval"


def ctx(app, trigger="user", **kw):
    return ToolContext(app, "thread-x", "turn-1", conversation_id=app.conversations.main_id(), trigger=trigger, **kw)


def new_bot(app, name="Coder", **profile):
    bot = app.bots.propose({"name": name, "job": "Fixes code.", "tier": "fast", "why": "x", **profile})
    return app.bots.approve(bot["id"])


async def ask(app, bot, command, task_id=None):
    params = {"threadId": "bot-thread", "turnId": "t1", "command": command, "cwd": "/tmp"}
    return await app.interactions.ask(COMMAND, params, conversation_id=bot["conversation_id"], task_id=task_id,
                                      agent=bot["name"])


async def test_tier_one_the_bot_does_what_its_autonomy_allows(app):
    bot = new_bot(app, autonomy=["read", "act"])
    response = await ask(app, bot, "pytest -q")
    assert response == {"decision": "accept"}
    assert not app.approvals.open_items()


async def test_tier_three_the_user_decides_on_one_bundled_card(app):
    bot = new_bot(app, autonomy=["read"])
    first = await ask(app, bot, "pytest -q")
    second = await ask(app, bot, "git push origin fix")
    assert first == {"decision": "decline"} and second == {"decision": "decline"}  # the bot never hangs waiting
    items = app.approvals.open_items()
    assert [i["data"]["action"] for i in items] == ["command", "send"]
    cards = [m for m in app.store.list_messages(app.conversations.main_id()) if m["kind"] == "needs_you"]
    assert len(cards) == 1 and len(cards[0]["data"]["items"]) == 2 and cards[0]["status"] == "pending"
    assert not app.store.list_notifications()  # not urgent: batched, no interruption
    assert "Waiting on the user" in app.bots.digest(mark_delivered=False)
    again = await ask(app, bot, "git push origin fix")  # a retry doesn't pile up
    assert again == {"decision": "decline"} and len(app.approvals.open_items()) == 2


async def test_urgent_needs_interrupt(app):
    bot = new_bot(app, autonomy=["read"])
    job = await app.bots.assign_job(bot["id"], "Ship the hotfix.", actor="chief", hops=1)
    app.store._update("bot_jobs", "id", job["id"], {"data": {**app.bots.get_job(job["id"])["data"], "urgent": True}})
    await ask(app, bot, "git push origin hotfix")
    assert [n["title"] for n in app.store.list_notifications()] == ["Coder needs your OK"]


async def test_tier_two_standing_rules_decide_for_the_user(app):
    bot = new_bot(app, autonomy=["read"])
    app.approvals.add_rule(bot["id"], "command", "pytest", "user")
    assert await ask(app, bot, "pytest -q tests") == {"decision": "accept"}
    assert any("standing rule" in row["title"] for row in app.store.list_journal(kinds=("policy",)))
    assert "Decided for the user" in app.bots.digest(mark_delivered=False)
    assert await ask(app, bot, "npm test") == {"decision": "decline"}  # outside the rule


async def test_hard_gates_open_only_with_the_users_own_rule(app):
    bot = new_bot(app, autonomy=["read", "act"])
    assert await ask(app, bot, "git push origin fix") == {"decision": "decline"}  # act doesn't cover sending
    app.approvals.add_rule(bot["id"], "send", "git push", "chief")  # the chief can't open a hard gate
    assert await ask(app, bot, "git push origin fix") == {"decision": "decline"}
    app.approvals.add_rule(bot["id"], "send", "git push", "user")
    assert await ask(app, bot, "git push origin fix") == {"decision": "accept"}
    assert await ask(app, bot, "rm -rf build") == {"decision": "decline"}  # deleting is its own gate


async def test_ask_once_remember(app, fake_engine):
    bot = new_bot(app, autonomy=["read"])
    await ask(app, bot, "make deploy")
    item = app.approvals.open_items()[0]
    await app.approvals.resolve(item["id"], "always", actor="user")
    assert app.approvals.open_items() == []
    rule = app.approvals.rules(bot["id"])[0]
    assert rule["action"] == "command" and rule["target"] == "make deploy" and not rule["data"]["once"]
    assert "The user approved" in fake_engine.turns[-1]["input"][0]["text"]  # the bot is told to go ahead
    for _ in range(2):
        assert await ask(app, bot, "make deploy") == {"decision": "accept"}


async def test_approve_once_is_used_up(app, fake_engine):
    bot = new_bot(app, autonomy=["read"])
    await ask(app, bot, "make release")
    await app.approvals.resolve(app.approvals.open_items()[0]["id"], "approve", actor="user")
    assert await ask(app, bot, "make release") == {"decision": "accept"}
    assert await ask(app, bot, "make release") == {"decision": "decline"}


async def test_deny_rules_win_and_are_revocable(app):
    bot = new_bot(app, autonomy=["read", "act"])
    rule = app.approvals.add_rule(bot["id"], "command", "terraform", "user", effect="deny")
    assert await ask(app, bot, "terraform apply") == {"decision": "decline"}
    assert not app.approvals.open_items()  # denied outright, nobody bothered
    assert await registry.call(ctx(app, trigger="routine"), "revoke_standing_rule", {"rule_id": rule["id"]})
    assert await ask(app, bot, "terraform plan") == {"decision": "accept"}


async def test_user_talking_in_a_bots_chat_gets_the_card_there(app, fake_engine):
    bot = new_bot(app, autonomy=["read"])
    await app.conversations.send(bot["conversation_id"], "run the tests please")
    task = asyncio.get_running_loop().create_task(ask(app, bot, "pytest -q"))
    await drain(5)
    cards = [m for m in app.store.list_messages(bot["conversation_id"]) if m["kind"] == "approval"]
    assert cards and cards[0]["status"] == "pending"
    pending = app.interactions.list()[0]
    app.interactions.resolve(pending["id"], "acceptAlways")
    assert (await task) == {"decision": "acceptForSession"}
    assert app.approvals.rules(bot["id"])[0]["data"]["added_by"] == "user"  # remembered as a standing rule


async def test_report_rules_from_what_the_user_said(app):
    bot = app.bots.get(new_bot(app)["id"])
    refused = await registry.call(ctx(app, trigger="bot_report"), "set_report_rule",
                                  {"scope": "Coder", "match": "PR fix", "bucket": "log"})
    assert not refused.success
    saved = await registry.call(ctx(app, trigger="user"), "set_report_rule",
                                {"scope": "Coder", "match": "PR fix", "bucket": "log"})
    assert saved.success
    assert app.reports.file(bot, "done", "Nightly PR fixes merged.", hops=1)["bucket"] == "log"
    assert app.reports.file(bot, "done", "Refactored the parser.", hops=1)["bucket"] == "brief"
    # trouble escapes log-only, and needing attention interrupts
    assert app.reports.file(bot, "failed", "PR fix broke the build.", hops=1)["bucket"] == "brief"
    assert app.reports.file(bot, "blocked", "PR fix needs a secret.", needs_attention=True, hops=9)["bucket"] == "interrupt"
    logged = await registry.call(ctx(app), "list_bot_reports", {"bucket": "log", "query": "nightly"})
    assert "Nightly PR fixes" in logged.text  # log-only stays searchable


async def test_tell_user_jobs_reach_the_chief(app, fake_engine):
    bot = app.bots.get(new_bot(app)["id"])
    job = await app.bots.assign_job(bot["id"], "Watch PR 12 until green.", report_on="done", tell_user=True,
                                    actor="chief", hops=1)
    assert app.reports.file(bot, "update", "Still red.", job_id=job["id"], hops=1)["bucket"] == "brief"
    assert app.reports.file(bot, "done", "PR 12 is green.", job_id=job["id"], hops=1)["bucket"] == "interrupt"


async def test_decide_request_needs_the_users_yes(app, fake_engine):
    bot = new_bot(app, autonomy=["read"])
    await ask(app, bot, "make deploy")
    item = app.approvals.open_items()[0]
    blocked = await registry.call(ctx(app, trigger="bot_report"), "decide_request",
                                  {"item_id": item["id"], "decision": "approve"})
    assert not blocked.success and app.approvals.open_items()
    denied = await registry.call(ctx(app, trigger="bot_report"), "decide_request",
                                 {"item_id": item["id"], "decision": "deny"})
    assert denied.success and not app.approvals.open_items()  # saying no is always allowed


async def test_outward_integrations_from_a_bot_are_hard_gates(app):
    bot = new_bot(app, autonomy=["read", "act"])
    ok = await app.interactions.confirm("Use integration: send_text_message", "send_text_message({...})",
                                        conversation_id=bot["conversation_id"])
    assert ok is False  # act autonomy doesn't cover texting people
    item = app.approvals.open_items()[0]
    assert item["data"]["action"] == "contact_people" and item["data"]["hard"]
    booked = await app.interactions.confirm("Use integration: schedule_event", "schedule_event({...})",
                                            conversation_id=bot["conversation_id"])
    assert booked is False and app.approvals.open_items()[-1]["data"]["action"] == "send"
