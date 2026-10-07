"""The heartbeat: Weebo's proactive loop.

Every tick it decides whether to brief the user, dream (consolidate memory and
generate insights), or audit its own code for self-improvements. Everything is
gated by a budget so background work never eats the user's Codex plan: it stops
above a usage ceiling, caps background turns per day, and respects quiet hours.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .. import log, paths
from ..brain.mind import ThinkError, extract_json
from ..evolution import outcomes
from ..memory.memory import KINDS, MemoryError_
from . import heatmap

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("heartbeat")

TICK_SECONDS = 30
UPKEEP_SECONDS = 600  # outcome review and skill pruning: bookkeeping only, no model calls
EVAL_MIN_HOURS_BETWEEN = 20

DREAM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["new_memories", "updates", "delete_ids", "episode", "insights", "message_to_user", "improvements"],
    "properties": {
        "new_memories": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "kind", "importance"],
            "properties": {"text": {"type": "string"},
                           "kind": {"type": "string", "enum": [k for k in KINDS if k != "episode"]},
                           "importance": {"type": "integer"}}}},
        "updates": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "text"],
            "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}},
        "delete_ids": {"type": "array", "items": {"type": "string"}},
        "episode": {"type": "string"},
        "insights": {"type": "array", "items": {"type": "string"}},
        "message_to_user": {"type": "string"},
        "improvements": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "description", "rationale", "addresses"],
            "properties": {"title": {"type": "string"}, "description": {"type": "string"},
                           "rationale": {"type": "string"},
                           "addresses": {"type": "array", "items": {"type": "string"}}}}},
        "eval_cases": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "prompt", "rubric"],
            "properties": {"title": {"type": "string"}, "prompt": {"type": "string"}, "rubric": {"type": "string"}}}},
        "skills": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "description", "instructions"],
            "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                           "instructions": {"type": "string"}}}},
    },
}
DREAM_SCHEMA["required"] = [*DREAM_SCHEMA["required"], "eval_cases", "skills"]

AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "proposals"],
    "properties": {
        "summary": {"type": "string"},
        "proposals": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "description", "rationale", "severity", "addresses"],
            "properties": {"title": {"type": "string"}, "description": {"type": "string"},
                           "rationale": {"type": "string"},
                           "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                           "addresses": {"type": "array", "items": {"type": "string"}}}}},
    },
}


def _clock_minutes(value: str) -> int:
    hh, mm = value.split(":")
    return int(hh) * 60 + int(mm)


def in_quiet_hours(start: str, end: str, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    minutes = now.hour * 60 + now.minute
    s, e = _clock_minutes(start), _clock_minutes(end)
    if s == e:
        return False
    if s < e:
        return s <= minutes < e
    return minutes >= s or minutes < e


class Heartbeat:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.last_activity = time.time()
        self.busy: str | None = None  # dream | brief | audit | eval
        self._task: asyncio.Task | None = None
        self._last_limits_refresh = 0.0
        self._last_upkeep = 0.0

    def touch(self) -> None:
        self.last_activity = time.time()

    @property
    def idle_minutes(self) -> float:
        return (time.time() - self.last_activity) / 60

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="heartbeat")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Heartbeat tick failed")
            await asyncio.sleep(TICK_SECONDS)

    # ------------------------------------------------------------------ gating
    def quiet(self) -> bool:
        s = self.app.settings
        return in_quiet_hours(s.get("autonomy.quiet_hours_start"), s.get("autonomy.quiet_hours_end"))

    def budget(self) -> tuple[bool, str]:
        s = self.app.settings
        if not s.get("autonomy.proactive"):
            return False, "Proactive mode is off."
        if self.app.engine.status != "ready":
            return False, f"Codex engine is {self.app.engine.status}."
        ceiling = float(s.get("autonomy.usage_ceiling_percent"))
        used = self.app.engine.usage_percent()
        if used >= ceiling:
            return False, f"Plan usage is at {used:.0f}% (ceiling {ceiling:.0f}%)."
        if self.app.background_turns_today() >= int(s.get("autonomy.max_background_turns_per_day")):
            return False, "Daily background budget is used up."
        return True, ""

    def _quiet_system(self) -> bool:
        return bool(self.app.conversations.active_turns()) or bool(self.app.agents.runs)

    # ------------------------------------------------------------------ tick
    async def tick(self) -> None:
        now = time.time()
        if now - self._last_limits_refresh > 900 and self.app.engine.status == "ready":
            self._last_limits_refresh = now
            try:
                await self.app.engine.refresh_rate_limits()
            except Exception as exc:
                logger.debug("rate limit refresh failed: %s", exc)
        if now - self._last_upkeep > UPKEEP_SECONDS:
            self._last_upkeep = now
            self.upkeep()
        if self.busy:
            return
        ok, _reason = self.budget()
        if not ok:
            return
        s = self.app.settings
        store = self.app.store
        if s.get("autonomy.daily_brief") and self._brief_due():
            await self._run("brief", self.daily_brief())
            return
        if self.quiet() or self._quiet_system():
            return
        if (s.get("autonomy.dream") and self.idle_minutes >= float(s.get("autonomy.dream_idle_minutes"))
                and now - float(store.kv_get("last_dream", 0) or 0) >= float(s.get("autonomy.dream_min_hours_between")) * 3600
                and self._new_dialogue_count() >= 4):
            await self._run("dream", self.dream())
            return
        if (s.get("autonomy.self_audit") and s.get("evolution.mode") != "off"
                and self.idle_minutes >= float(s.get("autonomy.dream_idle_minutes"))
                and now - float(store.kv_get("last_audit", 0) or 0) >= float(s.get("autonomy.self_audit_min_hours_between")) * 3600):
            await self._run("audit", self.self_audit())
            return
        if (self.app.evals.enabled() and self.idle_minutes >= float(s.get("autonomy.dream_idle_minutes"))
                and now - float(store.kv_get("last_eval", 0) or 0) >= EVAL_MIN_HOURS_BETWEEN * 3600
                and self.app.evals.needs_baseline()):
            await self._run("eval", self.app.evals.refresh_baseline())

    def upkeep(self) -> None:
        """Bookkeeping that costs no model turns: settle upgrade outcomes, archive skills nobody uses."""
        for name, job in (("outcomes", lambda: outcomes.review(self.app)), ("skills", self.app.skills.prune)):
            try:
                job()
            except Exception:
                logger.exception("Upkeep (%s) failed", name)

    async def _run(self, name: str, coro: Any) -> None:
        self.busy = name
        self.app.bus.publish("weebo.activity", {"activity": name, "state": "started"})
        mood = {"dream": "dreaming", "audit": "inspecting", "eval": "inspecting", "brief": "alert"}.get(name, "thinking")
        self.app.bus.publish("weebo.mood", {"mood": mood, "background": True})
        try:
            result = await coro
            self.app.bus.publish("weebo.activity", {"activity": name, "state": "finished", "result": result})
        except ThinkError as exc:
            logger.warning("%s failed: %s", name, exc)
            self.app.store.journal(name, f"{name.title()} did not complete", str(exc))
            self.app.bus.publish("weebo.activity", {"activity": name, "state": "failed", "error": str(exc)})
        except Exception as exc:
            logger.exception("%s crashed", name)
            self.app.diagnostics.record(f"{name}_crash", f"{name} crashed: {exc!r}")
            self.app.bus.publish("weebo.activity", {"activity": name, "state": "failed", "error": str(exc)})
        finally:
            self.busy = None

    # ------------------------------------------------------------------ brief
    def _brief_due(self) -> bool:
        s = self.app.settings
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if self.app.store.kv_get("last_brief_date") == today:
            return False
        brief_at = _clock_minutes(s.get("autonomy.daily_brief_time"))
        minutes = now.hour * 60 + now.minute
        # Only within a 3 hour window after the brief time, so a late start doesn't brief at night.
        return brief_at <= minutes < brief_at + 180 and not self.quiet()

    async def daily_brief(self) -> dict[str, Any]:
        self.app.store.kv_set("last_brief_date", datetime.now().strftime("%Y-%m-%d"))
        desk = self.app.conversations.desk()
        user = self.app.settings.get("user.name") or "the user"
        prompt = (
            f"[Event: morning brief for {user}]\n"
            "Give a short, warm morning brief (under 120 words): today's reminders and routines, any background "
            "agents or self-improvements waiting for review, and one genuinely useful proactive suggestion based "
            "on what you remember about their goals and projects. Use tools only if they clearly add value "
            "(for example weather, if you know their location). End with a quick question or offer."
        )
        self.app.count_background_turn("brief")
        await self.app.conversations.run_event(desk["id"], prompt, "Morning brief", trigger="brief")
        self.app.store.journal("brief", "Posted the morning brief")
        return {"conversation_id": desk["id"]}

    # ------------------------------------------------------------------ dream
    def _new_dialogue_count(self) -> int:
        since = float(self.app.store.kv_get("last_dream", 0) or 0)
        row = self.app.store.query_one(
            "SELECT COUNT(*) AS n FROM messages WHERE created_at>? AND role='user' AND kind='text'", (since,))
        return int(row["n"]) if row else 0

    async def dream(self) -> dict[str, Any]:
        store = self.app.store
        since = float(store.kv_get("last_dream", 0) or 0)
        store.kv_set("last_dream", time.time())
        dialogue = store.dialogue_since(since, limit=400)
        if not dialogue:
            return {"skipped": "nothing new"}
        transcript_lines = []
        for row in dialogue:
            who = "USER" if row["role"] == "user" else "WEEBO"
            transcript_lines.append(f"[{row['conversation_title'][:40]}] {who}: {row['content'][:500]}")
        transcript = "\n".join(transcript_lines)[-24000:]
        memories = store.list_memories(limit=150)
        memory_lines = "\n".join(f"{m['id']} ({m['kind']}, {m['importance']}) {m['text'][:220]}" for m in memories)
        issues = self.app.diagnostics.open_issues(min_count=2)[:5]
        issue_lines = "\n".join(f"- [{i['id']}] x{i['count']} {i['kind']}{' (came back after a fix)' if i['status'] == 'regressed' else ''}: "
                                f"{i['message'][:200]}" for i in issues) or "(none)"
        trouble = store.trouble_since(since, limit=20)
        trouble_lines = "\n".join(f"[{t['conversation_title'][:40]}] {'failed' if t['kind'] == 'error' else 'interrupted'}: "
                                  f"{t['content'][:200]}" for t in trouble) or "(none)"
        agent_runs = [t for t in store.list_tasks(limit=60, statuses=("completed",))
                      if t.get("kind") == "agent" and float(t.get("finished_at") or 0) > since]
        run_lines = "\n".join(f"- {t['title']}: {str(t.get('summary') or '')[:300]}" for t in agent_runs[:15]) or "(none)"
        existing_skills = ", ".join(s["name"] for s in self.app.skills.list()) or "(none)"
        evolution_on = self.app.settings.get("evolution.mode") != "off"
        learn_skills = self.app.settings.get("skills.auto_learn")
        prompt = f"""You are dreaming: reflect on Weebo's recent conversations to consolidate long-term memory.

RECENT CONVERSATIONS:
{transcript}

TURNS THAT FAILED OR WERE INTERRUPTED:
{trouble_lines}

EXISTING MEMORIES (id, kind, importance, text):
{memory_lines or '(none yet)'}

WEEBO'S RECURRING FAILURES ([id] count kind: message):
{issue_lines}

BACKGROUND AGENT JOBS THAT SUCCEEDED:
{run_lines}

SKILLS WEEBO ALREADY HAS: {existing_skills}

Return JSON:
- new_memories: durable facts/preferences/goals/people/projects/lessons about the user that are NOT already in memory (max 10). Each one self-contained. importance 1-5. No secrets, no trivia, nothing speculative.
- updates: corrections where an existing memory is now wrong or can be merged into a clearer statement (max 8; use existing ids).
- delete_ids: ids of memories that are duplicates or clearly obsolete (max 8). Be conservative.
- episode: 2-4 sentences summarizing what happened in these conversations (what the user worked on, decisions, open threads).
- insights: up to 3 specific ways Weebo could proactively help soon, grounded in the conversations.
- message_to_user: one short, friendly proactive note for the user's desk ONLY if there is something genuinely useful to say (a follow-up, a reminder of an open thread, an idea). Otherwise "".
- improvements: {"up to 2 concrete improvements to Weebo's own code/abilities, grounded in failures or unmet requests above (title, description with how to verify, rationale citing evidence, addresses: the [id]s of the recurring failures it fixes, or []). Otherwise []." if evolution_on else "always []."}
- eval_cases: up to 2 moments where Weebo fell short (the user corrected it, a turn failed or was interrupted, a request was missed or misunderstood) worth replaying as behavior checks: title, prompt (the user's message, verbatim), rubric (what a good reply must do, concretely and checkably). Only clear shortfalls; otherwise [].
- skills: {"up to 1 reusable procedure that the background agent jobs above carried out successfully at least twice (or that clearly will recur), not already a skill: name (kebab-case), description (one line), instructions (step by step, general, no personal data). Otherwise []." if learn_skills else "always []."}
"""
        result = await self.app.mind.think(prompt, DREAM_SCHEMA, label="dream", timeout=420)
        applied = self._apply_dream(result if isinstance(result, dict) else {})
        extras = [f"{applied[k]} {label}" for k, label in (("proposals", "improvement idea(s)"),
                                                          ("eval_cases", "behavior check(s)"),
                                                          ("skills", "skill(s) learned")) if applied[k]]
        store.journal("dream", "Dreamed and consolidated memories",
                      f"+{applied['added']} memories, {applied['updated']} updated, {applied['deleted']} removed"
                      + (", " + ", ".join(extras) if extras else ""),
                      {"episode": (result or {}).get("episode", "")})
        return applied

    def _apply_dream(self, result: dict[str, Any]) -> dict[str, int]:
        store = self.app.store
        memory = self.app.memory
        applied = {"added": 0, "updated": 0, "deleted": 0, "proposals": 0, "eval_cases": 0, "skills": 0}
        for item in (result.get("new_memories") or [])[:10]:
            try:
                _, action = memory.remember(str(item.get("text", "")), str(item.get("kind", "fact")),
                                            max(1, min(5, int(item.get("importance", 3) or 3))), source="dream")
                applied["added" if action == "added" else "updated"] += 1
            except (MemoryError_, ValueError, TypeError):
                continue
        for item in (result.get("updates") or [])[:8]:
            existing = store.get_memory(str(item.get("id", "")))
            text = str(item.get("text", "")).strip()
            if existing and text and not existing["pinned"]:
                try:
                    memory.update(existing["id"], text=text)
                    applied["updated"] += 1
                except MemoryError_:
                    continue
        for memory_id in (result.get("delete_ids") or [])[:8]:
            existing = store.get_memory(str(memory_id))
            if existing and not existing["pinned"] and existing["source"] != "user":
                memory.update(existing["id"], status="archived")
                applied["deleted"] += 1
        episode = str(result.get("episode") or "").strip()
        if len(episode) > 20:
            try:
                memory.remember(f"{datetime.now():%b %d}: {episode}", "episode", 2, source="dream")
            except MemoryError_:
                pass
        for insight in (result.get("insights") or [])[:3]:
            text = str(insight).strip()
            if len(text) > 10:
                try:
                    memory.remember(text, "insight", 2, source="dream")
                except MemoryError_:
                    pass
        note = str(result.get("message_to_user") or "").strip()
        if note:
            desk = self.app.conversations.desk()
            message = store.add_message(desk["id"], "assistant", note, data={"source": "dream"})
            store.update_conversation(desk["id"], unread=1)
            self.app.bus.publish("conv.message", {"conversation_id": desk["id"], "message": message})
            if not self.quiet():
                self.app.notify("weebo", "Weebo had a thought", note[:300], {"conversation_id": desk["id"]})
        if self.app.settings.get("evolution.mode") != "off":
            for idea in (result.get("improvements") or [])[:2]:
                title, description = str(idea.get("title", "")).strip(), str(idea.get("description", "")).strip()
                if title and description:
                    addresses = [str(a) for a in idea.get("addresses") or []]
                    asyncio.create_task(self.app.evolution.propose(title, description, str(idea.get("rationale", "")),
                                                                   source="dream", meta={"addresses": addresses}))
                    applied["proposals"] += 1
        if self.app.evals.enabled():
            for case in (result.get("eval_cases") or [])[:2]:
                try:
                    self.app.evals.add_case(str(case.get("prompt", "")), str(case.get("rubric", "")),
                                            title=str(case.get("title", "")), source="dream")
                    applied["eval_cases"] += 1
                except Exception as exc:  # EvalError: too vague, or looks like a secret
                    logger.info("Skipped a dreamed behavior check: %s", exc)
        if self.app.settings.get("skills.auto_learn"):
            for skill in (result.get("skills") or [])[:1]:
                name = str(skill.get("name", "")).strip()
                if name and name not in {s["name"] for s in self.app.skills.list()}:
                    asyncio.create_task(self._learn_skill(name, str(skill.get("description", "")),
                                                          str(skill.get("instructions", ""))))
                    applied["skills"] += 1
        return applied

    async def _learn_skill(self, name: str, description: str, instructions: str) -> None:
        try:
            await self.app.skills.save(name, description, instructions, source="dream")
        except ValueError as exc:
            logger.info("Skipped a dreamed skill %s: %s", name, exc)

    # ------------------------------------------------------------------ self audit
    async def self_audit(self) -> dict[str, Any]:
        store = self.app.store
        store.kv_set("last_audit", time.time())
        issues = self.app.diagnostics.open_issues(min_count=1, since=time.time() - 14 * 86400)[:8]
        target = heatmap.pick(self.app, issues)
        focus = target["focus"]
        issue_text = "\n".join(
            f"- [{i['id']}] seen {i['count']}x ({i['kind']}){' — CAME BACK after an upgrade meant to fix it' if i['status'] == 'regressed' else ''}: "
            f"{i['message'][:300]}" for i in issues) or "(none recorded)"
        file_text = "\n".join(f"- {f}" for f in target["files"]) or "(any file in the area)"
        open_titles = [p["title"] for p in store.list_proposals(limit=40)
                       if p["status"] not in ("rejected", "discarded", "merged", "failed")]
        prompt = f"""Audit Weebo's own source code (this repository; Weebo lives in weebo/ and its UI in weebo/web/).

Focus this round on: {focus}.
Start with these files (changed since they were last audited, or not looked at in a while):
{file_text}

Weebo's recorded runtime failures ([id] count kind: message; strong evidence, prioritize these):
{issue_text}

How Weebo's recent self-improvements turned out (learn from what held and what didn't):
{outcomes.track_record(self.app, limit=12)}

Already-proposed improvements (do not duplicate): {json.dumps(open_titles)[:2000]}

Read the relevant code (do not modify anything). Find real, verifiable problems: crashes, wrong behavior, race
conditions, broken UI flows, or high-value missing abilities. Ignore style nits. For each proposal give a precise
description an engineer could implement and verify (files, functions, expected behavior, how to test it), a
rationale with evidence (file:line or the failure above), and addresses: the [id]s of the recorded failures it
fixes ([] if none; this is how Weebo later checks whether the fix held). Return at most 3 proposals, best first;
return [] if nothing is worth changing.
"""
        task = await self.app.agents.start(
            "Self-audit: " + focus.split(" (")[0].split(":")[0], prompt, cwd=str(paths.PROJECT_ROOT), kind="audit",
            meta={"sandbox_mode": "read-only-auto", "output_schema": AUDIT_SCHEMA, "tools_scope": "none"},
            effort=self.app.settings.get("codex.background_effort") or "medium",
        )
        self.app.count_background_turn("audit")
        finished = await self.app.agents.wait(task["id"], timeout=3600)
        proposals = 0
        if finished["status"] == "completed":
            try:
                parsed = extract_json(finished.get("summary") or "")
            except Exception:
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
            for item in (parsed.get("proposals") or [])[:3]:
                title, description = str(item.get("title", "")).strip(), str(item.get("description", "")).strip()
                if not title or not description:
                    continue
                await self.app.evolution.propose(title, description, str(item.get("rationale", "")),
                                                 source="self-audit", meta={"severity": item.get("severity"),
                                                                            "addresses": item.get("addresses") or []})
                proposals += 1
            for issue in issues:
                self.app.diagnostics.set_status(issue["id"], "reviewed")
            heatmap.mark_audited(self.app, target["files"])
            store.journal("audit", "Audited my own code", f"Focus: {focus}. {proposals} proposal(s). "
                          + str(parsed.get("summary", ""))[:400])
        else:
            store.journal("audit", "Self-audit did not finish", finished.get("error") or finished["status"])
        return {"task_id": task["id"], "proposals": proposals}

    # ------------------------------------------------------------------ manual triggers
    async def trigger(self, name: str) -> dict[str, Any]:
        if self.busy:
            raise ValueError(f"Weebo is already busy with: {self.busy}")
        if self.app.engine.status != "ready":
            raise ValueError(f"Codex engine is {self.app.engine.status}")
        runners = {"dream": self.dream, "brief": self.daily_brief, "audit": self.self_audit,
                   "eval": self.app.evals.refresh_baseline}
        if name not in runners:
            raise ValueError(f"Unknown activity {name}")
        if name == "dream":
            # A manual dream reflects on the last day even if a dream ran recently.
            self.app.store.kv_set("last_dream", min(float(self.app.store.kv_get("last_dream", 0) or 0),
                                                    time.time() - 86400))
        asyncio.create_task(self._run(name, runners[name]()))
        return {"started": name}

    def status(self) -> dict[str, Any]:
        ok, reason = self.budget()
        store = self.app.store
        return {
            "busy": self.busy, "idle_minutes": round(self.idle_minutes, 1), "budget_ok": ok, "budget_reason": reason,
            "quiet_hours": self.quiet(), "background_turns_today": self.app.background_turns_today(),
            "last_dream": store.kv_get("last_dream"), "last_audit": store.kv_get("last_audit"),
            "last_brief_date": store.kv_get("last_brief_date"),
            "next_brief": self._next_brief_text(),
        }

    def _next_brief_text(self) -> str:
        s = self.app.settings
        if not s.get("autonomy.daily_brief"):
            return "off"
        hh, mm = s.get("autonomy.daily_brief_time").split(":")
        now = datetime.now()
        target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if self.app.store.kv_get("last_brief_date") == now.strftime("%Y-%m-%d") or target < now:
            target += timedelta(days=1)
        return target.strftime("%a %I:%M %p")
