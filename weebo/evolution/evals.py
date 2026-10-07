"""Behavior evals: does Weebo still handle real situations well after it changes itself?

Tests prove the code works; they don't show whether Weebo answers better or worse. Eval cases are moments
from real use where Weebo fell short: a reply the user corrected, or a shortfall a dream noticed (a failed or
interrupted turn, a missed request). Each case keeps the user's message, the dialogue and memories around it,
and a rubric saying what a good reply must do.

* Rehearsal: Weebo's chat persona and turn context are rebuilt from the case (memories seeded into a scratch
  database, so memory retrieval is exercised too) and the model answers with tools disabled.
* Grading: a judge checks each answer against its rubric. Grading always runs in the live Weebo with this
  module's prompt, never in the code under test, so a build can't weaken the judge that grades it.
* Baseline: the running Weebo's results, refreshed nightly and cached per code version.
* Gate: a self-evolution build that touches behavior code (brain, memory, skills) is rehearsed in its own
  worktree (``python -m weebo --rehearse``) and must not pass fewer cases than the baseline. Cases that flip
  from pass to fail are re-run once before they count, because model answers vary.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import log, paths

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("evolution.evals")

BEHAVIOR_PREFIXES = ("weebo/brain/", "weebo/memory/", "weebo/skills.py")
BASELINE_KEY = "eval_baseline"
BASELINE_MAX_AGE = 7 * 86400
CONTEXT_MEMORIES = 20

REHEARSAL_NOTE = """

## Rehearsal mode
This turn is a rehearsal used to check your behavior; nothing you say reaches the user and no tools can run.
Answer exactly as you would in the real chat. Where you would call a tool, write the call on its own line as
`[tool: name({"arg": "value"})]`, say in one line what you expect it to return, and carry on from there.
"""

JUDGE_PROMPT = """You are grading one reply from Weebo, a personal AI assistant, in a behavior check.

## What a good reply must do
{rubric}

## The situation
{situation}

## The user's message
{prompt}

## Weebo's reply
{answer}

Tools couldn't run in this check, so a tool call written as `[tool: name(args)]` counts as done when calling
that tool is the right move. Judge only against the requirement above, not style. Pass the reply only if it
clearly meets the requirement. Answer with JSON: passed (true/false) and reason (one sentence).
"""

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["passed", "reason"],
    "properties": {"passed": {"type": "boolean"}, "reason": {"type": "string"}},
}


class EvalError(RuntimeError):
    pass


def touches_behavior(changed: list[str]) -> bool:
    return any(path.replace("\\", "/").startswith(BEHAVIOR_PREFIXES) for path in changed)


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


# ---------------------------------------------------------------------------- rehearsal (runs in any Weebo version)
class RehearsalApp:
    """The slice of WeeboApp that the persona and turn context read, backed by a scratch database."""

    def __init__(self, folder: Path, user_name: str = ""):
        from ..config import Settings
        from ..events import EventBus
        from ..memory.memory import Memory
        from ..store import Store

        folder.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(folder / "settings.json")
        if user_name:
            self.settings.update({"user.name": user_name})
        self.store = Store(folder / "rehearsal.db")
        self.memory = Memory(self.store, EventBus())

    def seed(self, memories: list[dict[str, Any]]) -> None:
        for m in memories:
            text = str(m.get("text") or "").strip()
            if text:
                self.store.add_memory(text, str(m.get("kind") or "fact"), int(m.get("importance") or 3),
                                      source="rehearsal", pinned=bool(m.get("pinned")))

    def close(self) -> None:
        self.store.close()


def rehearsal_inputs(app: RehearsalApp, case: dict[str, Any]) -> tuple[str, str]:
    """(developer instructions, prompt) for one case, built by this Weebo version's persona and memory code."""
    from ..brain import persona

    data = case.get("data") or {}
    conversation = {"id": "rehearsal", "title": data.get("conversation_title") or "Chat"}
    context, _ = persona.turn_context(app, conversation, case["prompt"])
    dialogue = "\n".join(str(line) for line in (data.get("dialogue") or [])[-8:])
    prompt = f"<context>\n{context}\n</context>\n\n"
    if dialogue:
        prompt += f"Earlier in this conversation:\n{dialogue}\n\n"
    prompt += f"User: {case['prompt']}"
    return persona.developer_instructions(app, "chat") + REHEARSAL_NOTE, prompt


async def rehearse(engine: Any, cases: list[dict[str, Any]], *, user_name: str = "", model: str = "",
                   effort: str = "", timeout: float = 240.0) -> dict[str, dict[str, str]]:
    """Answer every case. Returns {case_id: {"answer": ...} or {"error": ...}}."""
    from ..codex.oneshot import OneShotError, ask

    results: dict[str, dict[str, str]] = {}
    # A timed-out Codex turn can still be running in the folder; on Windows that blocks deleting it, and a
    # cleanup error must not throw away every answer already collected.
    with tempfile.TemporaryDirectory(prefix="weebo-rehearsal-", ignore_cleanup_errors=True) as tmp:
        for index, case in enumerate(cases):
            app = RehearsalApp(Path(tmp) / f"case{index}", user_name)
            try:
                app.seed((case.get("data") or {}).get("memories") or [])
                instructions, prompt = rehearsal_inputs(app, case)
                answer = await ask(engine, prompt, developer_instructions=instructions, model=model or None,
                                   effort=effort or None, cwd=tmp, timeout=timeout, service_name="weebo-eval")
                results[case["id"]] = {"answer": answer}
            except (OneShotError, asyncio.TimeoutError, OSError, ValueError, KeyError) as exc:
                results[case["id"]] = {"error": str(exc) or type(exc).__name__}
            finally:
                app.close()
    return results


async def _rehearse_cli(spec: dict[str, Any]) -> dict[str, Any]:
    from ..codex.engine import CodexEngine
    from ..events import EventBus

    bus = EventBus()
    bus.bind_loop(asyncio.get_running_loop())
    engine = CodexEngine(bus, spec.get("binary") or "")
    await engine.start()
    try:
        if not await engine.wait_ready(120):
            return {"error": f"Codex engine didn't start: {engine.snapshot().get('error') or engine.status}"}
        results = await rehearse(engine, spec["cases"], user_name=spec.get("user_name", ""),
                                 model=spec.get("model", ""), effort=spec.get("effort", ""))
        return {"results": results}
    finally:
        await engine.stop()


def rehearse_main(in_path: str, out_path: str) -> int:
    """``python -m weebo --rehearse IN --out OUT``: answer eval cases with this checkout's code (no grading)."""
    try:
        spec = json.loads(Path(in_path).read_text(encoding="utf-8"))
        output = asyncio.run(_rehearse_cli(spec))
    except Exception as exc:  # report, don't crash: the parent decides what a failure means
        output = {"error": f"{type(exc).__name__}: {exc}"}
    Path(out_path).write_text(json.dumps(output), encoding="utf-8")
    return 0 if "results" in output else 1


# ---------------------------------------------------------------------------- the live suite
class Evals:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.code_version = ""
        self._baseline_lock: asyncio.Lock | None = None  # created on first use, inside the running loop

    async def detect_code_version(self) -> None:
        """What is running: a snapshot of the code on disk at startup (uncommitted work included)."""
        from . import git
        try:
            if await git.is_repo(paths.PROJECT_ROOT):
                self.code_version = await git.snapshot_commit(paths.PROJECT_ROOT)
        except Exception as exc:
            logger.info("Couldn't fingerprint the running code: %s", exc)

    # ------------------------------------------------------------------ cases
    def enabled(self) -> bool:
        return bool(self.app.settings.get("evals.enabled"))

    def active_cases(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or int(self.app.settings.get("evals.max_cases"))
        return self.app.store.list_eval_cases("active", limit=limit)

    def _context_memories(self, query: str) -> list[dict[str, Any]]:
        memory = self.app.memory
        rows = {m["id"]: m for m in memory.profile()}
        for m in memory.search(query, limit=CONTEXT_MEMORIES):
            rows.setdefault(m["id"], m)
        return [{"text": m["text"], "kind": m["kind"], "importance": m["importance"], "pinned": bool(m["pinned"])}
                for m in list(rows.values())[:CONTEXT_MEMORIES]]

    def add_case(self, prompt: str, rubric: str, title: str = "", source: str = "user",
                 dialogue: list[str] | None = None, conversation_title: str = "",
                 meta: dict[str, Any] | None = None) -> dict[str, Any]:
        prompt, rubric = (prompt or "").strip(), (rubric or "").strip()
        if len(prompt) < 2 or len(rubric) < 10:
            raise EvalError("An eval case needs the user's message and a rubric (what a good reply must do).")
        from ..memory.memory import looks_secret
        if looks_secret(prompt) or looks_secret(rubric):
            raise EvalError("That looks like it contains a secret; not saved.")
        key = _normalize(prompt)
        for existing in self.app.store.list_eval_cases("active", limit=500):
            if _normalize(existing["prompt"]) == key:
                case = self.app.store.update_eval_case(existing["id"], rubric=rubric[:2000])
                self.app.bus.publish("evals.updated", {"case": case})
                return case  # type: ignore[return-value]
        data = {"dialogue": [line[:600] for line in (dialogue or [])][-8:], "conversation_title": conversation_title,
                "memories": self._context_memories(prompt)}
        title = (title or prompt).strip().splitlines()[0][:80]
        case = self.app.store.add_eval_case(title, prompt[:4000], rubric[:2000], source, data)
        if meta:
            case = self.app.store.update_eval_case(case["id"], meta=meta)  # type: ignore[assignment]
        self.app.store.journal("evals", f"New behavior check: {title}", rubric[:300], {"case_id": case["id"]})
        self.app.bus.publish("evals.updated", {"case": case})
        return case

    def capture_correction(self, conv_id: str, correction: str) -> dict[str, Any] | None:
        """The user corrected Weebo's last reply: replaying the message that led to it becomes an eval case."""
        rows = self.app.store.recent_dialogue(conv_id, limit=30)
        if rows and rows[-1]["role"] == "user" and rows[-1]["content"].strip() == correction.strip():
            rows = rows[:-1]  # the correction itself was already stored
        # Weebo's reply can be several messages (commentary while it used tools, then the answer): step back over
        # all of them to the user's message they answered.
        i = len(rows) - 1
        if i < 0 or rows[i]["role"] != "assistant":
            return None
        while i >= 0 and rows[i]["role"] == "assistant":
            i -= 1
        if i < 0:
            return None
        asked, earlier = rows[i]["content"], rows[:i]
        dialogue = [f"{'User' if r['role'] == 'user' else 'Weebo'}: {r['content'][:600]}" for r in earlier
                    if (r.get("data") or {}).get("phase") != "commentary"][-6:]
        rubric = (f"When Weebo first answered this, the user corrected it: \"{correction.strip()[:600]}\". "
                  "A good reply gets it right the first time and respects that correction.")
        conv = self.app.store.get_conversation(conv_id) or {}
        try:
            return self.add_case(asked, rubric, source="correction", dialogue=dialogue,
                                 conversation_title=conv.get("title", ""), meta={"conversation_id": conv_id})
        except EvalError:
            return None

    def retire(self, case_id: str) -> dict[str, Any] | None:
        case = self.app.store.update_eval_case(case_id, status="retired")
        self.app.bus.publish("evals.updated", {"case": case})
        return case

    # ------------------------------------------------------------------ grading (always the live code)
    async def grade(self, case: dict[str, Any], answer: str, count: bool) -> dict[str, Any]:
        situation = "\n".join((case.get("data") or {}).get("dialogue") or []) or "(start of a conversation)"
        prompt = JUDGE_PROMPT.format(rubric=case["rubric"], situation=situation[-3000:], prompt=case["prompt"],
                                     answer=(answer or "(no reply)")[-8000:])
        verdict = await self.app.mind.think(prompt, JUDGE_SCHEMA, label="eval:judge", count=count, timeout=240)
        if not isinstance(verdict, dict) or not isinstance(verdict.get("passed"), bool):
            raise EvalError("The judge gave no clear verdict.")
        return {"passed": verdict["passed"], "reason": str(verdict.get("reason") or "")[:500]}

    async def grade_all(self, cases: list[dict[str, Any]], answers: dict[str, dict[str, str]],
                        count: bool) -> dict[str, dict[str, Any]]:
        graded: dict[str, dict[str, Any]] = {}
        for case in cases:
            got = answers.get(case["id"]) or {"error": "no answer"}
            if "answer" not in got:
                graded[case["id"]] = {"passed": False, "reason": f"Couldn't answer: {got.get('error')}", "error": True}
                continue
            try:
                graded[case["id"]] = {**await self.grade(case, got["answer"], count), "answer": got["answer"][:4000]}
            except Exception as exc:
                graded[case["id"]] = {"passed": False, "reason": f"Couldn't grade: {exc}", "error": True}
        return graded

    # ------------------------------------------------------------------ baseline (the running Weebo)
    def _spec(self) -> dict[str, str]:
        settings, engine = self.app.settings, self.app.engine
        return {"user_name": settings.get("user.name") or "",
                "model": engine.default_model(settings.get("codex.model")),
                "effort": settings.get("codex.chat_effort") or ""}

    def cached_baseline(self, case_ids: list[str]) -> dict[str, dict[str, Any]] | None:
        cached = self.app.store.kv_get(BASELINE_KEY) or {}
        results = cached.get("results") or {}
        fresh = time.time() - float(cached.get("at") or 0) < BASELINE_MAX_AGE
        if cached.get("code") == self.code_version and fresh and all(cid in results for cid in case_ids):
            return {cid: results[cid] for cid in case_ids}
        return None

    def needs_baseline(self) -> bool:
        cases = self.active_cases()
        return bool(cases) and self.cached_baseline([c["id"] for c in cases]) is None

    async def baseline(self, cases: list[dict[str, Any]], count: bool = True) -> dict[str, dict[str, Any]]:
        # One run at a time: the nightly run and a build's gate can both need it. Whoever comes second waits and
        # then reuses the first one's results instead of paying for every rehearsal again.
        if self._baseline_lock is None:
            self._baseline_lock = asyncio.Lock()
        async with self._baseline_lock:
            return await self._baseline(cases, count)

    async def _baseline(self, cases: list[dict[str, Any]], count: bool) -> dict[str, dict[str, Any]]:
        cached = self.cached_baseline([c["id"] for c in cases])
        if cached is not None:
            return cached
        spec = self._spec()
        answers = await rehearse(self.app.engine, cases, user_name=spec["user_name"], model=spec["model"],
                                 effort=spec["effort"])
        graded = await self.grade_all(cases, answers, count=False)
        if count:
            self.app.count_background_turn("evals")
        stored = self.app.store.kv_get(BASELINE_KEY) or {}
        results = dict(stored.get("results") or {}) if stored.get("code") == self.code_version else {}
        # A case that couldn't be answered or graded (engine hiccup) isn't a verdict: don't cache it as one.
        results.update({cid: {k: v for k, v in r.items() if k != "answer"} for cid, r in graded.items()
                        if not r.get("error")})
        self.app.store.kv_set(BASELINE_KEY, {"code": self.code_version, "at": time.time(), "results": results})
        for case in cases:
            result = graded[case["id"]]
            meta = {**(case.get("meta") or {}), "last": {"passed": result["passed"], "reason": result["reason"],
                                                        "at": time.time(), "error": bool(result.get("error"))}}
            self.app.store.update_eval_case(case["id"], meta=meta)
        self.app.bus.publish("evals.updated", {})
        return graded

    async def refresh_baseline(self) -> dict[str, Any]:
        """Nightly: grade the running Weebo on every active case (also what the UI's 'Run checks' does)."""
        cases = self.active_cases()
        if not cases:
            return {"skipped": "no eval cases yet"}
        self.app.store.kv_set("last_eval", time.time())
        graded = await self.baseline(cases)
        passed = sum(1 for r in graded.values() if r["passed"])
        failing = [c["title"] for c in cases if not graded[c["id"]]["passed"]]
        self.app.store.journal("evals", f"Behavior checks: {passed}/{len(cases)} passed",
                               ("Still failing: " + "; ".join(failing[:6])) if failing else "Everything passed.")
        return {"passed": passed, "total": len(cases)}

    # ------------------------------------------------------------------ the gate (a candidate build)
    async def rehearse_candidate(self, worktree: Path, cases: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        """Answer the cases with the build's own code, in a separate process with its own Codex engine."""
        spec = {**self._spec(), "binary": (self.app.engine.snapshot() or {}).get("binary") or "", "cases": cases}
        scratch = Path(tempfile.mkdtemp(prefix="weebo-eval-"))
        try:
            (scratch / "in.json").write_text(json.dumps(spec), encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": str(worktree), "WEEBO_DATA_DIR": str(scratch / "data"),
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
            # The live engine runs Codex with its helper folders on PATH (the Windows desktop app ships some);
            # the build's engine gets the same, so both rehearsals run under the same conditions.
            helpers = list(getattr(getattr(self.app.engine, "binary", None), "extra_path_dirs", None) or [])
            if helpers:
                env["PATH"] = os.pathsep.join([*helpers, env.get("PATH", "")])
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "weebo", "--rehearse", str(scratch / "in.json"), "--out", str(scratch / "out.json"),
                cwd=str(worktree), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            timeout = min(3600, 180 + 240 * len(cases))
            try:
                output, _ = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise EvalError(f"The candidate's rehearsal took longer than {timeout // 60} minutes.") from None
            try:
                result = json.loads((scratch / "out.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                tail = output.decode("utf-8", "replace")[-1500:]
                raise EvalError(f"The candidate's rehearsal crashed:\n{tail}") from None
            if "results" not in result:
                raise EvalError(result.get("error") or "The candidate's rehearsal failed.")
            return result["results"]
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    async def gate(self, worktree: Path, count: bool) -> dict[str, Any]:
        """Compare a build against the running Weebo. Returns {"ok", "skipped", "summary", "regressed", ...}."""
        cases = self.active_cases()
        if not cases:
            return {"ok": True, "skipped": True, "summary": "No eval cases yet; they come from corrections and dreams."}
        baseline = await self.baseline(cases, count=count)
        answers = await self.rehearse_candidate(worktree, cases)
        candidate = await self.grade_all(cases, answers, count=False)
        flipped = [c for c in cases if baseline[c["id"]]["passed"] and not candidate[c["id"]]["passed"]]
        if flipped:  # model answers vary: a regression must reproduce before it counts
            again = await self.grade_all(flipped, await self.rehearse_candidate(worktree, flipped), count=False)
            for case in flipped:
                if again[case["id"]]["passed"]:
                    candidate[case["id"]] = again[case["id"]]
        if count:
            self.app.count_background_turn("evals")
        base_passed = sum(1 for c in cases if baseline[c["id"]]["passed"])
        cand_passed = sum(1 for c in cases if candidate[c["id"]]["passed"])
        regressed = [c for c in cases if baseline[c["id"]]["passed"] and not candidate[c["id"]]["passed"]]
        improved = [c for c in cases if not baseline[c["id"]]["passed"] and candidate[c["id"]]["passed"]]
        lines = [f"Running Weebo: {base_passed}/{len(cases)} behavior checks pass. This build: {cand_passed}/{len(cases)}."]
        for case in regressed:
            lines.append(f"- REGRESSED: {case['title']}\n  Must: {case['rubric'][:400]}\n"
                         f"  Judge: {candidate[case['id']]['reason']}")
        for case in improved:
            lines.append(f"- now passes: {case['title']}")
        return {"ok": cand_passed >= base_passed, "skipped": False, "summary": "\n".join(lines),
                "regressed": [c["id"] for c in regressed], "improved": [c["id"] for c in improved],
                "baseline_passed": base_passed, "candidate_passed": cand_passed, "total": len(cases)}

    def stats(self) -> dict[str, Any]:
        cases = self.app.store.list_eval_cases("active", limit=500)
        last = [c for c in cases if (c.get("meta") or {}).get("last")]
        return {"cases": len(cases), "checked": len(last),
                "passing": sum(1 for c in last if c["meta"]["last"]["passed"]),
                "last_run": self.app.store.kv_get("last_eval")}
