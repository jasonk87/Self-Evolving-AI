"""Personal outcomes and a read-only overview of the existing work systems.

A goal is not a scheduler or an authorization. Starting one records intent only;
delegation goes through BotManager, with its normal budgets and approval gates.
"""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .store import new_id

if TYPE_CHECKING:
    from .app import WeeboApp

FEED_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    due REAL, idea_id TEXT UNIQUE, memory_id TEXT UNIQUE,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    CHECK(status IN ('active','paused','done','dropped'))
);
CREATE INDEX IF NOT EXISTS idx_goals_status_due ON goals(status, due);
"""
FACT_ENTITIES = ("events", "threads", "commitments", "people")


class Feed:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self._delegate_lock = asyncio.Lock()

    def evidence(self, entity: str, ref: str) -> dict | None:
        """Resolve real sources. Memories are remembered statements, not verified calendar facts."""
        if entity in FACT_ENTITIES:
            return self.app.facts.repository.get(entity, ref)
        if entity == "memories":
            row = self.app.store.get_memory(ref)
            if not row or row["status"] != "active":
                return None
        elif entity == "goals":
            row = self.get(ref)
            if not row or row["status"] not in ("active", "paused"):
                return None
        elif entity == "messages":
            row = self.app.store.get_message(ref)
            if not row or row["role"] != "user" or not row["content"].strip():
                return None
        else:
            return None
        return {**row, "text": row.get("text") or row.get("content") or row.get("title"),
                "provenance": {"source": f"local:{entity}", "fetched_at": row["updated_at"],
                               "ttl_seconds": 0, "origin": row.get("source", "")}}

    def get(self, ref: str) -> dict | None:
        if not isinstance(ref, str):
            raise ValueError("Goal id must be text.")
        return self.app.store.query_one("SELECT * FROM goals WHERE id=?", (ref,))

    def require(self, ref: str) -> dict:
        row = self.get(ref)
        if not row:
            raise ValueError("No such goal.")
        return row

    def goals(self, include_closed: bool = False) -> list[dict]:
        where = "" if include_closed else "WHERE status IN ('active','paused')"
        return self.app.store.query(f"SELECT * FROM goals {where} ORDER BY due IS NULL, due, updated_at DESC")

    def _changed(self, goal: dict, actor: str, title: str) -> dict:
        self.app.store.journal("goals", title, goal["title"], data={"goal_id": goal["id"], "actor": actor})
        self.app.bus.publish("goals.updated", {"id": goal["id"]})
        return goal

    def _validate(self, patch: dict) -> dict:
        if not isinstance(patch, dict) or set(patch) - {"title", "details", "next_step", "blocker", "due", "status", "owner"}:
            raise ValueError("Unknown goal field.")
        clean = dict(patch)
        for field in ("title", "details", "next_step", "blocker", "owner"):
            if field in clean:
                if not isinstance(clean[field], str):
                    raise ValueError(f"{field} must be text.")
                clean[field] = clean[field].strip()
                if len(clean[field]) > (160 if field == "title" else 3000):
                    raise ValueError(f"{field} is too long.")
        if "title" in clean and not clean["title"]:
            raise ValueError("A goal needs a title.")
        if "status" in clean and clean["status"] not in ("active", "paused", "done", "dropped"):
            raise ValueError("Unknown goal status.")
        due = clean.get("due")
        if due is not None and (isinstance(due, bool) or not isinstance(due, (int, float)) or
                                not math.isfinite(due) or not 0 <= due <= 253402214400):
            raise ValueError("Due must be a valid timestamp or null.")
        if clean.get("owner") not in (None, "", "user"):
            bot = self.app.bots.get(clean["owner"])
            if not bot or bot["status"] not in ("active", "paused"):
                raise ValueError("Choose an approved bot or yourself as owner.")
            clean["owner"] = bot["id"]
        return clean

    def create(self, title: str, *, actor: str = "user", idea_id: str | None = None,
               memory_id: str | None = None, **fields: Any) -> dict:
        clean = self._validate({"title": title, **fields})
        if idea_id and not self.app.ideas.get(idea_id):
            raise ValueError("No such idea.")
        if memory_id and not self.evidence("memories", memory_id):
            raise ValueError("That memory is no longer available.")
        with self.app.store.transaction():
            for column, ref in (("idea_id", idea_id), ("memory_id", memory_id)):
                if ref:
                    existing = self.app.store.query_one(f"SELECT * FROM goals WHERE {column}=?", (ref,))
                    if existing:
                        return existing
            now = time.time()
            row = {"id": new_id("goal"), "title": clean.pop("title"), "status": clean.pop("status", "active"),
                   "due": clean.pop("due", None), "idea_id": idea_id, "memory_id": memory_id,
                   "created_at": now, "updated_at": now, "data": {"owner": "user", "jobs": [], **clean}}
            self.app.store._insert("goals", row)
        return self._changed(row, actor, "Goal chosen")

    def update(self, ref: str, patch: dict, actor: str = "user") -> dict:
        goal = self.require(ref)
        clean = self._validate(patch)
        columns = {k: clean.pop(k) for k in ("title", "due", "status") if k in clean}
        self.app.store._update("goals", "id", ref,
                               {**columns, "data": {**goal["data"], **clean}, "updated_at": time.time()})
        return self._changed(self.require(ref), actor, "Goal updated")

    def start_idea(self, ref: str) -> dict:
        idea = self.app.ideas.get(ref)
        if not idea:
            raise ValueError("No such idea.")
        existing = self.get(idea["data"].get("goal_id", "")) or self.app.store.query_one("SELECT * FROM goals WHERE idea_id=?", (ref,))
        if existing:
            return existing  # a retried Start never resurrects a completed goal
        self.app.ideas.require_current(idea)
        if idea["data"].get("pending_draft"):
            raise ValueError("This idea already has a draft in progress.")
        memory = next((e["id"] for e in idea["data"]["evidence"] if e["entity"] == "memories"), None)
        with self.app.store.transaction():
            goal = self.create(idea["data"]["title"], idea_id=ref, memory_id=memory,
                               details=idea["data"]["statement"], next_step="Choose the first concrete step.")
            self.app.store._update("ideas", "id", ref, {"data": {**idea["data"], "goal_id": goal["id"]}})
            self.app.ideas._set(ref, "done", note="Added to goals; no work has been started.")
            self.app.ideas._feedback(idea["kind"], 1)
        return goal

    async def delegate(self, ref: str, bot_id: str, instructions: str = "") -> dict:
        async with self._delegate_lock:
            goal = self.require(ref)
            if goal["status"] != "active":
                raise ValueError("Resume the goal before delegating work.")
            if not isinstance(instructions, str) or len(instructions) > 6000:
                raise ValueError("Job instructions must be text, up to 6000 characters.")
            for job in self.public_goal(goal)["jobs"]:
                if job and job["status"] in ("open", "running"):
                    raise ValueError("This goal already has work in progress. Open its bot chat to steer it.")
            if not isinstance(bot_id, str):
                raise ValueError("Bot id must be text.")
            bot = self.app.bots.require(bot_id)
            text = instructions.strip() or goal["data"].get("next_step") or goal["title"]
            context = f"Goal {ref}: {goal['title']}"
            if goal["data"].get("details"):
                context += f"\nGoal notes and constraints: {goal['data']['details']}"
            if goal["data"].get("blocker"):
                context += f"\nCurrent blocker: {goal['data']['blocker']}"
            deadline = datetime.fromtimestamp(goal["due"], timezone.utc).isoformat() if goal["due"] is not None else ""
            job = await self.app.bots.assign_job(bot["id"], f"{context}\nRequested step: {text}",
                done_criteria="Report the result and any remaining steps; do not claim the whole goal is achieved.",
                deadline=deadline, actor="user", goal_id=ref)
            # Re-read after await: a simultaneous edit must not be lost.
            current = self.require(ref)
            data = {**current["data"], "owner": bot["id"], "jobs": [*current["data"].get("jobs", []), job["id"]]}
            self.app.store._update("goals", "id", ref, {"data": data, "updated_at": time.time()})
            self._changed(self.require(ref), "user", "Goal work delegated")
            return {"goal": self.require(ref), "job": job}

    async def discuss(self, ref: str) -> dict:
        goal = self.require(ref)
        main = self.app.conversations.main_id()
        self.app.conversations.add_context(main, f"The user opened goal {ref}. Use goals_list to read its current "
            "state and linked work. Offer next steps with offer_replies. Do not start work or change permissions "
            "just because the goal is being discussed.")
        await self.app.conversations.send(main, f"Let's talk about my goal: {goal['title']}")
        return goal

    def link(self, ref: str, entity: str, item_id: str) -> dict:
        goal = self.require(ref)
        if not self.linked_item(entity, item_id):
            raise ValueError("Choose an existing job, reminder, or commitment.")
        links = list(goal["data"].get("links", []))
        link = {"entity": entity, "id": item_id}
        if link not in links:
            links.append(link)
            self.app.store._update("goals", "id", ref, {"data": {**goal["data"], "links": links}, "updated_at": time.time()})
            self._changed(self.require(ref), "user", "Linked existing work to goal")
        return self.require(ref)

    def linked_item(self, entity: str, ref: str) -> dict | None:
        if not isinstance(entity, str) or not isinstance(ref, str):
            raise ValueError("Link type and id must be text.")
        if entity == "jobs":
            return self.app.bots.get_job(ref)
        if entity == "reminders":
            return self.app.store.get_reminder(ref)
        if entity == "commitments":
            return self.app.facts.repository.get("commitments", ref)
        return None

    def public_goal(self, goal: dict) -> dict:
        jobs = [self.app.bots.get_job(ref) for ref in goal["data"].get("jobs", [])]
        links = []
        for link in goal["data"].get("links", []):
            item = self.linked_item(link["entity"], link["id"])
            links.append({**link, "item": item})
            if link["entity"] == "jobs" and item:
                jobs.append(item)
        # Jobs also carry the link, so an interrupted response cannot orphan delegated work.
        linked = self.app.store.query("SELECT * FROM bot_jobs WHERE json_extract(data,'$.goal_id')=? "
                                      "ORDER BY created_at DESC", (goal["id"],))
        jobs = list({j["id"]: j for j in [*jobs, *linked] if j}.values())
        jobs.sort(key=lambda j: j["created_at"], reverse=True)
        bot = self.app.bots.get(goal["data"].get("owner", ""))
        owner = bot["name"] if bot else "Former bot" if goal["data"].get("owner") not in (None, "", "user") else "You"
        return {**goal, "owner_name": owner, "jobs": jobs, "links": links,
                "monitoring": False, "note": "A goal is an outcome, not an automatic monitor. Linked work is shown below."}

    def context(self, bot_id: str | None = None) -> str:
        rows = [g for g in self.goals() if not bot_id or g["data"].get("owner") == bot_id][:6]
        if not rows:
            return ""
        instruction = "ask the chief for current details" if bot_id else "use goals_list for current details"
        return f"Chosen goals ({instruction}; recording a goal never schedules work):\n" + "\n".join(
            f"- {g['id']} [{g['status']}] {g['title'][:100]} · next: {g['data'].get('next_step', '')[:120]}" for g in rows)

    def snapshot(self) -> dict:
        goals = [self.public_goal(g) for g in self.goals()]
        linked = {j["id"] for g in goals for j in g["jobs"]}
        tracking = []
        for r in self.app.store.query("SELECT * FROM reminders WHERE status IN ('pending','paused') ORDER BY due_at"):
            routine = r["action"] in ("prompt", "workflow")
            bot = self.app.bots.for_conversation(r.get("conversation_id"))
            paused = r["status"] == "paused" or bool(bot and bot["status"] != "active")
            tracking.append({"id": r["id"], "kind": "routine" if routine else "reminder", "title": r["text"],
                             "status": "paused" if paused else "scheduled", "next_at": None if paused else r["due_at"],
                             "detail": "Paused; no work will run." if paused else
                             "Scheduled work; not continuous monitoring." if routine else "Reminder only; no background work.",
                             "link": {"panel": "schedule"}})
        for j in self.app.store.query("SELECT * FROM bot_jobs WHERE status IN ('open','running') ORDER BY created_at DESC"):
            if j["id"] in linked:
                continue
            bot = self.app.bots.get(j["bot_id"])
            tracking.append({"id": j["id"], "kind": "job", "title": j["data"]["instructions"][:180],
                             "status": j["status"], "detail": j["data"].get("result_summary", "Bot job in progress."),
                             "updated_at": j["updated_at"], "link": {"conversation": (bot or {}).get("conversation_id")}})
        for task in self.app.store.list_tasks(statuses=("queued", "running")):
            tracking.append({"id": task["id"], "kind": "agent", "title": task["title"], "status": task["status"],
                             "detail": (self.app.agents.live(task["id"]) or {}).get("progress") or "One-off background job.",
                             "link": {"panel": "agents", "task": task["id"]}})
        for c in self.app.facts.repository.query("commitments", status="open", limit=100):
            tracking.append({"id": c["id"], "kind": "commitment", "title": c["text"], "status": "open",
                             "next_at": c.get("due"), "detail": "Open commitment; no automatic check is scheduled.",
                             "link": {"panel": "facts"}})
        needs = [{"id": n["id"], "title": n["data"].get("summary", "Approval needed"),
                  "link": {"card": "needs-you"}} for n in self.app.approvals.open_items()]
        for pending in self.app.interactions.pending.values():
            if pending.future is not None and pending.future.done():
                continue
            needs.append({"id": pending.id, "title": "Question waiting for you" if pending.kind == "question" else "Approval waiting for you",
                          "link": {"conversation": pending.conversation_id, "message": pending.message_id}})
        for b in self.app.bots.list():
            if b["status"] == "proposed":
                needs.append({"id": b["id"], "title": f"Review proposed bot: {b['name']}", "link": {"card": f"bot:{b['id']}"}})
        for p in self.app.store.list_proposals(statuses=("ready",)):
            needs.append({"id": p["id"], "title": f"Review upgrade: {p['title']}", "link": {"panel": "evolution", "proposal": p["id"]}})
        for c in self.app.store.query("SELECT * FROM bot_changes WHERE status='proposed' ORDER BY created_at"):
            needs.append({"id": c["id"], "title": "Review requested bot profile change", "link": {"panel": "manage_bots"}})
        return {"goals": goals, "tracking": tracking, "needs_you": needs, "ideas": self.app.ideas.list(),
                "closed_goals": [self.public_goal(g) for g in self.goals(True) if g["status"] in ("done", "dropped")]}
