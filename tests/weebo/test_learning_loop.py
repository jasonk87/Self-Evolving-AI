"""Learning from use: corrections, behavior evals, semantic memory, skills upkeep and the audit heatmap."""

import asyncio
import hashlib
import os
import time

import pytest

from weebo import paths
from weebo.brain.conversation import CORRECTION_HINT, looks_like_correction
from weebo.evolution import evals as evals_mod
from weebo.memory import embeddings
from weebo.proactive import heatmap
from tests.weebo.conftest import drain

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- corrections
@pytest.mark.parametrize("text", [
    "No, I told you I like green tea", "no. it's Tuesday", "That's not what I asked", "Wrong file",
    "You forgot the attachment", "I said Friday, not Thursday", "Actually, it's spelled Jordan",
    "Not quite: the meeting is at 3", "Why did you delete that?", "please stop adding emojis",
])
async def test_corrections_are_recognized(text):
    assert looks_like_correction(text)


@pytest.mark.parametrize("text", [
    "No problem, thanks!", "no worries", "Thanks, that worked", "Actually can you also check the weather?",
    "Notes for tomorrow: buy milk", "I said hi to Sam today", "I meant to ask: what's for dinner?",
    "Now do the same for March", "Know any good tea shops?",
])
async def test_ordinary_messages_are_not_corrections(text):
    assert not looks_like_correction(text)


async def test_correction_becomes_a_behavior_check_and_a_lesson_nudge(app, fake_engine):
    conv = app.conversations.create("Tea")
    app.store.add_message(conv["id"], "user", "Plan my week")
    app.store.add_message(conv["id"], "assistant", "Sure: Monday gym, Tuesday groceries.")
    app.store.add_message(conv["id"], "user", "What tea should I order?")
    app.store.add_message(conv["id"], "assistant", "Get a nice dark roast coffee!")
    app.memory.remember("User's favorite tea is genmaicha", "preference", 4)
    await app.conversations.send(conv["id"], "No, I told you I only drink green tea")
    assert CORRECTION_HINT in fake_engine.turns[-1]["context"]
    cases = app.store.list_eval_cases()
    assert len(cases) == 1
    case = cases[0]
    assert case["prompt"] == "What tea should I order?" and case["source"] == "correction"
    assert "I only drink green tea" in case["rubric"]
    assert case["data"]["dialogue"] == ["User: Plan my week", "Weebo: Sure: Monday gym, Tuesday groceries."]
    assert any("genmaicha" in m["text"] for m in case["data"]["memories"])  # the context Weebo had


async def test_same_moment_isnt_saved_twice(app):
    first = app.evals.add_case("What tea should I order?", "Must suggest a green tea, never coffee.")
    second = app.evals.add_case("what tea should I order", "Must suggest genmaicha specifically.")
    assert first["id"] == second["id"] and second["rubric"].startswith("Must suggest genmaicha")
    with pytest.raises(evals_mod.EvalError):
        app.evals.add_case("hi", "too short")
    with pytest.raises(evals_mod.EvalError):
        app.evals.add_case("My password is hunter2, remember it", "Must refuse to store the password anywhere.")


# ---------------------------------------------------------------- rehearsal and grading
def answer_with(fake_engine, reply):
    async def on_turn(engine, thread_id, turn_id, record):
        await engine.finish_turn(thread_id, turn_id, text=reply(record["input"][0]["text"]))
    fake_engine.on_turn = on_turn


async def test_rehearsal_rebuilds_the_situation_with_this_versions_code(tmp_path):
    case = {"id": "ev_1", "prompt": "What tea should I brew tonight?", "rubric": "Suggest genmaicha.",
            "data": {"dialogue": ["User: I'm winding down"], "memories": [
                {"text": "User's favorite tea is genmaicha", "kind": "preference", "importance": 4},
                {"text": "User is allergic to cats", "kind": "fact", "importance": 2}]}}
    rehearsal = evals_mod.RehearsalApp(tmp_path / "r", "Robin")
    try:
        rehearsal.seed(case["data"]["memories"])
        instructions, prompt = evals_mod.rehearsal_inputs(rehearsal, case)
    finally:
        rehearsal.close()
    assert "Rehearsal mode" in instructions and "Robin" in instructions
    assert "genmaicha" in prompt and "I'm winding down" in prompt and prompt.endswith("tonight?")


async def test_baseline_is_graded_by_the_live_judge_and_cached(app, fake_engine, monkeypatch):
    app.memory.remember("User's favorite tea is genmaicha", "preference", 4)
    case = app.evals.add_case("What tea should I brew?", "Must suggest genmaicha.")  # snapshots that memory
    app.memory.forget(app.memory.search("genmaicha")[0]["id"])  # the rehearsal uses the snapshot, not live memory
    answer_with(fake_engine, lambda prompt: "Brew genmaicha!" if "genmaicha" in prompt else "Coffee?")
    judged = []

    async def judge(prompt, schema=None, **kwargs):
        judged.append(prompt)
        return {"passed": "Brew genmaicha!" in prompt, "reason": "checked"}

    monkeypatch.setattr(app.mind, "think", judge)
    app.evals.code_version = "v1"
    assert app.evals.needs_baseline()
    result = await app.evals.refresh_baseline()
    assert result == {"passed": 1, "total": 1}
    assert app.store.get_eval_case(case["id"])["meta"]["last"]["passed"] is True
    assert not app.evals.needs_baseline()
    turns = len(fake_engine.turns)
    await app.evals.baseline(app.evals.active_cases())
    assert len(fake_engine.turns) == turns  # cached for this code version
    app.evals.code_version = "v2"
    assert app.evals.needs_baseline()  # new code: run again


async def test_edited_rubric_rechecks_only_its_baseline_and_clears_old_status(app, monkeypatch):
    tea = app.evals.add_case("What tea should I order?", "Must recommend black coffee.")
    other = app.evals.add_case("What should I read tonight?", "Must suggest a book.")
    runs = []

    async def rehearsal(engine, cases, **kwargs):
        runs.append([c["id"] for c in cases])
        return {c["id"]: {"answer": "Genmaicha and a book"} for c in cases}

    async def judge(prompt, schema=None, **kwargs):
        return {"passed": "black coffee" not in prompt, "reason": "mocked rubric check"}

    monkeypatch.setattr(evals_mod, "rehearse", rehearsal)
    monkeypatch.setattr(app.mind, "think", judge)
    initial = await app.evals.baseline([tea, other], count=False)
    assert not initial[tea["id"]]["passed"] and initial[other["id"]]["passed"]
    edited = app.evals.add_case("what tea should I order", "Must recommend green tea.")
    assert edited["id"] == tea["id"]
    assert "last" not in edited["meta"]
    assert app.evals.needs_baseline()
    assert app.evals.cached_baseline([tea["id"]]) is None
    assert app.evals.cached_baseline([other["id"]])[other["id"]]["passed"]
    subsequent = await app.evals.baseline([edited, other], count=False)
    assert subsequent[tea["id"]]["passed"]
    assert runs == [[tea["id"], other["id"]], [tea["id"]]]
    assert not app.evals.needs_baseline()
    unchanged = app.evals.add_case(edited["prompt"], edited["rubric"])
    assert unchanged["meta"]["last"]["passed"]
    await app.evals.baseline([unchanged, other], count=False)
    assert len(runs) == 2  # the same rubric does not invalidate a verdict


async def test_rubric_edit_during_baseline_cannot_restore_an_old_verdict(app, monkeypatch):
    case = app.evals.add_case("What tea should I order?", "Must recommend black coffee.")

    async def rehearsal(engine, cases, **kwargs):
        app.evals.add_case(case["prompt"], "Must recommend green tea.")
        return {c["id"]: {"answer": "Genmaicha"} for c in cases}

    async def judge(prompt, schema=None, **kwargs):
        return {"passed": False, "reason": "old rubric"}

    monkeypatch.setattr(evals_mod, "rehearse", rehearsal)
    monkeypatch.setattr(app.mind, "think", judge)
    await app.evals.baseline([case], count=False)
    assert app.evals.needs_baseline()
    assert app.evals.cached_baseline([case["id"]]) is None
    assert "last" not in app.store.get_eval_case(case["id"])["meta"]


@pytest.mark.parametrize("stage", ["baseline", "candidate", "confirmation"])
@pytest.mark.parametrize("failure", ["answer", "judge"])
@pytest.mark.parametrize("mixed", [False, True])
async def test_gate_reports_incomplete_evaluations_instead_of_success(app, monkeypatch, stage, failure, mixed):
    cases = [app.evals.add_case(f"Question number {i} about tea", f"Must answer question {i} well.")
             for i in range(2 if mixed else 1)]
    broken = cases[0]["id"]
    candidate_runs = []
    counted = []

    def answers(cases_to_run, this_stage):
        result = {c["id"]: {"answer": "GOOD"} for c in cases_to_run}
        if this_stage == stage:
            result[broken] = {"error": "mocked engine unavailable"} if failure == "answer" else {"answer": "JUDGE_ERROR"}
        if stage == "confirmation" and this_stage == "candidate":
            result[broken] = {"answer": "BAD"}
        return result

    async def rehearsal(engine, cases_to_run, **kwargs):
        return answers(cases_to_run, "baseline")

    async def candidate(worktree, cases_to_run):
        candidate_runs.append([c["id"] for c in cases_to_run])
        return answers(cases_to_run, "candidate" if len(candidate_runs) == 1 else "confirmation")

    async def judge(prompt, schema=None, **kwargs):
        reply = prompt.split("## Weebo's reply")[1]
        if "JUDGE_ERROR" in reply:
            raise RuntimeError("mocked judge unavailable")
        return {"passed": "GOOD" in reply, "reason": "graded"}

    monkeypatch.setattr(evals_mod, "rehearse", rehearsal)
    monkeypatch.setattr(app.evals, "rehearse_candidate", candidate)
    monkeypatch.setattr(app.mind, "think", judge)
    monkeypatch.setattr(app, "count_background_turn", counted.append)
    with pytest.raises(evals_mod.EvalError, match="mocked .* unavailable") as error:
        await app.evals.gate(paths.PROJECT_ROOT, count=True)
    assert stage in str(error.value).lower()
    assert counted == ["evals"] * (1 if stage == "baseline" else 2)
    if stage == "baseline":
        assert candidate_runs == []  # no comparison is possible without a complete baseline
    elif stage == "confirmation":
        assert candidate_runs[1] == [broken]


async def test_gate_confirms_regressions_before_counting_them(app, fake_engine, monkeypatch):
    cases = [app.evals.add_case(f"Question number {i} about tea", f"Must answer question {i} well.") for i in range(3)]
    answer_with(fake_engine, lambda prompt: "GOOD")

    async def judge(prompt, schema=None, **kwargs):
        return {"passed": "GOOD" in prompt.split("## Weebo's reply")[1], "reason": "graded"}

    monkeypatch.setattr(app.mind, "think", judge)
    candidate_runs = []

    async def fake_candidate(worktree, cases_to_run):
        candidate_runs.append([c["id"] for c in cases_to_run])
        flaky = len(candidate_runs) == 1
        return {c["id"]: {"answer": "BAD" if (c["id"] == cases[0]["id"] and flaky) else "GOOD"} for c in cases_to_run}

    monkeypatch.setattr(app.evals, "rehearse_candidate", fake_candidate)
    verdict = await app.evals.gate(paths.PROJECT_ROOT, count=False)
    assert verdict["ok"] and verdict["candidate_passed"] == 3  # the flip didn't reproduce on the re-run
    assert candidate_runs[1] == [cases[0]["id"]]

    async def always_bad(worktree, cases_to_run):
        return {c["id"]: {"answer": "BAD" if c["id"] == cases[1]["id"] else "GOOD"} for c in cases_to_run}

    monkeypatch.setattr(app.evals, "rehearse_candidate", always_bad)
    verdict = await app.evals.gate(paths.PROJECT_ROOT, count=False)
    assert not verdict["ok"] and verdict["regressed"] == [cases[1]["id"]]
    assert "REGRESSED" in verdict["summary"] and "Must answer question 1 well." in verdict["summary"]


async def test_gate_without_cases_is_skipped(app):
    verdict = await app.evals.gate(paths.PROJECT_ROOT, count=False)
    assert verdict["ok"] and verdict["skipped"]


async def test_dream_adds_checks_skills_and_cites_failures(app):
    app.settings.update({"evolution.mode": "propose"})
    issue = app.diagnostics.record("turn_failed", "Reply cut off mid-sentence")
    result = {
        "new_memories": [], "updates": [], "delete_ids": [], "episode": "", "insights": [], "message_to_user": "",
        "improvements": [{"title": "Finish replies", "description": "Retry the stream.", "rationale": "x3",
                          "addresses": [issue["id"]]}],
        "eval_cases": [{"title": "Weather follow-up", "prompt": "And tomorrow?",
                        "rubric": "Must give tomorrow's forecast for the same city, not ask which city."}],
        "skills": [{"name": "weekly-report", "description": "Compile the Friday status report.",
                    "instructions": "1. Collect merged PRs.\n2. Summarize."}],
    }
    applied = app.heartbeat._apply_dream(result)
    await drain(30)
    assert applied["eval_cases"] == 1 and applied["skills"] == 1 and applied["proposals"] == 1
    assert app.store.list_eval_cases()[0]["source"] == "dream"
    assert "weekly-report" in {s["name"] for s in app.skills.list()}
    assert app.store.list_proposals()[0]["meta"]["addresses"] == [issue["id"]]


# ---------------------------------------------------------------- semantic memory
CONCEPTS = {"kid": "child", "kids": "child", "daughter": "child", "son": "child", "children": "child",
            "tea": "tea", "genmaicha": "tea", "matcha": "tea", "car": "car", "sedan": "car"}


class FakeEmbedder:
    """Maps words to concepts, so paraphrases land close together, like a real embedding model."""
    name = "fake-concepts"

    def warm(self):
        pass

    def embed(self, texts):
        vectors = []
        for text in texts:
            vec = [0.0] * 32
            for word in text.lower().replace("'", " ").split():
                concept = CONCEPTS.get(word.strip(".,?!"))
                if concept:
                    vec[int(hashlib.md5(concept.encode()).hexdigest(), 16) % 32] += 1.0
            vec[31] += 0.05  # avoid zero vectors
            vectors.append(embeddings.normalize(vec))
        return vectors


async def test_semantic_recall_finds_paraphrases(app):
    memory = app.memory
    daughter, _ = memory.remember("User's daughter Mia turns seven in May", "person", 3)  # not a core memory
    memory.remember("User drives a blue sedan", "fact", 3)
    assert memory.search("how is my kid doing at school") == []  # keyword search alone misses it
    memory.embedder = FakeEmbedder()
    memory.semantic = "ready"
    assert memory.index_pending() == 2
    hits = memory.search("how is my kid doing at school")
    assert [h["id"] for h in hits] == [daughter["id"]] and hits[0]["similarity"] > 0.9
    text, used = memory.context_block("any ideas for my kid's birthday?")
    assert "Mia" in text and daughter["id"] in used
    assert memory.stats()["recall"] == "semantic + keyword"


async def test_edited_memories_are_re_embedded(app):
    memory = app.memory
    memory.embedder, memory.semantic = FakeEmbedder(), "ready"
    tea, _ = memory.remember("User drinks matcha daily", "preference", 3)
    memory.index_pending()
    memory.update(tea["id"], text="User drives a sedan to work")
    assert memory.search("tea recommendations") == []  # the old meaning is gone with the old text
    assert memory.index_pending() == 1
    assert [h["id"] for h in memory.search("which car do I have")] == [tea["id"]]


async def test_background_indexer_loads_and_reports(app):
    memory = app.memory
    memory.remember("User's son plays chess", "person", 3)
    memory.embedder = FakeEmbedder()
    memory.start_indexing(True)
    deadline = time.time() + 5
    while memory.semantic != "ready" and time.time() < deadline:
        await asyncio.sleep(0.02)
    assert memory.semantic == "ready" and memory.stats()["embedded"] == 1
    memory.start_indexing(False)
    assert memory.stats()["recall"] == "keyword"


async def test_without_fastembed_recall_stays_keyword(app, monkeypatch):
    monkeypatch.setattr(embeddings, "available", lambda: False)
    app.memory.start_indexing(True)
    assert app.memory.semantic == "unavailable" and "fastembed" in app.memory.stats()["recall"]


async def test_vectors_pack_round_trip():
    vector = embeddings.normalize([3.0, 4.0])
    assert embeddings.unpack(embeddings.pack(vector)) == pytest.approx([0.6, 0.8])


# ---------------------------------------------------------------- skills upkeep
async def test_skill_reads_count_as_uses(app):
    await app.skills.save("deploy-blog", "Deploy the blog.", "1. Build\n2. Upload", source="conv_1")
    path = paths.skills_dir() / "deploy-blog" / "SKILL.md"
    conv = app.conversations.create("x")
    item = {"type": "commandExecution", "id": "c1", "command": f"cat {path}", "status": "completed",
            "commandActions": [{"type": "read", "path": str(path)}]}
    app.conversations._on_item(conv["id"], app.conversations.session(conv["id"]), item, completed=True)
    assert app.skills.note_item({"type": "commandExecution", "command": "cat skills/unknown-skill/SKILL.md"}) == []
    skill = next(s for s in app.skills.list() if s["name"] == "deploy-blog")
    assert skill["uses"] == 1 and skill["last_used_at"]


async def test_unused_auto_learned_skills_are_archived(app):
    app.settings.update({"skills.prune_unused_days": 30})
    assert app.skills.prune(force=True) == []  # first run only starts the clock
    app.store.kv_set("skill_tracking_since", time.time() - 90 * 86400)
    await app.skills.save("old-trick", "An old trick.", "Do the thing.", source="dream")
    await app.skills.save("kept-trick", "Still useful.", "Do the other thing.", source="dream")
    old = time.time() - 60 * 86400
    meta = app.store.kv_get("skill_meta")
    meta["old-trick"]["created_at"] = old
    meta["kept-trick"].update({"created_at": old, "last_used_at": time.time() - 86400})
    app.store.kv_set("skill_meta", meta)
    for name in ("old-trick", "kept-trick", "weebo-self-check"):
        os.utime(paths.skills_dir() / name / "SKILL.md", (old, old))
    assert app.skills.prune(force=True) == ["old-trick"]
    names = {s["name"] for s in app.skills.list()}
    assert "old-trick" not in names and {"kept-trick", "weebo-self-check"} <= names  # seeded skills stay
    assert any(p.name.startswith("old-trick-") for p in (paths.data_dir() / "skills_archive").iterdir())
    app.settings.update({"skills.prune_unused_days": 0})
    assert app.skills.prune(force=True) == []


# ---------------------------------------------------------------- audit heatmap
async def test_audit_follows_changes_and_failures(app, tmp_path):
    root = tmp_path / "src"
    for rel in ("weebo/brain/conversation.py", "weebo/brain/persona.py", "weebo/server/app.py",
                "weebo/web/vendor/lib.js", "weebo/web/js/chat.js"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x = 1\n")
    assert "weebo/web/vendor/lib.js" not in heatmap.source_files(root)
    heatmap.mark_audited(app, list(heatmap.source_files(root)))
    later = time.time() + 60
    os.utime(root / "weebo/brain/persona.py", (later, later))  # edited after the last audit
    target = heatmap.pick(app, [], root)
    assert target["area"] == "brain" and target["files"][0] == "weebo/brain/persona.py"
    target = heatmap.pick(app, [{"kind": "api_error", "count": 4}], root)
    assert target["area"] == "server"  # recorded failures outweigh one edited file
