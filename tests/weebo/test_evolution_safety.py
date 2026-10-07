"""Self-evolution safety: test-rewrite detection, personal details, protected paths, outcomes, behavior gate."""

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

from weebo.evolution import engine as evo
from weebo.evolution import gates, outcomes
from weebo.evolution.council import _history
from tests.weebo.test_evolution import evolving, git, repo, wait_status  # noqa: F401  (fixtures)

pytestmark = pytest.mark.asyncio


def commit(root, message="change"):
    git(root, "-c", "user.name=T", "-c", "user.email=t@t", "add", "-A")
    git(root, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


async def wait_settled(app, proposal_id, timeout=20.0):
    """Wait until the build worker is done with this proposal, auto-merge attempt included.

    A status alone races: an auto-merging build is briefly "ready" while merge() runs its first git commands,
    before it flips to "merging". The worker clears ``evolution.current`` only after merge() has returned.
    """
    await wait_status(app, proposal_id, ("ready", "merged", "failed", "conflict"), timeout)
    deadline = time.time() + timeout
    while app.evolution.current == proposal_id and time.time() < deadline:
        await asyncio.sleep(0.02)
    assert app.evolution.current != proposal_id, "the build worker never finished with this proposal"
    return app.store.get_proposal(proposal_id)


@pytest.fixture
def pyrepo(tmp_path):
    """A tiny project with a real test suite, for gates that run pytest."""
    root = tmp_path / "proj"
    (root / "weebo").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "weebo" / "__init__.py").write_text("")
    (root / "weebo" / "core.py").write_text("VALUE = 1\n")
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_core.py").write_text(
        "from weebo.core import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n")
    git(root, "init", "-q", "-b", "main")
    return root, commit(root, "init")


# ---------------------------------------------------------------- the original tests run against new code
async def test_rewritten_test_is_rerun_in_its_original_form(pyrepo):
    root, base = pyrepo
    (root / "weebo" / "core.py").write_text("VALUE = 2\n")
    (root / "tests" / "test_core.py").write_text(
        "from weebo.core import VALUE\n\n\ndef test_value():\n    assert VALUE == 2\n")  # the "fix" to the test
    commit(root)
    env = {"PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    result = await gates.existing_tests(root, base, env)
    assert result is not None and result.advisory and not result.ok
    assert "tests/test_core.py" in result.output and "no longer hold" in result.output
    report = gates.GateReport([gates.GateResult("Tests", True), result])
    assert report.ok and report.failures() == []  # advisory: shown to the reviewer, doesn't fail the build


async def test_new_tests_alone_are_not_flagged(pyrepo):
    root, base = pyrepo
    (root / "tests" / "test_more.py").write_text("def test_more():\n    assert True\n")
    with (root / "tests" / "test_core.py").open("a") as handle:
        handle.write("\n\ndef test_extra():\n    assert VALUE > 0\n")  # appended, nothing removed
    commit(root)
    assert await gates.existing_tests(root, base, {"PYTHONPATH": str(root)}) is None


async def test_rewritten_test_that_still_passes_is_reported_passing(pyrepo):
    root, base = pyrepo
    (root / "tests" / "test_core.py").write_text(
        "from weebo.core import VALUE\n\n\ndef test_value():\n    assert VALUE >= 1\n")
    commit(root)
    result = await gates.existing_tests(root, base, {"PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.ok and result.advisory and "still pass" in result.output


# ---------------------------------------------------------------- JavaScript syntax
async def test_js_check_catches_broken_es_modules(tmp_path):
    """Regression: plain `node --check file.js` exits 0 on a broken ES module (Node's module-type guess)."""
    node = __import__("shutil").which("node")
    if not node:
        pytest.skip("node not installed")
    broken = tmp_path / "panel.js"
    broken.write_text('import { el } from "./ui.js";\nexport function f() { return el("p")); }\n')
    assert "SyntaxError" in await gates.check_js(node, broken)
    fine_module = tmp_path / "ok.js"
    fine_module.write_text('import { el } from "./ui.js";\nexport const f = () => el("p");\n')
    classic = tmp_path / "classic.js"
    classic.write_text("var x = 010; with (Math) { x = max(x, 1); }\n")  # sloppy-mode script, not a module
    assert await gates.check_js(node, fine_module) == "" and await gates.check_js(node, classic) == ""


# ---------------------------------------------------------------- personal details
async def test_hardcoded_user_details_fail_the_build(pyrepo):
    root, base = pyrepo
    (root / "weebo" / "status.py").write_text(
        'READY = "Waiting for Robin\'s approval"\nOWNER = "robin@example.com"\nWILL = "Robinson"\n')
    commit(root)
    result = await gates.personal_details(root, base, ("Robin", "Robin@Example.com"))
    assert not result.ok and "weebo/status.py:1" in result.output and "weebo/status.py:2" in result.output
    assert "weebo/status.py:3" not in result.output  # a different word that merely starts with the name
    assert (await gates.personal_details(root, base, ())).skipped


# ---------------------------------------------------------------- governance
async def test_rewriting_existing_tests_needs_a_human(evolving, repo):
    app, state = evolving
    (repo / "tests" / "weebo").mkdir(parents=True)
    (repo / "tests" / "weebo" / "test_app.py").write_text("def test_version():\n    assert True\n")
    commit(repo)
    app.settings.update({"evolution.mode": "auto_merge"})
    state["script"] = [{"weebo/web/app.js": "export const version = 2;\n",
                        "tests/weebo/test_app.py": "def test_version():\n    pass\n"}]
    proposal = await app.evolution.propose("UI and a softer test", "v2")
    ready = await wait_settled(app, proposal["id"])
    assert ready["status"] == "ready"  # not auto-merged, although UI and tests are both low-risk zones
    gov = ready["meta"]["governance"]
    assert gov["autonomous"] is False and gov["rewritten_tests"] == ["tests/weebo/test_app.py"]
    assert "existing tests" in gov["why"]


async def test_new_tests_with_ui_change_still_auto_merge(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "auto_merge"})
    state["script"] = [{"weebo/web/app.js": "export const version = 3;\n",
                        "tests/weebo/test_new.py": "def test_new():\n    assert True\n"}]
    proposal = await app.evolution.propose("UI with a new test", "v3")
    settled = await wait_settled(app, proposal["id"])
    assert settled["status"] == "merged", settled["meta"].get("governance")


async def test_protect_paths_are_enforced(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "auto_merge", "evolution.protect_paths": ["weebo/web/*.js"]})
    state["script"] = [{"weebo/web/app.js": "export const version = 4;\n"}]
    proposal = await app.evolution.propose("Protected UI", "v4")
    ready = await wait_settled(app, proposal["id"])
    assert ready["status"] == "ready" and "protected" in ready["meta"]["governance"]["why"]


async def test_protect_path_patterns():
    assert evo.is_protected("weebo/evolution/engine.py", ["weebo/evolution/"])
    assert evo.is_protected("weebo/evolution/engine.py", ["./weebo/evolution"])
    assert evo.is_protected("weebo/supervisor.py", ["weebo/supervisor.py"])
    assert evo.is_protected("weebo/web/css/a.css", ["weebo/web/**"])
    assert not evo.is_protected("weebo/evolutionary.py", ["weebo/evolution"])
    assert not evo.is_protected("weebo/x.py", ["", "  "])


async def test_gates_get_the_users_details_to_look_for(evolving):
    app, state = evolving
    app.settings.update({"evolution.mode": "build", "user.name": "Robin"})
    state["script"] = [{"weebo/web/app.js": "export const version = 5;\n"}]
    proposal = await app.evolution.propose("Anything", "v5")
    await wait_status(app, proposal["id"], ("ready", "failed"))
    kwargs = state["gate_kwargs"][0]
    assert kwargs["personal_terms"] == ("Robin",) and kwargs["base_commit"]


# ---------------------------------------------------------------- outcomes
async def test_fix_that_comes_back_is_reported(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    issue = app.diagnostics.record("turn_failed", "Stream closed before the final reply")
    state["script"] = [{"weebo/web/app.js": "export const version = 6;\n"}]
    proposal = await app.evolution.propose("Keep the stream open", "Reconnect.", "Seen 3x.",
                                           meta={"addresses": [issue["id"], "not-a-real-issue"]})
    assert proposal["meta"]["addresses"] == [issue["id"]]  # unknown ids are dropped
    await wait_status(app, proposal["id"], ("ready",))
    await app.evolution.merge(proposal["id"])
    assert app.diagnostics.get(issue["id"])["status"] == "fixed"
    assert outcomes.review(app) == []  # too early to call
    assert "still watching" in outcomes.describe(app.store.get_proposal(proposal["id"]))

    app.diagnostics.record("turn_failed", "Stream closed before the final reply")  # it happens again
    app.diagnostics.set_status(issue["id"], "reviewed")  # even if an audit already looked at it
    settled = outcomes.review(app)
    assert settled and settled[0]["result"] == "regressed"
    merged = app.store.get_proposal(proposal["id"])
    assert merged["meta"]["outcome"]["result"] == "regressed"
    assert any("didn't fix" in n["title"] for n in app.store.list_notifications())
    assert "came back" in _history(app, "p_other")
    assert outcomes.stats(app)["regressed"] == 1


async def test_fix_that_holds_is_recorded(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    issue = app.diagnostics.record("api_error", "GET /api/tasks: KeyError('x')")
    state["script"] = [{"weebo/web/app.js": "export const version = 7;\n"}]
    proposal = await app.evolution.propose("Guard tasks API", "Handle missing keys.",
                                           meta={"addresses": [issue["id"]]})
    await wait_status(app, proposal["id"], ("ready",))
    await app.evolution.merge(proposal["id"])
    meta = app.store.get_proposal(proposal["id"])["meta"]
    meta["merged_at"] = time.time() - (outcomes.WINDOW_DAYS + 1) * 86400
    app.store.update_proposal(proposal["id"], meta=meta)
    assert outcomes.review(app)[0]["result"] == "held"
    assert "stayed fixed" in outcomes.track_record(app)


async def test_rollback_reopens_the_failures(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    issue = app.diagnostics.record("agent_crash", "Agent runner crashed: boom")
    state["script"] = [{"weebo/web/app.js": "export const version = 8;\n"}]
    proposal = await app.evolution.propose("Agent fix", "Fix it.", meta={"addresses": [issue["id"]]})
    await wait_status(app, proposal["id"], ("ready",))
    await app.evolution.merge(proposal["id"])
    await app.evolution.rollback(proposal["id"])
    assert app.diagnostics.get(issue["id"])["status"] == "open"


# ---------------------------------------------------------------- the behavior gate
async def test_behavior_changes_are_rehearsed_and_regressions_go_back_to_the_agent(evolving, monkeypatch):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    verdicts = [
        {"ok": False, "skipped": False, "summary": "Running Weebo: 3/3. This build: 2/3.\n- REGRESSED: Remembers tea"},
        {"ok": True, "skipped": False, "summary": "Running Weebo: 3/3. This build: 3/3."},
    ]
    calls = []

    async def fake_gate(worktree, count):
        calls.append((count, app.store.get_proposal(app.evolution.current)["meta"]["stage"]))
        return verdicts.pop(0)

    monkeypatch.setattr(app.evals, "gate", fake_gate)
    state["script"] = [{"weebo/brain/persona.py": "PERSONA = 'v2'\n"}, {"weebo/brain/persona.py": "PERSONA = 'v3'\n"}]
    proposal = await app.evolution.propose("Warmer persona", "Tweak tone.")
    ready = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert ready["status"] == "ready"
    assert calls == [(False, "evaluating"), (False, "evaluating")]  # user-requested work isn't counted
    assert [r["gates_ok"] for r in ready["meta"]["rounds"]] == [False, True]
    revision = [t for t in app.store.list_tasks(limit=10) if "revision" in t["title"]][0]
    assert "REGRESSED: Remembers tea" in revision["prompt"]
    assert "Behavior evals" in ready["gate_report"]


async def test_ui_changes_skip_the_behavior_gate(evolving, monkeypatch):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})

    async def never(*_args, **_kwargs):
        raise AssertionError("UI changes must not be rehearsed")

    monkeypatch.setattr(app.evals, "gate", never)
    state["script"] = [{"weebo/web/app.js": "export const version = 9;\n"}]
    proposal = await app.evolution.propose("UI only", "v9")
    assert (await wait_status(app, proposal["id"], ("ready", "failed")))["status"] == "ready"


async def test_behavior_gate_that_cannot_run_is_shown_not_hidden(evolving, monkeypatch):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})

    async def broken(worktree, count):
        raise RuntimeError("codex not signed in")

    monkeypatch.setattr(app.evals, "gate", broken)
    state["script"] = [{"weebo/memory/memory.py": "X = 1\n"}]
    proposal = await app.evolution.propose("Memory tweak", "Change recall.")
    ready = await wait_status(app, proposal["id"], ("ready", "failed"))
    assert ready["status"] == "ready" and "Couldn't run: codex not signed in" in ready["gate_report"]
    assert ready["meta"]["governance"]["autonomous"] is False  # core code: a person reviews it anyway


async def test_rehearse_cli_reports_errors_instead_of_crashing(tmp_path):
    (tmp_path / "in.json").write_text("{not json")
    out = tmp_path / "out.json"
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([sys.executable, "-m", "weebo", "--rehearse", str(tmp_path / "in.json"), "--out", str(out)],
                          cwd=root, capture_output=True, text=True, timeout=120,
                          env={"PATH": "/usr/bin:/bin", "WEEBO_DATA_DIR": str(tmp_path / "data"),
                               "PYTHONPATH": str(root)})
    assert proc.returncode == 1 and "error" in out.read_text()
