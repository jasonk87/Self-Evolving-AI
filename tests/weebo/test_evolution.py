"""Self-evolution against a real temporary git repo, with a scripted build agent."""

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest
import pytest_asyncio

from weebo import paths
from weebo.evolution import engine as evo
from weebo.evolution.gates import GateReport, GateResult
from tests.weebo.conftest import drain

pytestmark = pytest.mark.asyncio


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "weebo" / "web").mkdir(parents=True)
    (root / "weebo" / "web" / "app.js").write_text("export const version = 1;\n")
    (root / "weebo" / "core.py").write_text("VALUE = 1\n")
    (root / ".gitignore").write_text("weebo_data/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "-c", "user.name=T", "-c", "user.email=t@t", "add", "-A")
    git(root, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    monkeypatch.setattr(paths, "PROJECT_ROOT", root)
    return root


@pytest_asyncio.fixture
async def evolving(app, repo, monkeypatch):
    """App whose build agent edits files according to ``script`` and whose gates are scripted too."""
    state = {"script": [], "gates": [], "reviews": [], "council": [], "restarts": [], "stages": [], "convened": [],
             "gate_kwargs": []}

    async def fake_start(title, instructions, cwd=None, kind="agent", meta=None, effort=None, **_):
        task = app.store.create_task(title, instructions, kind=kind, cwd=cwd, meta=meta or {})
        edit = state["script"].pop(0) if state["script"] else None
        if edit:
            for rel, content in edit.items():
                target = Path(cwd) / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        app.store.update_task(task["id"], status="completed", summary="done", finished_at=time.time())
        return app.store.get_task(task["id"])

    async def fake_wait(task_id, timeout=None):
        return app.store.get_task(task_id)

    def note_stage():
        meta = app.store.get_proposal(app.evolution.current)["meta"]
        state["stages"].append((meta.get("stage"), meta.get("round")))

    async def fake_gates(worktree, changed, test_command="", **kwargs):
        note_stage()
        state["gate_kwargs"].append(kwargs)
        ok = state["gates"].pop(0) if state["gates"] else True
        return GateReport([GateResult("Tests", ok, "" if ok else "1 failed: test_thing")])

    async def fake_review(worktree, base_ref, count=True):
        note_stage()
        assert git(repo, "rev-parse", base_ref) == app.store.get_proposal(app.evolution.current)["base_commit"]
        return state["reviews"].pop(0) if state["reviews"] else {"text": "LGTM", "blocking": False}

    async def fake_convene(_app, proposal):
        state["convened"].append(proposal["id"])
        return state["council"].pop(0) if state["council"] else {"approved": True, "reason": "Worth it.", "value": "high"}

    monkeypatch.setattr(app.agents, "start", fake_start)
    monkeypatch.setattr(app.agents, "wait", fake_wait)
    monkeypatch.setattr(evo, "run_gates", fake_gates)
    monkeypatch.setattr(app.evolution, "_codex_review", fake_review)
    monkeypatch.setattr(evo.council, "convene", fake_convene)
    monkeypatch.setattr(app.heartbeat, "budget", lambda: (True, ""))
    monkeypatch.setattr(app, "request_restart", lambda reason: state["restarts"].append(reason))
    app.evolution._worker = asyncio.create_task(app.evolution._work())
    yield app, state
    app.evolution._worker.cancel()


async def wait_status(app, proposal_id, statuses, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        proposal = app.store.get_proposal(proposal_id)
        if proposal["status"] in statuses:
            return proposal
        await asyncio.sleep(0.05)
    raise AssertionError(f"proposal stuck at {app.store.get_proposal(proposal_id)['status']}")


async def test_build_produces_verified_branch(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/web/app.js": "export const version = 2;\n"}]
    proposal = await app.evolution.propose("Bump UI version", "Change version to 2.", "testing")
    ready = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert ready["status"] == "ready", ready["gate_report"]
    meta = ready["meta"]
    assert meta["changed"] == ["weebo/web/app.js"]
    assert meta["governance"]["autonomous"] is True
    assert "+export const version = 2;" in meta["diff"]
    author = git(Path(ready["worktree"]), "log", "-1", "--format=%an")
    assert author == "Weebo"
    assert git(repo, "status", "--porcelain") == ""  # main tree untouched


async def test_failed_checks_trigger_revision_round(evolving):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/core.py": "VALUE = 2\n"}, {"weebo/core.py": "VALUE = 3\n"}]
    state["gates"] = [False, True]
    proposal = await app.evolution.propose("Fix value", "Set VALUE correctly.")
    ready = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert ready["status"] == "ready"
    rounds = ready["meta"]["rounds"]
    assert [r["gates_ok"] for r in rounds] == [False, True]
    assert ready["meta"]["governance"]["autonomous"] is False  # core code needs a human


async def test_build_fails_after_max_rounds(evolving):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/core.py": f"VALUE = {i}\n"} for i in range(10, 15)]
    state["gates"] = [False, False, False]
    proposal = await app.evolution.propose("Doomed", "Never passes.")
    failed = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert failed["status"] == "failed" and len(failed["meta"]["rounds"]) == evo.MAX_ROUNDS


async def test_user_ideas_skip_the_council(evolving):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/web/app.js": "export const version = 3;\n"}]
    proposal = await app.evolution.propose("User asked for this", "Change version.", source="user")
    ready = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert ready["status"] == "ready" and state["convened"] == [] and "council" not in ready["meta"]
    assert state["stages"] == [("testing", 1), ("reviewing", 1)] and ready["meta"]["stage"] is None


async def test_council_declines_own_idea_before_anything_is_built(evolving):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["council"] = [{"approved": False, "reason": "Speculative; no errors show this is needed.", "value": "low"}]
    proposal = await app.evolution.propose("Rewrite the logger", "Make logs fancier.", source="dream")
    assert proposal["status"] == "vetting"
    declined = await wait_status(app, proposal["id"], ("declined",))
    assert declined["meta"]["council"]["reason"].startswith("Speculative")
    assert app.store.list_tasks(limit=10) == [] and not declined.get("worktree")  # no build was spent on it
    # The user can still overrule the Council.
    state["script"] = [{"weebo/web/app.js": "export const version = 4;\n"}]
    await app.evolution.approve(proposal["id"])
    assert (await wait_status(app, proposal["id"], ("ready", "failed")))["status"] == "ready"


async def test_council_approval_lets_own_idea_build(evolving):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/web/app.js": "export const version = 5;\n"}]
    proposal = await app.evolution.propose("Speed up the stage", "Cache the avatar.", source="self-audit")
    ready = await wait_status(app, proposal["id"], ("ready", "failed", "declined"))
    assert ready["status"] == "ready" and state["convened"] == [proposal["id"]]
    assert ready["meta"]["council"]["approved"] is True


async def test_council_unavailable_never_builds(evolving):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["council"] = [{"approved": None, "reason": "The Council couldn't meet: offline", "value": None}]
    proposal = await app.evolution.propose("Add sparkles", "More sparkles.", source="conversation")
    waiting = await wait_status(app, proposal["id"], ("proposed", "queued", "declined"))
    assert waiting["status"] == "proposed" and app.store.list_tasks(limit=10) == []
    app.heartbeat.budget = lambda: (False, "Daily background budget is used up.")
    over = await app.evolution.propose("Add confetti mode", "Confetti everywhere.", source="dream")
    waiting = await wait_status(app, over["id"], ("proposed", "queued", "declined"))
    assert waiting["status"] == "proposed" and "budget" in waiting["meta"]["council"]["reason"]
    assert state["convened"] == [proposal["id"]]  # over budget: the Council isn't even convened


async def test_review_that_cannot_run_fails_closed(evolving):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/web/app.js": "export const version = 6;\n"}]
    state["reviews"] = [{"text": "Review did not complete", "blocking": True, "error": "timed out"}] * 2
    proposal = await app.evolution.propose("Unreviewed", "Change version.")
    failed = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert failed["status"] == "failed" and len(failed["meta"]["rounds"]) == 1
    assert "couldn't run" in failed["gate_report"]


async def test_codex_review_covers_only_the_agents_change(app):
    """The review must diff against the build's base snapshot, and a failed review turn must block."""
    engine = app.engine

    async def run(status, text):
        task = asyncio.create_task(app.evolution._codex_review(Path("."), "weebo/base-1234"))
        await drain(5)
        thread_id = engine.threads[-1]["id"]
        if text:
            await engine.emit(thread_id, "item/completed", {"item": {"type": "exitedReviewMode", "review": text}})
        await engine.emit(thread_id, "turn/completed", {"turn": {"id": "review-turn", "status": status}})
        return await task

    clean = await run("completed", "No issues found.")
    assert engine.reviews[-1]["target"] == {"type": "baseBranch", "branch": "weebo/base-1234"}
    assert clean == {"text": "No issues found.", "blocking": False}
    blocking = await run("completed", "- [P1] Leaks the token")
    assert blocking["blocking"] is True and "error" not in blocking
    broken = await run("failed", "")
    assert broken["blocking"] is True and broken["error"]


async def test_merge_ui_change_reloads_without_restart(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/web/app.js": "export const version = 3;\n"}]
    reloads = []
    app.bus.subscribe("ui.reload", lambda t, d: reloads.append(d))
    proposal = await app.evolution.propose("UI tweak", "Version 3.")
    await wait_status(app, proposal["id"], ("ready",))
    merged = await app.evolution.merge(proposal["id"])
    assert merged["status"] == "merged"
    assert (repo / "weebo" / "web" / "app.js").read_text() == "export const version = 3;\n"
    assert "Weebo evolution: UI tweak" in git(repo, "log", "-1", "--format=%s")
    assert reloads and not state["restarts"]
    assert not Path(merged["worktree"]).exists()
    assert merged["branch"] not in git(repo, "branch", "--list")


async def test_merge_python_change_restarts_and_marks_pending(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/core.py": "VALUE = 42\n"}]
    proposal = await app.evolution.propose("Core change", "VALUE 42.")
    await wait_status(app, proposal["id"], ("ready",))
    merged = await app.evolution.merge(proposal["id"])
    assert state["restarts"] == ["Applying a self-upgrade"]
    pending = json.loads((paths.data_dir() / evo.PENDING_FILE).read_text())
    assert pending["proposal_id"] == proposal["id"] and pending["merged_commit"] == merged["merged_commit"]


async def test_merge_refuses_dirty_tree_and_handles_conflict(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/core.py": "VALUE = 'branch'\n"}]
    proposal = await app.evolution.propose("Conflicting", "Change VALUE.")
    await wait_status(app, proposal["id"], ("ready",))
    (repo / "weebo" / "core.py").write_text("VALUE = 'dirty'\n")
    head_before = git(repo, "rev-parse", "HEAD")
    app.settings.update({"evolution.checkpoint_commits": False})
    with pytest.raises(evo.EvolutionError, match="uncommitted"):
        await app.evolution.merge(proposal["id"])
    app.settings.update({"evolution.checkpoint_commits": True})
    with pytest.raises(evo.EvolutionError, match="conflicts"):
        await app.evolution.merge(proposal["id"])
    assert app.store.get_proposal(proposal["id"])["status"] == "conflict"
    # The checkpoint was undone: the user's edit is back, uncommitted, and history is untouched.
    assert git(repo, "rev-parse", "HEAD") == head_before
    assert git(repo, "status", "--porcelain") == "M weebo/core.py"
    assert (repo / "weebo" / "core.py").read_text() == "VALUE = 'dirty'\n"


async def test_auto_merge_only_for_low_risk_zones(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "auto_merge"})
    state["script"] = [{"weebo/web/app.js": "export const version = 9;\n"}, {"weebo/core.py": "VALUE = 9\n"}]
    ui = await app.evolution.propose("Auto UI", "v9")
    assert (await wait_status(app, ui["id"], ("merged",)))["status"] == "merged"
    core = await app.evolution.propose("Auto core", "VALUE 9")
    await wait_status(app, core["id"], ("ready", "merged"))
    await asyncio.sleep(0.5)
    assert app.store.get_proposal(core["id"])["status"] == "ready"  # core code waits for a human


async def test_rollback_reverts_merge(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/web/app.js": "export const version = 5;\n"}]
    proposal = await app.evolution.propose("Temp", "v5")
    await wait_status(app, proposal["id"], ("ready",))
    await app.evolution.merge(proposal["id"])
    rolled = await app.evolution.rollback(proposal["id"])
    assert rolled["status"] == "rolled_back"
    assert (repo / "weebo" / "web" / "app.js").read_text() == "export const version = 1;\n"


async def test_duplicate_proposals_are_coalesced(app):
    app.settings.update({"evolution.mode": "propose"})
    first = await app.evolution.propose("Add a weather tool", "Use web search")
    second = await app.evolution.propose("Add weather tool", "Same idea")
    assert first["id"] == second["id"]


async def test_supervisor_reverts_upgrade_that_cannot_boot(repo, data_dir, monkeypatch):
    from weebo import supervisor
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "weebo" / "core.py").write_text("raise SystemExit('broken')\n")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qam", "break")
    git(repo, "checkout", "-q", "main")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "merge", "--no-ff", "-q", "-m", "merge feature", "feature")
    merged = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(supervisor, "ROOT", repo)
    pending = data_dir / evo.PENDING_FILE
    data_dir.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({"proposal_id": "p_x", "merged_commit": merged, "previous_head": base}))
    assert supervisor._rollback(pending) is True
    assert (repo / "weebo" / "core.py").read_text() == "VALUE = 1\n"
    assert json.loads(pending.read_text())["rolled_back"] is True
    assert supervisor._rollback(pending) is False  # never twice


async def test_builds_on_uncommitted_work_and_merge_checkpoints_it(evolving, repo):
    """Self-evolution must work even while the code it runs is uncommitted (no 'commit first' wall)."""
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    (repo / "weebo" / "new_module.py").write_text("X = 1\n")  # untracked work in progress
    (repo / "weebo" / "core.py").write_text("VALUE = 2\n")  # uncommitted edit
    head_before = git(repo, "rev-parse", "HEAD")
    state["script"] = [{"weebo/web/app.js": "export const version = 7;\n"}]
    proposal = await app.evolution.propose("Works without a commit", "Bump UI.")
    ready = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert ready["status"] == "ready"
    worktree = Path(ready["worktree"])
    assert (worktree / "weebo" / "new_module.py").read_text() == "X = 1\n"  # the build saw the uncommitted work
    assert ready["meta"]["changed"] == ["weebo/web/app.js"]  # ...but only its own change is the upgrade
    assert git(repo, "rev-parse", "HEAD") == head_before  # building never touched the user's branch
    merged = await app.evolution.merge(proposal["id"])
    assert merged["status"] == "merged" and merged["meta"]["checkpoint"]
    assert git(repo, "status", "--porcelain") == ""
    subjects = git(repo, "log", "--first-parent", "--format=%s", "-2").splitlines()  # merge, then the checkpoint
    assert subjects[0].startswith("Weebo evolution: Works without a commit")
    assert subjects[1].startswith("Checkpoint: save work before Weebo upgrade")
    assert (repo / "weebo" / "new_module.py").read_text() == "X = 1\n"
    assert (repo / "weebo" / "core.py").read_text() == "VALUE = 2\n"
    assert (repo / "weebo" / "web" / "app.js").read_text() == "export const version = 7;\n"


async def test_rejected_while_queued_is_never_built(evolving):
    app, state = evolving
    app.settings.update({"evolution.mode": "propose"})
    proposal = await app.evolution.propose("Change of heart", "Never mind.")
    app.store.update_proposal(proposal["id"], status="queued")
    await app.evolution._queue.put(proposal["id"])
    await app.evolution.reject(proposal["id"])
    await asyncio.sleep(0.3)
    assert app.store.get_proposal(proposal["id"])["status"] == "rejected" and not state["restarts"]
    assert app.store.get_proposal(proposal["id"]).get("branch") is None


async def test_locked_old_worktree_is_moved_aside_for_rebuild(evolving, monkeypatch):
    """Codex's sandbox account can leave folders we may not delete; a rebuild must still get a clean worktree."""
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/web/app.js": "export const version = 7;\n"}, {"weebo/web/app.js": "export const version = 8;\n"}]
    proposal = await app.evolution.propose("Locked leftovers", "Change version.")
    first = await wait_status(app, proposal["id"], ("ready", "failed"))
    worktree = Path(first["worktree"])
    (worktree / ".verification-tmp").mkdir()
    monkeypatch.setattr(evo.shutil, "rmtree", lambda *a, **k: None)  # simulate files we can't delete
    monkeypatch.setattr(evo.git, "git", _skip_worktree_remove(evo.git.git))
    assert first["status"] == "ready"
    await app.evolution.approve(proposal["id"])  # the panel's Rebuild button
    rebuilt = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert rebuilt["status"] == "ready", rebuilt["gate_report"]
    trash = list((worktree.parent / ".trash").iterdir())
    assert trash and trash[0].name.startswith(worktree.name)


def _skip_worktree_remove(real_git):
    async def fake(root, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            return await real_git(root, "status", **{k: v for k, v in kwargs.items() if k == "timeout"})
        return await real_git(root, *args, **kwargs)
    return fake


async def test_agent_scratch_files_are_never_committed(evolving):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/web/app.js": "export const version = 9;\n", ".weebo-tmp/pytest/log.txt": "scratch",
                        ".verification-tmp/x.patch": "scratch"}]
    proposal = await app.evolution.propose("Scratch stays out", "Change version.")
    ready = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert ready["status"] == "ready" and ready["meta"]["changed"] == ["weebo/web/app.js"]


async def test_agent_edits_to_untracked_files_merge_cleanly(evolving, repo):
    """Regression: the whole weebo/ package was untracked, so merging the build branch across its snapshot made
    every file the agent touched an add/add conflict. Merges must replay only the agent's commits."""
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    (repo / "weebo" / "brain.py").write_text("A = 1\nB = 2\nC = 3\nD = 4\nE = 5\n")  # new, never committed
    state["script"] = [{"weebo/brain.py": "A = 1\nB = 2\nC = 3\nD = 4\nE = 50\n"}]
    proposal = await app.evolution.propose("Tune brain", "Set B to 20.")
    ready = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert ready["status"] == "ready"
    (repo / "weebo" / "brain.py").write_text("A = 10\nB = 2\nC = 3\nD = 4\nE = 5\n")  # the user kept working
    merged = await app.evolution.merge(proposal["id"])
    assert merged["status"] == "merged"
    assert (repo / "weebo" / "brain.py").read_text() == "A = 10\nB = 2\nC = 3\nD = 4\nE = 50\n"  # both edits kept
    assert git(repo, "status", "--porcelain") == ""


async def test_real_conflict_restores_users_work_untouched(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    (repo / "weebo" / "brain.py").write_text("B = 2\n")
    state["script"] = [{"weebo/brain.py": "B = 20\n"}]
    proposal = await app.evolution.propose("Set B", "B = 20.")
    assert (await wait_status(app, proposal["id"], ("ready", "failed")))["status"] == "ready"
    (repo / "weebo" / "brain.py").write_text("B = 99\n")  # same line changed by the user
    head_before = git(repo, "rev-parse", "HEAD")
    with pytest.raises(evo.EvolutionError):
        await app.evolution.merge(proposal["id"])
    assert app.store.get_proposal(proposal["id"])["status"] == "conflict"
    assert git(repo, "rev-parse", "HEAD") == head_before and (repo / "weebo" / "brain.py").read_text() == "B = 99\n"
    assert "brain.py" in git(repo, "status", "--porcelain")  # still uncommitted, exactly as before


async def test_merge_waits_for_running_build_before_restarting(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    state["script"] = [{"weebo/core.py": "VALUE = 5\n"}]
    proposal = await app.evolution.propose("Core tweak", "VALUE = 5.")
    assert (await wait_status(app, proposal["id"], ("ready", "failed")))["status"] == "ready"
    app.evolution.current = "p_other_build"  # another build is mid-flight
    merged = await app.evolution.merge(proposal["id"])
    assert merged["status"] == "merged" and state["restarts"] == []
    assert app.evolution.restart_after_build == "Applying a self-upgrade"
    app.evolution.current = None
    state["script"] = [{"weebo/web/app.js": "export const version = 11;\n"}]
    other = await app.evolution.propose("UI tweak", "Version 11.")
    await wait_status(app, other["id"], ("ready", "failed"))
    await asyncio.sleep(0.1)
    assert state["restarts"] == ["Applying a self-upgrade"] and app.evolution.restart_after_build is None


async def test_user_requested_builds_dont_spend_the_proactive_budget(app, monkeypatch):
    calls = []

    async def fake_start_review(thread_id, target):
        calls.append(thread_id)
        await app.engine.emit(thread_id, "turn/completed", {"turn": {"id": "r", "status": "completed"}})

    monkeypatch.setattr(app.engine, "start_review", fake_start_review)
    before = app.background_turns_today()
    await app.evolution._codex_review(Path("."), "weebo/base-x", count=False)
    assert app.background_turns_today() == before
    await app.evolution._codex_review(Path("."), "weebo/base-x")
    assert app.background_turns_today() == before + 1 and len(calls) == 2
