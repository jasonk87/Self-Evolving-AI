"""Regressions for timing, environment and platform bugs found by an adversarial sweep of the self-evolution
upgrade (each test is named for the failure it prevents)."""

import asyncio
import threading
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from weebo.evolution import engine as evo
from weebo.evolution import gates, outcomes
from weebo.evolution import git as gitlib
from weebo.integrations import gcalendar, http, missing_requirements
from weebo.memory import embeddings
from tests.weebo.test_evolution import evolving, git, repo, wait_status  # noqa: F401  (fixtures)
from tests.weebo.test_evolution_safety import commit, pyrepo, wait_settled  # noqa: F401  (fixtures)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- renames can't slip past the change policy
FEATURE_TESTS = "".join(f"def test_case_{i}():\n    assert sorted([{i}, 1, 0])[0] == 0\n\n\n" for i in range(20))


async def test_renaming_a_weakened_test_does_not_auto_merge(evolving, repo):
    app, state = evolving
    (repo / "tests" / "weebo").mkdir(parents=True)
    (repo / "tests" / "weebo" / "test_feature.py").write_text(FEATURE_TESTS)
    commit(repo)
    # Similar enough that git pairs the two files as a rename (it would hide the old path from a plain diff).
    weakened = FEATURE_TESTS.replace("assert sorted([3, 1, 0])[0] == 0", "assert True") \
                            .replace("assert sorted([7, 1, 0])[0] == 0", "assert True")
    assert weakened != FEATURE_TESTS
    app.settings.update({"evolution.mode": "auto_merge"})
    state["script"] = [{"tests/weebo/test_feature.py": None, "tests/weebo/test_feature_v2.py": weakened,
                        "weebo/web/app.js": "export const version = 2;\n"}]
    proposal = await app.evolution.propose("Tidy tests", "Rename the feature tests.")
    settled = await wait_settled(app, proposal["id"])
    # Precondition: with git's default rename detection this really is a rename (else the test proves nothing).
    renames = git(repo, "diff", "-M", "--name-status", f"{settled['base_commit']}...{settled['branch']}")
    assert any(line.startswith("R") and "test_feature_v2.py" in line for line in renames.splitlines()), renames
    assert settled["status"] == "ready"
    files = {f["path"]: f["tier"] for f in settled["meta"]["governance"]["files"]}
    assert files["tests/weebo/test_feature.py"] == "human_required"  # the old path is classified as a deletion
    assert settled["meta"]["changed"].count("tests/weebo/test_feature.py") == 1


async def test_moving_a_protected_file_does_not_auto_merge(evolving, repo):
    app, state = evolving
    (repo / "weebo" / "web" / "guard.js").write_text("export const guarded = true;\n")
    commit(repo)
    app.settings.update({"evolution.mode": "auto_merge", "evolution.protect_paths": ["weebo/web/guard.js"]})
    state["script"] = [{"weebo/web/guard.js": None, "weebo/web/moved.js": "export const guarded = true;\n"}]
    proposal = await app.evolution.propose("Move the guard", "Rename guard.js.")
    settled = await wait_settled(app, proposal["id"])
    assert settled["status"] == "ready" and not settled["meta"]["governance"]["autonomous"]


async def test_changed_files_lists_both_sides_of_a_rename(repo):
    (repo / "weebo" / "web" / "app.js").rename(repo / "weebo" / "web" / "main.js")
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "rename")
    changed = await gitlib.changed_files(repo, base)
    assert set(changed) == {"weebo/web/app.js", "weebo/web/main.js"}
    assert await gitlib.deleted_files(repo, base) == ["weebo/web/app.js"]


# ---------------------------------------------------------------- the user's git config can't blind the gates
@pytest.mark.parametrize("config", ["[diff]\n\tnoprefix = true\n", "[color]\n\tui = always\n",
                                    "[core]\n\tquotepath = true\n"])
async def test_personal_details_gate_ignores_git_config(pyrepo, tmp_path, monkeypatch, config):
    root, base = pyrepo
    (root / "weebo" / "status.py").write_text('READY = "Waiting for Robin\'s approval"\n')
    (root / "weebo" / "ünïcode.py").write_text('OWNER = "robin@example.com"\n')
    commit(root)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(config)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    result = await gates.personal_details(root, base, ("Robin", "robin@example.com"))
    assert not result.ok and "weebo/status.py:1" in result.output and "weebo/ünïcode.py:1" in result.output


# ---------------------------------------------------------------- the "Existing tests" gate and test-to-test imports
async def test_original_tests_that_import_other_tests_still_run(pyrepo):
    root, base = pyrepo
    (root / "tests" / "test_core.py").write_text(
        "from weebo.core import VALUE\n\n\ndef helper():\n    return VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n")
    (root / "tests" / "test_uses_core.py").write_text(
        "from tests.test_core import helper\n\n\ndef test_helper():\n    assert helper() == 1\n")
    base = commit(root, "tests importing tests")
    (root / "tests" / "test_core.py").write_text(  # a line removed from the module the other test imports
        "from weebo.core import VALUE\n\n\ndef helper():\n    return VALUE\n\n\ndef test_value():\n    pass\n")
    commit(root)
    result = await gates.existing_tests(root, base, {"PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.ok, result.output  # no false alarm from a missing import in the rebuilt originals
    assert "2 passed" in result.output  # the importer was re-run too: its expectations depend on the rewrite


async def test_affected_tests_follow_conftest_and_imports(tmp_path):
    originals = ["tests/weebo/conftest.py", "tests/weebo/test_a.py", "tests/weebo/test_b.py", "tests/other/test_c.py"]
    for path in originals:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("")
    (tmp_path / "tests/other/test_c.py").write_text("from tests.weebo.test_a import fixture  # noqa\n")
    assert gates._affected_tests(["tests/weebo/test_a.py"], originals, tmp_path) == {
        "tests/weebo/test_a.py", "tests/other/test_c.py"}
    assert gates._affected_tests(["tests/weebo/conftest.py"], originals, tmp_path) == {
        "tests/weebo/test_a.py", "tests/weebo/test_b.py"}


# ---------------------------------------------------------------- HTTP reads the whole body
async def test_http_reads_whole_bodies_not_the_first_chunk(monkeypatch):
    page = "<p>" + "x" * 400_000 + "</p>"

    async def streamed(request):
        resp = web.StreamResponse()
        await resp.prepare(request)
        for i in range(0, len(page), 16_384):
            await resp.write(page[i:i + 16_384].encode())
            await asyncio.sleep(0)
        await resp.write_eof()
        return resp

    image = bytes(range(256)) * 1200  # ~300 KB

    async def picture(request):
        return web.Response(body=image, content_type="image/png")

    app = web.Application()
    app.router.add_get("/page", streamed)
    app.router.add_get("/img", picture)
    async with TestServer(app) as server:
        assert await http.get_text(str(server.make_url("/page"))) == page
        body, kind = await http.get_bytes(str(server.make_url("/img")))
        assert body == image and kind == "image/png"
        monkeypatch.setattr(http, "MAX_BYTES", 100_000)
        with pytest.raises(http.HttpError, match="larger than"):
            await http.get_bytes(str(server.make_url("/img")))  # refused, not saved as a broken half-image
        assert len(await http.get_text(str(server.make_url("/page")))) == 100_000  # pages are just cut off


# ---------------------------------------------------------------- a fix counts once it's live
async def test_python_fix_is_judged_only_after_weebo_restarts_into_it(evolving, repo):
    app, state = evolving
    app.settings.update({"evolution.mode": "build"})
    issue = app.diagnostics.record("tool_crash", "Tool get_weather crashed: KeyError('main')")
    state["script"] = [{"weebo/core.py": "VALUE = 'fixed'\n"}]
    proposal = await app.evolution.propose("Guard weather parsing", "Handle missing keys.",
                                           meta={"addresses": [issue["id"]]})
    await wait_status(app, proposal["id"], ("ready",))
    await app.evolution.merge(proposal["id"])
    assert state["restarts"]  # Python changed: the new code needs a restart to run
    assert app.diagnostics.get(issue["id"])["status"] != "fixed"
    assert "goes live when Weebo next restarts" in outcomes.describe(app.store.get_proposal(proposal["id"]))
    app.diagnostics.record("tool_crash", "Tool get_weather crashed: KeyError('main')")  # still the old code
    assert outcomes.review(app) == []
    app.started_at = time.time() + 1  # as if this process had started after the merge
    app.evolution._go_live()
    merged = app.store.get_proposal(proposal["id"])
    assert merged["meta"]["live_at"] and "awaiting_live" not in merged["meta"]
    assert app.diagnostics.get(issue["id"])["status"] == "fixed"
    app.diagnostics.record("tool_crash", "Tool get_weather crashed: KeyError('main')")  # now it's the new code
    assert outcomes.review(app)[0]["result"] == "regressed"


# ---------------------------------------------------------------- protect_paths as people write them
@pytest.mark.parametrize("entry", ["weebo/web/", "weebo\\web\\", "/weebo/web", "./weebo/web",
                                   "\"C:\\Repo\\weebo\\web\\js\\main.js\"", "C:/Repo/weebo/web/*", "Weebo/Web/"])
async def test_protect_paths_entries_written_the_windows_way(entry):
    assert evo.is_protected("weebo/web/js/main.js", [entry], root="C:\\Repo", fold=True)


async def test_protect_paths_stay_exact_on_case_sensitive_systems():
    assert not evo.is_protected("weebo/web/js/main.js", ["Weebo/Web/"], root="/repo", fold=False)
    assert evo.is_protected("weebo/web/js/main.js", ["/repo/weebo/web"], root="/repo", fold=False)
    assert evo.is_protected("weebo/x.py", ["/repo"], root="/repo", fold=False)  # the whole repository
    assert not evo.is_protected("weebo/x.py", ["/elsewhere/weebo"], root="/repo", fold=False)


# ---------------------------------------------------------------- calendar status checks are read-only
async def test_calendar_status_check_never_copies_credentials(tmp_path, monkeypatch, data_dir):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "credentials.json").write_text("{}")
    monkeypatch.setattr(gcalendar, "_legacy_dirs", lambda: [legacy])
    monkeypatch.setattr(gcalendar, "libraries_missing", lambda: [])
    assert missing_requirements("check_calendar") == []  # the 1.x client file counts as set up...
    assert not (data_dir / "google" / "credentials.json").exists()  # ...without being copied anywhere
    assert gcalendar.credentials_path(adopt=True) == data_dir / "google" / "credentials.json"  # signing in adopts it
    assert (data_dir / "google" / "credentials.json").exists()


# ---------------------------------------------------------------- memory indexer
class BlockingEmbedder:
    name = "blocking"

    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.texts = []

    def warm(self):
        pass

    def embed(self, texts):
        self.texts.extend(texts)
        self.started.set()
        assert self.release.wait(5)
        return [embeddings.normalize([float(len(t)), 1.0]) for t in texts]


async def test_memory_edited_mid_embedding_gets_its_new_text_embedded(app):
    memory = app.memory
    tea, _ = memory.remember("User drinks matcha daily", "preference", 3)
    memory.embedder, memory.semantic = BlockingEmbedder(), "ready"
    worker = threading.Thread(target=memory.index_pending)
    worker.start()
    assert await asyncio.to_thread(memory.embedder.started.wait, 5)
    memory.update(tea["id"], text="User drives a sedan to work")  # lands while the old text is being embedded
    memory.embedder.release.set()
    await asyncio.to_thread(worker.join, 5)
    assert app.store.memory_vectors("blocking") == []  # the stale vector was refused
    assert memory.index_pending() == 1
    assert memory.embedder.texts[-1] == "User drives a sedan to work"


async def test_forgetting_mid_embedding_is_harmless(app):
    memory = app.memory
    gone, _ = memory.remember("User once had a goldfish named Bubbles", "fact", 2)
    memory.embedder, memory.semantic = BlockingEmbedder(), "ready"
    errors = []

    def index():
        try:
            memory.index_pending()
        except Exception as exc:  # the indexer thread would log and lose the rest of its batch
            errors.append(exc)

    worker = threading.Thread(target=index)
    worker.start()
    assert await asyncio.to_thread(memory.embedder.started.wait, 5)
    memory.forget(gone["id"])
    memory.embedder.release.set()
    await asyncio.to_thread(worker.join, 5)
    assert errors == []  # used to be: sqlite3.IntegrityError: FOREIGN KEY constraint failed
    assert app.store.memory_vectors("blocking") == [] and gone["id"] not in memory._vectors


async def test_stopping_memory_ends_the_indexer_thread(app):
    memory = app.memory
    memory.embedder = BlockingEmbedder()
    memory.embedder.release.set()
    memory.start_indexing(True)
    deadline = time.time() + 5
    while memory.semantic != "ready" and time.time() < deadline:
        await asyncio.sleep(0.02)
    worker = memory._worker
    assert worker.is_alive()
    await asyncio.to_thread(memory.stop)
    assert not worker.is_alive() and memory.semantic == "off"


# ---------------------------------------------------------------- corrections after tool-using replies
async def test_correction_after_a_reply_with_commentary_is_captured(app, fake_engine):
    conv = app.conversations.create("Weather")
    app.store.add_message(conv["id"], "user", "What's the weather in Paris tomorrow?")
    app.store.add_message(conv["id"], "assistant", "Let me check the forecast.", data={"phase": "commentary"})
    app.store.add_message(conv["id"], "tool", "get_weather", kind="tool")
    app.store.add_message(conv["id"], "assistant", "Paris, Texas: sunny, 75°F.", data={"phase": "final_answer"})
    await app.conversations.send(conv["id"], "No, I meant Paris, France")
    cases = app.store.list_eval_cases()
    assert [c["prompt"] for c in cases] == ["What's the weather in Paris tomorrow?"]
    assert "Paris, France" in cases[0]["rubric"]


# ---------------------------------------------------------------- skills you asked for are never archived
async def test_only_dream_learned_skills_are_archived(app):
    from weebo.brain.tools import ToolContext, registry
    from weebo import paths
    import os

    app.settings.update({"skills.prune_unused_days": 30})
    app.store.kv_set("skill_tracking_since", time.time() - 90 * 86400)
    ctx = ToolContext(app=app, thread_id="t", conversation_id="c_abc123")
    assert (await registry.call(ctx, "save_skill", {"name": "deploy-blog", "description": "Deploy the blog.",
                                                    "instructions": "1. Build\n2. Upload"})).success
    await app.skills.save("dreamt-trick", "A trick from a dream.", "Do the thing.", source="dream")
    (paths.skills_dir() / "hand-made").mkdir()
    (paths.skills_dir() / "hand-made" / "SKILL.md").write_text("---\nname: hand-made\ndescription: Mine.\n---\nStep.\n")
    old = time.time() - 60 * 86400
    meta = app.store.kv_get("skill_meta")
    for name in ("deploy-blog", "dreamt-trick"):
        meta[name]["created_at"] = old
    app.store.kv_set("skill_meta", meta)
    for name in ("deploy-blog", "dreamt-trick", "hand-made"):
        os.utime(paths.skills_dir() / name / "SKILL.md", (old, old))
    assert app.skills.prune(force=True) == ["dreamt-trick"]
    assert {"deploy-blog", "hand-made"} <= {s["name"] for s in app.skills.list()}


# ---------------------------------------------------------------- behavior-eval baselines
async def test_concurrent_baselines_rehearse_once(app, fake_engine, monkeypatch):
    for i in range(3):
        app.evals.add_case(f"Question {i} about tea", f"Must answer question {i} about tea well.")

    async def on_turn(engine, thread_id, turn_id, record):
        await asyncio.sleep(0.02)
        await engine.finish_turn(thread_id, turn_id, text="GOOD")

    fake_engine.on_turn = on_turn

    async def judge(prompt, schema=None, **kwargs):
        return {"passed": True, "reason": "ok"}

    monkeypatch.setattr(app.mind, "think", judge)
    app.evals.code_version = "v1"
    cases = app.evals.active_cases()
    first, second = await asyncio.gather(app.evals.baseline(cases), app.evals.baseline(cases))
    assert len(fake_engine.turns) == 3  # the second caller reused the first one's results
    assert {c: r["passed"] for c, r in first.items()} == {c: r["passed"] for c, r in second.items()}


async def test_errored_baseline_results_are_not_cached(app, fake_engine, monkeypatch):
    app.evals.add_case("What tea should I brew?", "Must suggest genmaicha.")

    async def judge(prompt, schema=None, **kwargs):
        raise RuntimeError("engine hiccup")

    async def on_turn(engine, thread_id, turn_id, record):
        await engine.finish_turn(thread_id, turn_id, text="Genmaicha!")

    fake_engine.on_turn = on_turn
    monkeypatch.setattr(app.mind, "think", judge)
    app.evals.code_version = "v1"
    graded = await app.evals.baseline(app.evals.active_cases())
    assert all(r.get("error") for r in graded.values())
    assert app.evals.needs_baseline()  # a hiccup isn't a verdict: the next run tries again


async def test_build_rehearsal_gets_the_live_engines_helper_folders(app, monkeypatch, tmp_path):
    seen = {}

    class Binary:
        extra_path_dirs = [str(tmp_path / "codex-helpers")]

    app.engine.binary = Binary()

    async def fake_exec(*args, env=None, **kwargs):
        seen["env"] = env
        raise OSError("stop here")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(OSError):
        await app.evals.rehearse_candidate(tmp_path, [])
    assert seen["env"]["PATH"].split(__import__("os").pathsep)[0] == str(tmp_path / "codex-helpers")
