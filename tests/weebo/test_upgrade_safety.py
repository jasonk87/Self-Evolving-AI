"""Merging and restarting stay safe when several things happen at once: no merge half-done by a restart, a rollback
note never lost or clobbered, a failed batch revert never leaves half the batch, and upgrade cards reach the user."""

import asyncio
import json

import pytest

from weebo import paths
from weebo.brain.tools import registry
from weebo.evolution import engine as evo
from tests.weebo.test_evolution import evolving, git, repo  # noqa: F401  (fixtures)
from tests.weebo.test_one_chat import ready_upgrade, said

pytestmark = pytest.mark.asyncio


def done(proposal_id, commit, needs_restart=True):
    return {"proposal_id": proposal_id, "title": proposal_id, "merged_commit": commit, "previous_head": "c0",
            "needs_restart": needs_restart}


# ---------------------------------------------------------------- merges and the restart
async def test_no_merge_starts_once_a_restart_is_decided(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    first = await ready_upgrade(app, state, "Core tweak", {"weebo/core.py": "VALUE = 2\n"})
    second = await ready_upgrade(app, state, "Helper module", {"weebo/helper.py": "HELP = True\n"})
    await app.evolution.merge(first["id"])  # from the panel: Python changed, so Weebo restarts now
    assert state["restarts"] == ["Applying a self-upgrade"]
    with pytest.raises(evo.EvolutionError, match="restarting"):
        await app.evolution.merge(second["id"])  # would be cut off mid-merge by the exit
    assert app.store.get_proposal(second["id"])["status"] == "ready"
    assert json.loads((paths.data_dir() / evo.PENDING_FILE).read_text())["proposal_ids"] == [first["id"]]


async def test_a_restart_after_the_builds_waits_for_a_merge_in_progress(app, monkeypatch):
    restarts = []
    monkeypatch.setattr(app, "request_restart", restarts.append)
    evolution = app.evolution
    lock = evolution._merge_lock()
    await lock.acquire()  # a merge is mid-way when the last build finishes
    evolution.restart_after_build = "Applying a self-upgrade"
    evolution._restart_if_idle()
    await asyncio.sleep(0.05)
    assert restarts == []
    lock.release()
    await evolution._restart_task
    assert restarts == ["Applying a self-upgrade"] and evolution.restarting


async def test_merging_while_a_build_runs_says_the_restart_waits_for_it(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    proposal = await ready_upgrade(app, state, "Core tweak", {"weebo/core.py": "VALUE = 2\n"})
    app.evolution.current = "p_building"  # another upgrade is being built
    try:
        result = await registry.call(said(app, app.conversations.main_id()), "merge_upgrades",
                                     {"proposal_ids": [proposal["id"]]})
    finally:
        app.evolution.current = None
    assert result.success and "as soon as it finishes" in result.text and "right after this reply" not in result.text
    assert app.evolution.restart_after_build and state["restarts"] == []


# ---------------------------------------------------------------- the supervisor's note
async def test_a_merge_soon_after_an_upgrade_boot_keeps_its_own_rollback_note(app):
    main = app.conversations.main_id()
    ids = []
    for title in ("Faster recall", "Calendar fix"):
        proposal = await app.evolution.propose(title, "x", source="user")
        app.store.update_proposal(proposal["id"], status="merged")
        ids.append(proposal["id"])
    earlier, now = ids
    pending = paths.data_dir() / evo.PENDING_FILE
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({"proposal_ids": [earlier], "merged_commits": ["c1"], "report_to": main,
                                   "reported": [earlier], "at": app.started_at - 5}))  # the last process's merge
    app.evolution._verify_after_upgrade()
    timer = app.evolution._verify_handle
    assert timer is not None

    app.evolution._write_pending([done(now, "c2")], main)  # merged before this boot was confirmed
    assert timer.cancelled() and app.store.get_proposal(earlier)["meta"].get("verified_at")  # confirmed now instead
    note = json.loads(pending.read_text())
    assert note["proposal_ids"] == [now] and note["merged_commits"] == ["c2"]
    app.evolution._mark_verified([earlier], pending, None, [earlier], at=app.started_at - 5)  # a late timer...
    assert json.loads(pending.read_text())["proposal_ids"] == [now]  # ...leaves the new note alone


async def test_a_batch_note_keeps_everything_to_report(app):
    main = app.conversations.main_id()
    pending = paths.data_dir() / evo.PENDING_FILE
    pending.parent.mkdir(parents=True, exist_ok=True)
    app.evolution._write_pending([done("p_a", "c1")], main)
    app.evolution._write_pending([done("p_b", "c2", needs_restart=False), done("p_c", "c3")], None)
    note = json.loads(pending.read_text())
    assert note["proposal_ids"] == ["p_a", "p_c"] and note["merged_commits"] == ["c1", "c3"]
    assert note["reported"] == ["p_a", "p_b", "p_c"] and note["report_to"] == main


async def test_a_rollback_report_names_only_what_was_reverted(app):
    main = app.conversations.main_id()
    ids = []
    for title in ("Faster recall", "New theme"):
        proposal = await app.evolution.propose(title, "x", source="user")
        app.store.update_proposal(proposal["id"], status="merged")
        ids.append(proposal["id"])
    python, page_only = ids
    pending = paths.data_dir() / evo.PENDING_FILE
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({"proposal_ids": [python], "merged_commits": ["c1"], "rolled_back": True,
                                   "report_to": main, "reported": ids}))
    app.evolution._verify_after_upgrade()
    report = app.store.list_messages(main)[-1]["content"]
    reverted, kept = report.split("Still in")
    assert "Faster recall" in reverted and "New theme" not in reverted and "New theme" in kept
    assert app.store.get_proposal(python)["status"] == "rolled_back"
    assert app.store.get_proposal(page_only)["status"] == "merged"


async def test_a_batch_the_supervisor_cant_fully_revert_is_left_whole(repo, data_dir, monkeypatch):
    from weebo import supervisor

    commits = []
    for name in ("a", "b"):
        git(repo, "checkout", "-q", "-b", name)
        (repo / "weebo" / f"{name}.py").write_text(f"VALUE = '{name}'\n")
        git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "add", "-A")
        git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", name)
        git(repo, "checkout", "-q", "main")
        git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "merge", "--no-ff", "-q", "-m", f"merge {name}", name)
        commits.append(git(repo, "rev-parse", "HEAD"))
    (repo / "weebo" / "a.py").write_text("VALUE = 'edited by hand'\n")  # so the older merge can't be reverted
    git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qam", "hand edit")
    before = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(supervisor, "ROOT", repo)
    data_dir.mkdir(parents=True, exist_ok=True)
    pending = data_dir / evo.PENDING_FILE
    pending.write_text(json.dumps({"proposal_ids": ["p_a", "p_b"], "merged_commits": commits}))
    assert supervisor._rollback(pending) is False
    assert git(repo, "rev-parse", "HEAD") == before  # b's revert (which worked) was undone too: never half a batch
    assert (repo / "weebo" / "b.py").exists() and git(repo, "status", "--porcelain") == ""
    assert not json.loads(pending.read_text()).get("rolled_back")


# ---------------------------------------------------------------- upgrade cards reach the user
async def test_upgrades_from_older_chats_come_to_the_main_chat(app):
    main = app.conversations.main_id()
    older = app.conversations.create(title="Trip planning")
    active = await app.evolution.propose("Faster recall", "x", source="user", conversation_id=older["id"])
    app.store.update_proposal(active["id"], status="ready")
    app.evolution._publish(active["id"])
    finished = await app.evolution.propose("Old theme", "x", source="user", conversation_id=older["id"])
    app.store.update_proposal(finished["id"], status="merged")
    assert app.store.get_message(f"evolution:{active['id']}")["conversation_id"] == older["id"]

    app.conversations.move_into_main()  # at startup
    assert app.store.get_proposal(active["id"])["meta"]["conversation_id"] == main
    assert app.store.get_proposal(finished["id"])["meta"]["conversation_id"] == older["id"]  # history stays put
    cards = app.store.list_proposal_messages(main)
    assert [c["id"] for c in cards] == [f"evolution:{active['id']}"]  # its card moved (not copied) to the main chat
    assert not [m for m in app.store.list_messages(older["id"]) if m["id"] == cards[0]["id"]]


async def test_show_upgrade_brings_a_card_from_an_older_chat(app):
    main = app.conversations.main_id()
    older = app.conversations.create(title="Trip planning")
    proposal = await app.evolution.propose("Old theme", "x", source="user", conversation_id=older["id"])
    app.store.update_proposal(proposal["id"], status="merged")
    result = await registry.call(said(app, main), "show_upgrade", {"proposal_id": proposal["id"]})
    assert result.success
    card = app.store.list_messages(main)[-1]
    assert card["id"] == f"evolution:{proposal['id']}" and card["conversation_id"] == main


async def test_a_rebuilt_upgrade_comes_back_to_the_bottom_when_ready_again(app):
    main = app.conversations.main_id()
    proposal = await app.evolution.propose("Faster recall", "x", source="user", conversation_id=main)
    card_id = f"evolution:{proposal['id']}"
    app.store.update_proposal(proposal["id"], status="ready")
    app.evolution._publish(proposal["id"])
    assert app.store.list_messages(main)[-1]["id"] == card_id
    for status in ("queued", "building"):  # the user asked for a rebuild
        app.store.update_proposal(proposal["id"], status=status)
        app.evolution._publish(proposal["id"])
    app.store.add_message(main, "user", "meanwhile, about dinner")
    app.store.update_proposal(proposal["id"], status="ready")
    app.evolution._publish(proposal["id"])
    assert app.store.list_messages(main)[-1]["id"] == card_id


async def test_rolling_back_an_upgrade_takes_it_off_the_boot_note(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    main = app.conversations.main_id()
    first = await ready_upgrade(app, state, "Core tweak", {"weebo/core.py": "VALUE = 2\n"})
    second = await ready_upgrade(app, state, "Helper module", {"weebo/helper.py": "HELP = True\n"})
    app.evolution.current = "p_building"  # the restart waits for a build, so both stay on the note meanwhile
    try:
        await app.evolution.merge_many([first["id"], second["id"]], report_to=main)
    finally:
        app.evolution.current = None
    pending = paths.data_dir() / evo.PENDING_FILE
    merged = json.loads(pending.read_text())
    assert merged["proposal_ids"] == [first["id"], second["id"]]

    await app.evolution.rollback(first["id"])  # before the new version was confirmed
    note = json.loads(pending.read_text())
    assert note["proposal_ids"] == [second["id"]] and note["merged_commits"] == merged["merged_commits"][1:]
    assert note["reported"] == [second["id"]] and note["at"] == merged["at"]
    # Confirmed after the restart: only what is still merged is live.
    app.evolution._mark_verified(merged["proposal_ids"], pending, main, merged["reported"], at=note["at"])
    report = app.store.list_messages(main)[-1]["content"]
    assert "Helper module" in report and "Core tweak" not in report
    assert not app.store.get_proposal(first["id"])["meta"].get("verified_at")


async def test_rolling_back_the_only_upgrade_on_the_note_removes_it(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    proposal = await ready_upgrade(app, state, "Core tweak", {"weebo/core.py": "VALUE = 2\n"})
    app.evolution.current = "p_building"
    try:
        await app.evolution.merge(proposal["id"])
    finally:
        app.evolution.current = None
    pending = paths.data_dir() / evo.PENDING_FILE
    assert pending.exists()
    await app.evolution.rollback(proposal["id"])
    assert not pending.exists()  # a crash on the next boot must not try to revert it a second time
