"""The growth loop: how Weebo's abilities expand and get pruned, always behind the same gates.

Layers: connectors are what Weebo can do, skills are procedures (``weebo/skills.py``), workflows are repeatable
multi-step jobs across bots, and the capabilities registry shows all of it. The chief proposes; the user's yes, the
approval tiers and self-evolution's gates decide; outcomes are watched; what goes unused or keeps failing is
questioned. Detection here is deterministic code, never a model guess.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from . import log
from .store import new_id

if TYPE_CHECKING:
    from .app import WeeboApp

logger = log.get("growth")

STOP = {"the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "at", "my", "me", "i", "it", "is", "be", "with",
        "please", "can", "you", "could", "would", "this", "that", "from", "by", "as", "do", "get", "make", "up"}
REQUESTS_KEY = "growth.requests"
MAX_REQUESTS = 200


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def similarity(a: str | set[str], b: str | set[str]) -> float:
    """Jaccard similarity of the meaningful words of two requests."""
    wa = a if isinstance(a, set) else words(a)
    wb = b if isinstance(b, set) else words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


class Growth:
    def __init__(self, app: "WeeboApp"):
        self.app = app

    # ------------------------------------------------------------------ capability gaps
    def log_gap(self, need: str, detail: str = "", by: str = "chief") -> dict[str, Any]:
        """Record something Weebo couldn't do. A gap that keeps coming back becomes a proposal: an existing ChatGPT
        app when one fits, or an MCP wrapper built through self-evolution."""
        need = (need or "").strip()[:200]
        if not need:
            raise ValueError("Say what capability was missing.")
        key = " ".join(sorted(words(need)))[:120] or need.lower()
        store = self.app.store
        gap = store.query_one("SELECT * FROM capability_gaps WHERE key=?", (key,))
        now = time.time()
        if gap:
            data = {**gap["data"], "details": (gap["data"].get("details", []) + [detail[:300]])[-5:] if detail
                    else gap["data"].get("details", []), "by": sorted(set(gap["data"].get("by", [])) | {by})}
            store._update("capability_gaps", "id", gap["id"], {"count": gap["count"] + 1, "updated_at": now,
                                                               "data": data})
        else:
            store._insert("capability_gaps", {"id": new_id("gap"), "key": key, "count": 1, "status": "open",
                                              "created_at": now, "updated_at": now,
                                              "data": {"need": need, "details": [detail[:300]] if detail else [],
                                                       "by": [by]}})
        gap = store.query_one("SELECT * FROM capability_gaps WHERE key=?", (key,))
        store.journal("growth", f"Capability gap: {need}", detail[:300], data={"gap_id": gap["id"], "by": by})
        if gap["status"] == "open" and gap["count"] >= int(self.app.settings.get("growth.gap_repeats")):
            gap = self.propose_for_gap(gap)
        self.app.bus.publish("capabilities.updated", {})
        return gap  # type: ignore[return-value]

    def propose_for_gap(self, gap: dict[str, Any]) -> dict[str, Any]:
        need = gap["data"]["need"]
        apps = self.app.store.kv_get("connectors.apps_cache", []) or []
        need_words = words(need)
        match = None
        for app in apps:
            if need_words & words(f"{app.get('name', '')} {app.get('description', '')}"):
                match = app
                break
        option = ({"kind": "app", "app_id": match.get("id"), "name": match.get("name"),
                   "install_url": match.get("installUrl") or match.get("install_url") or ""} if match
                  else {"kind": "mcp", "name": need})
        data = {**gap["data"], "option": option, "proposed_at": time.time()}
        self.app.store._update("capability_gaps", "id", gap["id"], {"status": "proposed", "data": data,
                                                                    "updated_at": time.time()})
        gap = self.app.store.query_one("SELECT * FROM capability_gaps WHERE id=?", (gap["id"],))
        self._card(f"gap:{gap['id']}", "gap", f"Missing: {need}", {"gap": gap}, bring_forward=True)
        self.app.store.journal("growth", f"Proposed a fix for the recurring gap \"{need}\"", data={"gap_id": gap["id"]})
        return gap  # type: ignore[return-value]

    async def resolve_gap(self, gap_id: str, action: str, actor: str) -> dict[str, Any]:
        """build (an MCP wrapper through self-evolution's gates), dismiss, or resolved (the user connected the app)."""
        store = self.app.store
        gap = store.query_one("SELECT * FROM capability_gaps WHERE id=?", (gap_id,))
        if not gap:
            raise ValueError("No such gap.")
        data = dict(gap["data"])
        if action == "build":
            need = data["need"]
            proposal = await self.app.evolution.propose(
                f"Build an MCP connector for: {need}"[:80],
                f"Weebo keeps hitting a missing capability: {need}. Build a thin MCP server for it from the template "
                "in weebo/connectors/template (FastMCP; stdio and HTTP), with a manifest that labels every tool's "
                "risk (read, act, spend, contact_people) and a read-only test call. Do not hardcode the user's "
                "identity, accounts or local paths; take them from environment variables named in the manifest. "
                "Add tests. Once merged it still goes through connector onboarding (register, test, approve).",
                "Recurring capability gap: " + "; ".join(d for d in data.get("details", []) if d)[:600],
                source="user" if actor == "user" else "self", conversation_id=self.app.conversations.main_id())
            data["proposal_id"] = proposal["id"]
            status = "proposed"
        elif action in ("dismiss", "resolved"):
            status = "dismissed" if action == "dismiss" else "resolved"
        else:
            raise ValueError("action must be build, dismiss or resolved")
        store._update("capability_gaps", "id", gap_id, {"status": status, "updated_at": time.time(),
                                                        "data": {**data, "decided_by": actor, "action": action}})
        gap = store.query_one("SELECT * FROM capability_gaps WHERE id=?", (gap_id,))
        self._card(f"gap:{gap_id}", "gap", f"Missing: {data['need']}", {"gap": gap})
        self.app.bus.publish("capabilities.updated", {})
        return gap  # type: ignore[return-value]

    # ------------------------------------------------------------------ workflows
    def note_request(self, text: str, bot: str = "", source: str = "user") -> dict[str, Any] | None:
        """Remember a multi-step request the user made. When substantially the same thing comes up often enough,
        propose a workflow for it."""
        text = (text or "").strip()
        if len(words(text)) < 3:
            return None
        store = self.app.store
        requests = list(store.kv_get(REQUESTS_KEY, []) or [])
        requests.append({"text": text[:1000], "bot": bot, "at": time.time(), "source": source})
        requests = [r for r in requests if time.time() - r["at"] < 60 * 86400][-MAX_REQUESTS:]
        store.kv_set(REQUESTS_KEY, requests)
        mine = words(text)
        similar = [r for r in requests if similarity(mine, r["text"]) >= 0.5 and not r.get("workflow")]
        if len(similar) < int(self.app.settings.get("growth.workflow_repeats")):
            return None
        for existing in self.workflows(("proposed", "active", "paused")):
            if similarity(mine, existing["data"].get("summary", "")) >= 0.5:
                return None
        workflow = self.propose_workflow(
            name=" ".join(text.split()[:6])[:60], summary=text,
            steps=[{"bot": bot, "instructions": text}], schedule=None, actor="chief",
            why=f"You asked for this {len(similar)} times; as a workflow it runs the same way each time.")
        for request in requests:
            if request in similar:
                request["workflow"] = workflow["id"]
        store.kv_set(REQUESTS_KEY, requests)
        return workflow

    def workflows(self, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        rows = self.app.store.query("SELECT * FROM workflows ORDER BY created_at")
        return [r for r in rows if not statuses or r["status"] in statuses]

    def get_workflow(self, ref: str) -> dict[str, Any] | None:
        row = self.app.store.query_one("SELECT * FROM workflows WHERE id=?", (ref,))
        if row:
            return row
        return next((w for w in self.workflows() if w["name"].lower() == (ref or "").strip().lower()), None)

    def propose_workflow(self, name: str, summary: str, steps: list[dict[str, Any]], schedule: dict | None,
                         actor: str, why: str = "") -> dict[str, Any]:
        if not name.strip() or not steps:
            raise ValueError("A workflow needs a name and at least one step.")
        clean = []
        for step in steps[:12]:
            if not isinstance(step, dict) or not str(step.get("instructions") or "").strip():
                raise ValueError("Each step needs instructions (and optionally a bot).")
            if step.get("bot") and not self.app.bots.get(step["bot"]):
                raise ValueError(f"No bot {step['bot']!r} for a workflow step.")
            clean.append({"bot": str(step.get("bot") or ""), "instructions": str(step["instructions"])[:2000],
                          "inputs_from": step.get("inputs_from"), "outputs": str(step.get("outputs") or "")[:200]})
        now = time.time()
        row = {"id": new_id("wf"), "name": name.strip()[:80], "status": "proposed", "created_at": now,
               "updated_at": now, "data": {"summary": summary[:1000], "steps": clean, "schedule": schedule,
                                           "owner": actor, "why": why[:400], "runs": 0, "skips": 0, "failures": 0,
                                           "fail_streak": 0, "last_run": None}}
        self.app.store._insert("workflows", row)
        row = self.get_workflow(row["id"])
        self.app.store.journal("growth", f"Proposed workflow {row['name']}", why, data={"workflow_id": row["id"]})
        self._card(f"workflow:{row['id']}", "workflow", row["name"], {"workflow": row}, bring_forward=True)
        return row  # type: ignore[return-value]

    def set_workflow_status(self, ref: str, status: str, actor: str) -> dict[str, Any]:
        workflow = self.get_workflow(ref)
        if not workflow:
            raise ValueError("No such workflow.")
        if status not in ("active", "paused", "retired", "rejected"):
            raise ValueError("status must be active, paused, retired or rejected")
        data = dict(workflow["data"])
        for reminder_id in data.get("routine_ids", []):
            self.app.scheduler.cancel(reminder_id)
        data["routine_ids"] = []
        if status == "active" and data.get("schedule"):
            schedule = data["schedule"]
            reminder = self.app.scheduler.add(f"Workflow: {workflow['name']}", schedule.get("when") or "tomorrow 9am",
                                              schedule.get("recurrence") or "daily", action="workflow",
                                              conversation_id=self.app.conversations.main_id(),
                                              meta={"workflow_id": workflow["id"]})
            data["routine_ids"] = [reminder["id"]]
        self.app.store._update("workflows", "id", workflow["id"], {"status": status, "data": data,
                                                                   "updated_at": time.time()})
        workflow = self.get_workflow(workflow["id"])
        self.app.store.journal("growth", f"Workflow {workflow['name']} → {status}", data={"actor": actor})
        self._card(f"workflow:{workflow['id']}", "workflow", workflow["name"], {"workflow": workflow})
        self.app.bus.publish("capabilities.updated", {})
        return workflow  # type: ignore[return-value]

    async def run_workflow(self, ref: str, actor: str = "schedule") -> dict[str, Any]:
        """Start a run: the first step goes to its bot as a job; each finished step hands its summary to the next."""
        workflow = self.get_workflow(ref)
        if not workflow or workflow["status"] != "active":
            raise ValueError("That workflow isn't active.")
        data = dict(workflow["data"])
        run = int(data.get("runs") or 0) + 1
        data.update(runs=run, last_run=time.time(), current={"run": run, "step": 0, "results": []})
        self.app.store._update("workflows", "id", workflow["id"], {"data": data, "updated_at": time.time()})
        started = await self._start_step(self.get_workflow(workflow["id"]), 0, "")
        if not started:
            self._record(workflow["id"], ok=False, skipped=True)
        return self.get_workflow(workflow["id"])  # type: ignore[return-value]

    async def _start_step(self, workflow: dict[str, Any], index: int, previous: str) -> bool:
        steps = workflow["data"]["steps"]
        if index >= len(steps):
            self._record(workflow["id"], ok=True)
            return True
        step = steps[index]
        instructions = step["instructions"] + (f"\n\nInput from the previous step:\n{previous}" if previous else "")
        tag = {"workflow_id": workflow["id"], "step": index, "run": workflow["data"].get("runs")}
        if step.get("bot"):
            try:
                job = await self.app.bots.assign_job(step["bot"], instructions, report_on="done, fail",
                                                     actor="chief", hops=1)
            except ValueError as exc:
                logger.info("Workflow %s step %d skipped: %s", workflow["name"], index, exc)
                return False
            stored = self.app.bots.get_job(job["id"])
            self.app.store._update("bot_jobs", "id", job["id"], {"data": {**stored["data"], "workflow": tag}})
            return True
        main = self.app.conversations.main_id()
        outcome = await self.app.conversations.run_event(
            main, f"[Event: workflow \"{workflow['name']}\", step {index + 1}]\n{instructions}",
            f"Workflow: {workflow['name']}", trigger="workflow")
        if index + 1 >= len(steps):
            self._record(workflow["id"], ok=outcome in ("started", "queued"))
        return outcome in ("started", "queued")

    async def on_job_finished(self, job: dict[str, Any], outcome: str, summary: str) -> None:
        tag = (job.get("data") or {}).get("workflow")
        if not tag:
            return
        workflow = self.get_workflow(tag["workflow_id"])
        if not workflow or workflow["status"] != "active" or workflow["data"].get("runs") != tag.get("run"):
            return
        if outcome != "done":
            self._record(workflow["id"], ok=False)
            return
        await self._start_step(workflow, int(tag["step"]) + 1, summary)

    def _record(self, workflow_id: str, ok: bool, skipped: bool = False) -> None:
        workflow = self.get_workflow(workflow_id)
        if not workflow:
            return
        data = dict(workflow["data"])
        if skipped:
            data["skips"] = int(data.get("skips") or 0) + 1
        if not ok:
            data["failures"] = int(data.get("failures") or 0) + (0 if skipped else 1)
            data["fail_streak"] = int(data.get("fail_streak") or 0) + 1
        else:
            data["fail_streak"] = 0
        data.pop("current", None)
        self.app.store._update("workflows", "id", workflow_id, {"data": data, "updated_at": time.time()})

    # ------------------------------------------------------------------ pruning
    def prune_daily(self) -> dict[str, list[str]] | None:
        store = self.app.store
        if time.time() - float(store.kv_get("growth.last_prune", 0) or 0) < 86400:
            return None
        store.kv_set("growth.last_prune", time.time())
        return self.prune()

    def prune(self) -> dict[str, list[str]]:
        """Question what isn't pulling its weight. Proposes and flags; never removes anything by itself."""
        out: dict[str, list[str]] = {"bots": [], "connectors": [], "workflows": []}
        now = time.time()
        idle_days = int(self.app.settings.get("growth.idle_bot_days"))
        for bot in self.app.bots.list(("active",)):
            if bot["data"].get("retire"):
                continue
            last = max([bot["created_at"], float(bot["data"].get("last_turn_at") or 0)]
                       + [j["updated_at"] for j in self.app.bots.jobs(bot["id"], limit=1)])
            if now - last > idle_days * 86400:
                self.app.bots.propose_retire(bot["id"], f"No work in {idle_days} days.", actor="chief")
                out["bots"].append(bot["id"])
        unused_days = int(self.app.settings.get("connectors.unused_days"))
        if unused_days:
            for row in self.app.connectors.list(("approved",)):
                last = float(row["data"].get("last_used") or row["data"].get("approved_at") or row["created_at"])
                if now - last > unused_days * 86400 and not row["data"].get("unused_flagged"):
                    self.app.store._update("connectors", "id", row["id"],
                                           {"data": {**row["data"], "unused_flagged": True}, "updated_at": now})
                    self.app.conversations.add_context(self.app.conversations.main_id(),
                                                       f"Connector {row['name']} hasn't been used in {unused_days} "
                                                       "days; ask whether to keep it.")
                    out["connectors"].append(row["id"])
        for workflow in self.workflows(("active",)):
            data = workflow["data"]
            if int(data.get("fail_streak") or 0) >= 3 and not data.get("questioned"):
                self.app.store._update("workflows", "id", workflow["id"],
                                       {"data": {**data, "questioned": True}, "updated_at": now})
                self._card(f"workflow:{workflow['id']}", "workflow", workflow["name"],
                           {"workflow": self.get_workflow(workflow["id"]), "question": "It keeps failing or being "
                            "skipped. Keep it, pause it or retire it?"}, bring_forward=True)
                out["workflows"].append(workflow["id"])
        if any(out.values()):
            self.app.store.journal("growth", "Pruning review", str({k: len(v) for k, v in out.items()}))
        return out

    # ------------------------------------------------------------------ the capabilities registry
    def capabilities(self) -> dict[str, Any]:
        store = self.app.store
        bots = self.app.bots
        connectors = []
        for row in self.app.connectors.list():
            users = [b["name"] for b in bots.list(("active", "paused")) if row["id"] in (b["data"].get("connectors") or [])]
            risks = sorted({t["risk"] for t in row["data"].get("tools", [])}, key=lambda r: ("read", "act", "spend",
                                                                                         "contact_people").index(r))
            connectors.append({**row, "used_by": users, "risk": risks[-1] if risks else "act", "risks": risks})
        skills = self.app.skills.list()
        return {
            "connectors": connectors,
            "apps": store.kv_get("connectors.apps_cache", []) or [],
            "skills": [{k: s[k] for k in ("name", "description", "uses", "last_used_at", "source")} for s in skills],
            "workflows": self.workflows(),
            "bots": [bots.public(b) for b in bots.list(("proposed", "active", "paused"))],
            "gaps": store.query("SELECT * FROM capability_gaps WHERE status IN ('open', 'proposed') "
                                "ORDER BY count DESC, updated_at DESC"),
            "pending": {
                "bots": [b["id"] for b in bots.list(("proposed",))],
                "changes": [c["id"] for c in store.query("SELECT id FROM bot_changes WHERE status='proposed'")],
                "connectors": [c["id"] for c in self.app.connectors.list(("tested",))],
                "workflows": [w["id"] for w in self.workflows(("proposed",))],
            },
        }

    def _card(self, message_id: str, kind: str, title: str, data: dict[str, Any], bring_forward: bool = False) -> None:
        store = self.app.store
        existing = store.get_message(message_id)
        main = self.app.conversations.main_id()
        item = data.get("workflow") or data.get("gap") or {}
        pending = item.get("status") in ("proposed",) or bool(data.get("question"))
        message = store.upsert_message(message_id, existing["conversation_id"] if existing else main, "event", kind,
                                       title, data, None, "pending" if pending else item.get("status", "done"))
        if bring_forward:
            message = store.bring_forward(message_id) or message
        self.app.bus.publish("conv.message", {"conversation_id": message["conversation_id"], "message": message})
