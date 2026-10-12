"""One chat: Weebo runs itself from the conversation (settings, agents, upgrades), keeps up with a long history,
and brings results to the bottom of the chat."""

import json
import subprocess
import time

import pytest

from weebo import paths
from weebo.brain.tools import ToolContext, registry
from weebo.evolution import engine as evo
from tests.weebo.test_evolution import evolving, git, repo, wait_status  # noqa: F401  (fixtures)

pytestmark = pytest.mark.asyncio


def said(app, conv_id, trigger="user"):
    return ToolContext(app=app, thread_id="t", turn_id="turn-1", conversation_id=conv_id, trigger=trigger)


async def ready_upgrade(app, state, title, files):
    state["script"].append(files)
    proposal = await app.evolution.propose(title, f"{title}.")
    assert (await wait_status(app, proposal["id"], ("ready", "failed")))["status"] == "ready"
    return proposal


# ---------------------------------------------------------------- "merge them all, tell me when it's done"
async def test_merging_several_upgrades_restarts_once_after_the_reply(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    main = app.conversations.main_id()
    first = await ready_upgrade(app, state, "Core tweak", {"weebo/core.py": "VALUE = 2\n"})
    second = await ready_upgrade(app, state, "Helper module", {"weebo/helper.py": "HELP = True\n"})
    session = app.conversations.session(main)
    from weebo.brain.conversation import TurnState
    session.turn = TurnState(trigger="user")  # the reply that asked for the merge is still being written

    result = await registry.call(said(app, main), "merge_upgrades", {"all_ready": True})
    assert result.success and "Merged" in result.text and "restarts right after this reply" in result.text
    assert {app.store.get_proposal(p["id"])["status"] for p in (first, second)} == {"merged"}
    assert state["restarts"] == []  # not mid-reply
    pending = json.loads((paths.data_dir() / evo.PENDING_FILE).read_text())
    assert pending["proposal_ids"] == [first["id"], second["id"]] and len(pending["merged_commits"]) == 2
    assert pending["report_to"] == main
    assert app.store.get_proposal(first["id"])["meta"]["approved_by"] == "user in chat"
    session.turn = None
    await app.evolution._restart_task
    assert state["restarts"] == ["Applying a self-upgrade"]


async def test_upgrades_wont_merge_because_a_report_said_so(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    proposal = await ready_upgrade(app, state, "Core tweak", {"weebo/core.py": "VALUE = 3\n"})
    for trigger in ("agent_report", "routine", "brief"):
        result = await registry.call(said(app, app.conversations.main_id(), trigger), "merge_upgrades",
                                     {"proposal_ids": [proposal["id"]]})
        assert not result.success and "Only the user" in result.text
    agent_ctx = ToolContext(app=app, thread_id="t", task_id="t_1", trigger="agent")
    assert not (await registry.call(agent_ctx, "restart_weebo", {})).success
    assert app.store.get_proposal(proposal["id"])["status"] == "ready" and state["restarts"] == []


async def test_a_batch_that_fails_to_boot_is_reverted_whole(repo, data_dir, monkeypatch):
    from weebo import supervisor

    base = git(repo, "rev-parse", "HEAD")
    commits = []
    for name, value in (("a", "VALUE = 'a'\n"), ("b", "raise SystemExit('broken')\n")):
        git(repo, "checkout", "-q", "-b", name)
        (repo / "weebo" / f"{name}.py").write_text(value)
        git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "add", "-A")
        git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", name)
        git(repo, "checkout", "-q", "main")
        git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "merge", "--no-ff", "-q", "-m", f"merge {name}", name)
        commits.append(git(repo, "rev-parse", "HEAD"))
    monkeypatch.setattr(supervisor, "ROOT", repo)
    data_dir.mkdir(parents=True, exist_ok=True)
    pending = data_dir / evo.PENDING_FILE
    pending.write_text(json.dumps({"proposal_id": "p_b", "merged_commit": commits[-1], "proposal_ids": ["p_a", "p_b"],
                                   "merged_commits": commits, "previous_head": base}))
    assert supervisor._rollback(pending) is True
    assert not (repo / "weebo" / "a.py").exists() and not (repo / "weebo" / "b.py").exists()  # both, not just the last


async def test_after_the_restart_weebo_reports_back_in_the_chat(app):
    main = app.conversations.main_id()
    ids = []
    for title in ("Faster recall", "Calendar fix"):
        proposal = await app.evolution.propose(title, "x", source="user")
        app.store.update_proposal(proposal["id"], status="merged")
        ids.append(proposal["id"])
    pending = paths.data_dir() / evo.PENDING_FILE
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({"proposal_ids": ids, "merged_commits": ["c1", "c2"], "report_to": main,
                                   "reported": ids}))
    app.evolution._mark_verified(ids, pending, main, ids)
    last = app.store.list_messages(main)[-1]
    assert last["role"] == "assistant" and "All 2 upgrades are live" in last["content"]
    assert "Faster recall" in last["content"] and not pending.exists()
    assert all(app.store.get_proposal(i)["meta"].get("verified_at") for i in ids)

    pending.write_text(json.dumps({"proposal_ids": ids[:1], "rolled_back": True, "report_to": main}))
    app.evolution._verify_after_upgrade()
    last = app.store.list_messages(main)[-1]
    assert "rolled back" in last["content"] and app.store.get_proposal(ids[0])["status"] == "rolled_back"


# ---------------------------------------------------------------- bringing things in front of the user
async def test_show_upgrade_puts_its_card_at_the_bottom_of_the_chat(app):
    main = app.conversations.main_id()
    proposal = await app.evolution.propose("Faster recall", "Make memory search quicker.", source="user",
                                           conversation_id=main)
    for i in range(3):
        app.store.add_message(main, "user", f"chatting {i}")
    result = await registry.call(said(app, main), "show_upgrade", {"proposal_id": proposal["id"]})
    assert result.success and "Faster recall" in result.text
    assert app.store.list_messages(main)[-1]["id"] == f"evolution:{proposal['id']}"


async def test_ideas_from_dreams_reach_the_main_chat_when_they_need_the_user(app):
    proposal = await app.evolution.propose("Tidy memory", "Merge duplicates.", source="self")
    assert app.store.get_message(f"evolution:{proposal['id']}") is None  # the Council's business, not the chat's
    app.store.update_proposal(proposal["id"], status="ready")
    app.evolution._publish(proposal["id"])
    card = app.store.get_message(f"evolution:{proposal['id']}")
    assert card and card["conversation_id"] == app.conversations.main_id()
    app.evolution._publish(proposal["id"])  # once per state: no jumping around on every update
    assert app.store.get_message(card["id"])["seq"] == card["seq"]


async def test_each_agent_has_a_live_card_in_the_chat(app, fake_engine):
    main = app.conversations.main_id()
    task = await app.agents.start("Fix the build", "Make the tests pass.", conversation_id=main)
    card = app.store.get_message(f"agent:{task['id']}")
    assert card["kind"] == "agent_card" and card["data"]["title"] == "Fix the build"
    app.agents._finalize(task["id"], "completed", "All 40 tests pass now.", "")
    card = app.store.get_message(f"agent:{task['id']}")
    assert card["data"]["status"] == "completed" and "40 tests" in card["data"]["summary"]


# ---------------------------------------------------------------- keeping up with one long chat
async def test_search_history_finds_what_was_said_and_what_agents_found(app):
    main = app.conversations.main_id()
    app.store.add_message(main, "user", "Let's book the cabin at Lake Tahoe for the second week of July")
    app.store.add_message(main, "assistant", "Got it, cabin at Lake Tahoe, July 8-15.")
    for i in range(50):
        app.store.add_message(main, "user", f"unrelated {i}")
    task = app.store.create_task("Compare cabins", "x", kind="agent", cwd=".", conversation_id=main)
    app.store.update_task(task["id"], status="completed", summary="Best Tahoe cabin: Pine Hollow, $210/night.")
    result = await registry.call(said(app, main), "search_history", {"query": "tahoe cabin"})
    assert "July 8-15" in result.text and "Pine Hollow" in result.text and "unrelated" not in result.text
    nothing = await registry.call(said(app, main), "search_history", {"query": "zanzibar"})
    assert "Nothing" in nothing.text
    wild = await registry.call(said(app, main), "search_history", {"query": "100%_off"})
    assert "Nothing" in wild.text  # LIKE wildcards in the query are literal


async def test_offered_replies_land_on_the_final_answer(app, fake_engine):
    main = app.conversations.main_id()

    async def on_turn(engine, thread_id, turn_id, record):
        session = app.conversations.session(main)
        await registry.call(ToolContext(app=app, thread_id=thread_id, turn_id=turn_id, conversation_id=main,
                                        trigger="user"), "offer_replies",
                            {"replies": ["I'll check with the seller tonight", "That 10-11 was just my block",
                                         "I've already handled this", "x" * 200, "a fifth"]})
        assert session.turn.suggestions
        await engine.finish_turn(thread_id, turn_id, text="Worth a quick check tonight.")

    fake_engine.on_turn = on_turn
    await app.conversations.send(main, "Pickup is 10-11 Friday")
    deadline = time.time() + 5
    while time.time() < deadline:
        answer = [m for m in app.store.list_messages(main) if m["role"] == "assistant" and m["kind"] == "text"][-1]
        if answer["data"].get("suggestions"):
            break
        import asyncio
        await asyncio.sleep(0.02)
    assert answer["content"] == "Worth a quick check tonight."
    assert answer["data"]["suggestions"][:3] == ["I'll check with the seller tonight", "That 10-11 was just my block",
                                                 "I've already handled this"]
    assert len(answer["data"]["suggestions"]) == 4 and len(answer["data"]["suggestions"][3]) == 60


async def test_the_desk_becomes_the_main_chat(app):
    desk = app.store.find_conversation_by_kind("desk")
    app.store.update_conversation(desk["id"], title="Weebo's Desk")
    assert app.conversations.desk()["title"] == "Weebo" and app.conversations.main_id() == desk["id"]


# ---------------------------------------------------------------- an agent asking for permission asks in the chat
async def test_an_agents_approval_is_asked_in_the_chat_that_launched_it(app, fake_engine):
    import asyncio

    from weebo.agents.manager import AgentRun

    main = app.conversations.main_id()
    task = await app.agents.start("Clean up downloads", "Delete old installers.", conversation_id=main)
    run = AgentRun(task["id"])
    notes = []
    app.bus.subscribe("notify", lambda topic, data: notes.append(data["notification"]))
    app.clients = 1  # a browser is open: the card is enough, no notification on top
    asking = asyncio.create_task(app.agents._on_request(run, "item/commandExecution/requestApproval", {
        "threadId": "th", "turnId": "tu", "command": "rm -rf ~/Downloads/old", "reason": "Free up space"}))
    for _ in range(100):
        cards = [m for m in app.store.list_messages(main) if m["kind"] == "approval"]
        if cards:
            break
        await asyncio.sleep(0.01)
    assert cards and cards[0]["data"]["agent"] == "Clean up downloads" and cards[0]["status"] == "pending"
    assert notes == []
    app.interactions.resolve(cards[0]["data"]["id"], "accept")
    assert (await asyncio.wait_for(asking, 2))["decision"] == "accept"

    # An agent that stops while its question is open: the question expires instead of hanging forever.
    again = asyncio.create_task(app.agents._on_request(run, "item/commandExecution/requestApproval", {
        "threadId": "th", "turnId": "tu", "command": "rm -rf ~/Downloads/older"}))
    for _ in range(100):
        if app.interactions.pending:
            break
        await asyncio.sleep(0.01)
    app.agents._finalize(task["id"], "cancelled", "", "Stopped by the user.")
    assert (await asyncio.wait_for(again, 2))["decision"] == "cancel"
    card = [m for m in app.store.list_messages(main) if m["kind"] == "approval"][-1]
    assert card["status"] == "expired"


# ---------------------------------------------------------------- agent results don't get lost
async def test_an_agents_result_survives_stop_while_weebo_is_busy(app, fake_engine):
    from weebo.brain.conversation import TurnState

    main = app.conversations.main_id()
    task = app.store.create_task("Compare cabins", "x", kind="agent", cwd=".", conversation_id=main)
    session = app.conversations.session(main)
    session.turn = TurnState(trigger="user", turn_id="busy-turn")  # Weebo is mid-reply
    app.store.update_conversation(main, thread_id="th-1")
    app.agents._finalize(task["id"], "completed", "Pine Hollow is the best cabin, $210/night.", "")
    import asyncio
    for _ in range(50):
        if session.followups:
            break
        await asyncio.sleep(0.01)
    reports = [m for m in app.store.list_messages(main) if m["kind"] == "agent_report"]
    assert len(reports) == 1 and "Pine Hollow" in reports[0]["data"]["summary"]  # in the chat right away
    assert session.followups and session.followups[0]["trigger"] == "agent_report"
    await app.conversations.interrupt(main)  # Stop: the queued "tell the user" turn is dropped...
    assert session.followups == []
    assert any("Pine Hollow" in c for c in session.pending_context)  # ...but the next turn still knows


async def test_agents_cut_off_by_a_restart_say_so_in_the_chat(app):
    main = app.conversations.main_id()
    task = app.store.create_task("Fix the build", "x", kind="agent", cwd=".", conversation_id=main)
    app.store.update_task(task["id"], status="running", started_at=time.time())
    app.agents._sync_card(task["id"])
    app.agents.recover_after_restart()
    card = app.store.get_message(f"agent:{task['id']}")
    assert card["data"]["status"] == "interrupted"
    report = [m for m in app.store.list_messages(main) if m["kind"] == "agent_report"][-1]
    assert "interrupted" in report["content"] and report["data"]["task_id"] == task["id"]


async def test_a_restart_waiting_for_builds_happens_when_the_last_queued_one_is_dropped(evolving, repo):
    import asyncio

    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    app.evolution.restart_after_build = "Applying a self-upgrade"  # a merge waits for the builds
    idea = await app.evolution.propose("Queued idea", "Never built.", source="user")
    app.store.update_proposal(idea["id"], status="rejected")  # dropped while it waited in line
    for _ in range(100):
        if state["restarts"]:
            break
        await asyncio.sleep(0.02)
    assert state["restarts"] == ["Applying a self-upgrade"] and app.evolution.restart_after_build is None
