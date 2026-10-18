"""Ideas: specific, evidence-backed suggestions that keep the user ahead of their calendar and email.

Most ideas come from deterministic generators over the day sheet and the facts store (conflicts, back-to-backs
without known travel time, time zones, threads waiting for a reply, commitments coming due, rain). The chief can add
its own with ``suggest_idea``, but only with evidence that exists in the store. Each idea cites its evidence; one
whose evidence changes goes stale and is regenerated or dropped. New ideas are capped per day, deduplicated against
everything ever shown (dismissed ones included), and ranked by what the user found useful before.

Do it runs only safe actions (a reminder, a held slot as a reminder, a draft) after re-reading the evidence, and
verifies the result. Anything that sends, accepts or reschedules with other people goes through the chief and its
approvals.
"""

from __future__ import annotations

import hashlib
import asyncio
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import log
from .store import new_id
from .timeparse import zone

if TYPE_CHECKING:
    from .app import WeeboApp

logger = log.get("ideas")

SAFE_ACTIONS = ("reminder", "hold_slot", "draft_email")
ALL_ACTIONS = SAFE_ACTIONS + ("propose_time", "reply", "accept", "decline", "reschedule")
FEEDBACK_KEY = "ideas.feedback"
STALE_EVENT_GRACE = 600


def _ago_days(ts: float) -> int:
    return max(0, int((time.time() - ts) // 86400))


class Ideas:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self._action_lock = asyncio.Lock()

    # ------------------------------------------------------------------ reading
    def list(self, statuses: tuple[str, ...] = ("new", "discussed")) -> list[dict[str, Any]]:
        marks = ", ".join("?" for _ in statuses)
        rows = self.app.store.query(f"SELECT * FROM ideas WHERE status IN ({marks}) ORDER BY created_at DESC", statuses)
        now = time.time()
        out = []
        for row in rows:
            if row.get("expires_at") and row["expires_at"] < now:
                self._set(row["id"], "expired")
                continue
            artifact = self.app.facts.repository.artifact(f"idea:{row['id']}")
            out.append({**row, "stale": bool(artifact and artifact["stale"])})
        weights = self.weights()
        return sorted(out, key=lambda r: (-(r["data"].get("confidence", 0.5) * weights.get(r["kind"], 1.0)),
                                          -r["created_at"]))

    def get(self, idea_id: str) -> dict[str, Any] | None:
        return self.app.store.query_one("SELECT * FROM ideas WHERE id=?", (idea_id,))

    # ------------------------------------------------------------------ feedback and ranking
    def weights(self) -> dict[str, float]:
        scores = self.app.store.kv_get(FEEDBACK_KEY, {}) or {}
        return {kind: max(0.25, min(2.0, 1.0 + 0.25 * float(score))) for kind, score in scores.items()}

    def _feedback(self, kind: str, delta: float) -> float:
        scores = dict(self.app.store.kv_get(FEEDBACK_KEY, {}) or {})
        scores[kind] = max(-6.0, min(6.0, float(scores.get(kind, 0)) + delta))
        self.app.store.kv_set(FEEDBACK_KEY, scores)
        return scores[kind]

    def suppressed(self, kind: str) -> bool:
        return float((self.app.store.kv_get(FEEDBACK_KEY, {}) or {}).get(kind, 0)) <= -3

    # ------------------------------------------------------------------ creating
    def _created_today(self) -> int:
        tz = zone(self.app.settings.get("user.timezone"))
        start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        row = self.app.store.query_one("SELECT COUNT(*) AS n FROM ideas WHERE created_at>=?", (start,))
        return int((row or {}).get("n") or 0)

    def add(self, kind: str, title: str, statement: str, evidence: list[dict[str, Any]], actions: list[dict[str, Any]],
            confidence: float = 0.6, expires_at: float | None = None, source: str = "generator",
            key: str | None = None) -> dict[str, Any] | None:
        """Store an idea if it's new, specific and backed by evidence that exists. None when it's a duplicate, over
        today's cap, or a kind the user keeps finding useless."""
        repo = self.app.facts.repository
        statement = (statement or "").strip()
        if len(statement) < 20:
            raise ValueError("An idea must say specifically what and why (a full sentence).")
        refs, cited = [], []
        for item in evidence or []:
            entity, fact_id = item.get("entity"), item.get("id")
            fact = repo.get(entity, fact_id) if entity in ("events", "threads", "commitments", "people") else None
            if not fact:
                raise ValueError(f"Evidence {entity}:{fact_id} isn't in the facts store; ideas must cite real facts.")
            p = fact.get("provenance") or {}
            refs.append((entity, fact_id))
            cited.append({"entity": entity, "id": fact_id,
                          "label": item.get("label") or fact.get("title") or fact.get("subject") or fact.get("text")
                          or fact.get("name") or fact_id, "source": p.get("source"), "fetched_at": p.get("fetched_at")})
        if not refs:
            raise ValueError("An idea needs evidence from the facts store.")
        clean_actions = []
        for action in actions or []:
            if action.get("kind") not in ALL_ACTIONS:
                raise ValueError(f"Unknown action {action.get('kind')!r}.")
            clean_actions.append({"kind": action["kind"], "label": str(action.get("label") or action["kind"])[:80],
                                  "params": action.get("params") or {}, "safe": action["kind"] in SAFE_ACTIONS})
        key = key or hashlib.sha1(f"{kind}|{'|'.join(sorted(f'{e}:{i}' for e, i in refs))}".encode()).hexdigest()[:20]
        if self.app.store.query_one("SELECT 1 AS seen FROM ideas WHERE key=?", (key,)):
            return None  # never twice, including ones the user dismissed
        if self.suppressed(kind) or self._created_today() >= int(self.app.settings.get("ideas.max_per_day")):
            return None
        now = time.time()
        idea = {"id": new_id("idea"), "key": key, "kind": kind, "status": "new", "created_at": now, "updated_at": now,
                "expires_at": expires_at,
                "data": {"title": title.strip()[:120], "statement": statement[:600], "evidence": cited,
                         "actions": clean_actions, "confidence": max(0.0, min(1.0, float(confidence))),
                         "source": source}}
        self.app.store._insert("ideas", idea)
        repo.save_artifact(f"idea:{idea['id']}", "idea", {"idea_id": idea["id"], "kind": kind}, refs)
        self.app.bus.publish("ideas.updated", {"id": idea["id"]})
        return self.get(idea["id"])

    # ------------------------------------------------------------------ generators (code, not the model)
    async def generate(self) -> list[dict[str, Any]]:
        if not self.app.settings.get("ideas.enabled"):
            return []
        await self.app.daysheet.build()
        self.refresh_stale()
        made: list[dict[str, Any]] = []
        for candidate in self.candidates():
            try:
                idea = self.add(**candidate)
            except ValueError as exc:
                logger.debug("Idea skipped: %s", exc)
                continue
            if idea:
                made.append(idea)
        return made

    def candidates(self) -> list[dict[str, Any]]:
        tz_name = self.app.settings.get("user.timezone")
        tz = zone(tz_name)
        out: list[dict[str, Any]] = []
        seen_conflicts: set[tuple[str, str]] = set()

        def at(ts: float) -> str:
            return datetime.fromtimestamp(ts, tz).strftime("%a %I:%M %p").replace(" 0", " ")

        for row in self.app.daysheet.rows(include_stale=False):
            if row.get("cancelled"):
                continue
            ev = {"entity": "events", "id": row["event_id"], "label": f"{row['title']} · {at(row['start'])}"}
            for clash in row.get("conflicts") or []:
                pair = tuple(sorted((row["event_id"], clash["event_id"])))
                if pair in seen_conflicts:
                    continue
                seen_conflicts.add(pair)  # type: ignore[arg-type]
                out.append({"kind": "conflict", "title": f"{row['title']} overlaps {clash['title']}",
                            "statement": f"{row['title']} ({at(row['start'])}) overlaps {clash['title']}. Pick one, "
                                         "or I can draft a note asking to move it.",
                            "evidence": [ev, {"entity": "events", "id": clash["event_id"]}],
                            "actions": [{"kind": "draft_email", "label": "Draft a reschedule note",
                                         "params": {"about": clash["event_id"]}},
                                        {"kind": "reminder", "label": "Remind me tonight",
                                         "params": {"when": "today 7pm", "text": f"Sort out {row['title']} vs {clash['title']}"}}],
                            "confidence": 0.9, "expires_at": row["start"]})
            travel = row.get("travel") or {}
            if travel.get("status") == "unknown" and travel.get("gap_minutes", 999) <= 30:
                where = f" at {row['location']}" if row.get("location") else ""
                out.append({"kind": "travel", "title": f"Getting to {row['title']}",
                            "statement": f"{row['title']}{where} starts {travel['gap_minutes']} min after "
                                         f"you leave {travel.get('from') or 'the previous event'}. "
                                         f"{travel.get('reason') or 'Travel time is unknown.'} Plan a buffer.",
                            "evidence": [ev, {"entity": "events", "id": travel["from_event"]}],
                            "actions": [{"kind": "hold_slot", "label": "Remind me to leave early",
                                         "params": {"at": row["start"] - max(15, travel["gap_minutes"] + 15) * 60,
                                                    "text": f"Head to {row['title']} ({row['location']})"}}],
                            "confidence": 0.7, "expires_at": row["start"]})
            elif travel.get("status") == "known" and travel.get("shortfall_minutes"):
                out.append({"kind": "travel", "title": f"Tight drive to {row['title']}",
                            "statement": f"The drive to {row['title']} takes {travel['minutes']} min ({travel['source']}) "
                                         f"but you only have {travel['gap_minutes']}. Leave by "
                                         f"{at(travel['leave_by'])} or ask to start later.",
                            "evidence": [ev, {"entity": "events", "id": travel["from_event"]}],
                            "actions": [{"kind": "hold_slot", "label": f"Leave by {at(travel['leave_by'])}",
                                         "params": {"at": travel["leave_by"], "text": f"Leave for {row['title']}"}}],
                            "confidence": 0.85, "expires_at": row["start"]})
            zone_info = row.get("timezone")
            if zone_info:
                out.append({"kind": "timezone", "title": f"{row['title']} is in another time zone",
                            "statement": f"{row['title']} is {at(row['start'])} your time, which is "
                                         f"{zone_info['there']} in {zone_info['event_tz']}.",
                            "evidence": [ev], "actions": [{"kind": "reminder", "label": "Remind me 30 min before",
                                                           "params": {"at": row["start"] - 1800,
                                                                      "text": f"{row['title']} in 30 minutes"}}],
                            "confidence": 0.6, "expires_at": row["start"]})
            weather = row.get("weather") or {}
            if weather.get("source") and float(weather.get("rain_chance") or 0) >= 50:
                out.append({"kind": "weather", "title": f"Rain around {row['title']}",
                            "statement": f"{weather['summary'].capitalize()} around {row['title']} at {row['location']} "
                                         f"({at(row['start'])}). Bring an umbrella or leave a little early.",
                            "evidence": [ev], "actions": [{"kind": "reminder", "label": "Remind me to bring one",
                                                           "params": {"at": row["start"] - 3600,
                                                                      "text": f"Umbrella for {row['title']}"}}],
                            "confidence": 0.6, "expires_at": row["start"]})
        repo = self.app.facts.repository
        for thread in repo.query("threads", needs_reply=True, limit=20):
            days = _ago_days(thread["last_message_at"])
            if days >= 2:
                who = ", ".join(str(p.get("name") or p.get("email") if isinstance(p, dict) else p)
                                for p in thread["participants"][:2]) or "someone"
                out.append({"kind": "reply", "title": f"Reply to “{thread['subject']}”",
                            "statement": f"“{thread['subject']}” from {who} has waited {days} days for your reply.",
                            "evidence": [{"entity": "threads", "id": thread["id"]}],
                            "actions": [{"kind": "draft_email", "label": "Draft a reply", "params": {"thread": thread["id"]}}],
                            "confidence": 0.65, "expires_at": None})
        now = time.time()
        for commitment in repo.query("commitments", status="open", end=now + 86400, limit=20):
            when = at(commitment["due"])
            out.append({"kind": "commitment", "title": "Coming due",
                        "statement": f"{'You' if commitment['owner'] == 'user' else 'Weebo'} committed to "
                                     f"{commitment['text'].rstrip('.')} by {when}.",
                        "evidence": [{"entity": "commitments", "id": commitment["id"]}],
                        "actions": [{"kind": "reminder", "label": "Remind me in 2 hours",
                                     "params": {"when": "in 2 hours", "text": commitment["text"]}}],
                        "confidence": 0.8, "expires_at": commitment["due"] + 86400})
        weights = self.weights()
        return sorted(out, key=lambda c: -(c["confidence"] * weights.get(c["kind"], 1.0)))

    def refresh_stale(self) -> int:
        """Ideas whose evidence changed: dropped, so the generators can make a fresh one if it still applies."""
        dropped = 0
        for row in self.app.store.query("SELECT i.* FROM ideas i JOIN facts_artifacts a ON a.id = 'idea:' || i.id "
                                        "WHERE a.stale=1 AND i.status IN ('new', 'discussed')"):
            self._set(row["id"], "expired", note="Its evidence changed.")
            self.app.store.execute("UPDATE ideas SET key=? WHERE id=?", (f"{row['key']}:changed:{row['id']}", row["id"]))
            dropped += 1
        return dropped

    # ------------------------------------------------------------------ the three buttons
    def _set(self, idea_id: str, status: str, note: str = "") -> dict[str, Any] | None:
        idea = self.get(idea_id)
        if not idea:
            return None
        data = {**idea["data"], **({"note": note} if note else {})}
        self.app.store._update("ideas", "id", idea_id, {"status": status, "data": data, "updated_at": time.time()})
        self.app.bus.publish("ideas.updated", {"id": idea_id})
        return self.get(idea_id)

    def not_useful(self, idea_id: str) -> dict[str, Any]:
        idea = self.get(idea_id)
        if not idea:
            raise ValueError("No such idea.")
        if idea["status"] == "dismissed":
            return idea  # a retried HTTP request is not a second negative vote
        if idea["status"] not in ("new", "discussed"):
            raise ValueError("That idea isn't open any more.")
        score = self._feedback(idea["kind"], -1)
        if score <= -2 and not self.app.store.kv_get(f"ideas.lesson.{idea['kind']}"):
            self.app.store.kv_set(f"ideas.lesson.{idea['kind']}", True)
            try:
                self.app.memory.remember(f"The user doesn't find {idea['kind']} suggestions useful "
                                         f"(e.g. \"{idea['data']['title']}\").", "preference", 3, source="ideas")
            except Exception as exc:
                logger.info("Couldn't save the ideas preference: %s", exc)
        return self._set(idea_id, "dismissed")  # type: ignore[return-value]

    async def discuss(self, idea_id: str) -> dict[str, Any]:
        """Open the main chat with the idea and its evidence, and have the chief open with options."""
        idea = self.get(idea_id)
        if not idea or idea["status"] not in ("new", "discussed"):
            raise ValueError("That idea isn't open any more.")
        if idea.get("expires_at") is not None and idea["expires_at"] <= time.time():
            self._set(idea_id, "expired")
            raise ValueError("That idea has expired.")
        artifact = self.app.facts.repository.artifact(f"idea:{idea_id}")
        if artifact and artifact["stale"]:
            self.refresh_stale()
            raise ValueError("That idea has expired because its evidence changed.")
        self._feedback(idea["kind"], 0.5)
        main = self.app.conversations.main_id()
        evidence = "; ".join(f"{e['entity']}:{e['id']} ({e['label']})" for e in idea["data"]["evidence"])
        self.app.conversations.add_context(main, (
            f"The user tapped Discuss on idea {idea['id']}: {idea['data']['statement']} Evidence: {evidence}. Look the "
            "evidence up with the facts tools first, then open with two or three concrete options and offer them as "
            "reply chips with offer_replies."))
        await self.app.conversations.send(main, f"Let's talk about this: {idea['data']['title']}")
        return self._set(idea_id, "discussed")  # type: ignore[return-value]

    async def do_it(self, idea_id: str, action_index: int = 0) -> dict[str, Any]:
        """Run one of the idea's safe actions: read its evidence again first, verify the result after."""
        async with self._action_lock:
            return await self._do_it(idea_id, action_index)

    async def _do_it(self, idea_id: str, action_index: int) -> dict[str, Any]:
        idea = self.get(idea_id)
        if not idea or idea["status"] not in ("new", "discussed"):
            raise ValueError("That idea isn't open any more.")
        if idea["data"].get("pending_draft"):
            raise ValueError("That draft is already waiting or running.")
        if idea.get("expires_at") is not None and idea["expires_at"] <= time.time():
            self._set(idea_id, "expired")
            raise ValueError("That idea has expired; nothing was done.")
        actions = idea["data"]["actions"]
        if not 0 <= action_index < len(actions):
            raise ValueError("No such action.")
        action = actions[action_index]
        if action["kind"] not in SAFE_ACTIONS:
            raise ValueError("That would act with other people, so it goes through the chief and your approval: "
                             "use Discuss.")
        refreshed = set()
        for item in idea["data"]["evidence"]:
            fact = self.app.facts.repository.get(item["entity"], item["id"])
            if not fact:
                raise ValueError("Its evidence is gone, so I didn't act on it.")
            provenance = fact.get("provenance") or {}
            source = provenance.get("source", "")
            if source in self.app.facts.sync.sources and source not in refreshed:
                result = await self.app.facts.sync.sync(source, force=True)
                if not result["ok"]:
                    raise ValueError("Its source could not be refreshed; nothing was done.")
                refreshed.add(source)
            elif not source.startswith("local:") and source not in refreshed:
                if time.time() - provenance.get("fetched_at", 0) >= provenance.get("ttl_seconds", 0):
                    raise ValueError("Its evidence is stale and the source is unavailable; nothing was done.")
        artifact = self.app.facts.repository.artifact(f"idea:{idea_id}")
        if artifact and artifact["stale"]:  # read before acting: the facts it rests on changed
            self.refresh_stale()
            raise ValueError("Something it was based on just changed, so I dropped it; a fresh idea follows if it "
                             "still applies.")
        for item in idea["data"]["evidence"]:
            if not self.app.facts.repository.get(item["entity"], item["id"]):
                raise ValueError("Its evidence is gone, so I didn't act on it.")
        params = action.get("params") or {}
        result: dict[str, Any] = {"action": action["kind"]}
        if action["kind"] in ("reminder", "hold_slot"):
            when = params.get("when")
            if params.get("at"):
                scheduled = datetime.fromtimestamp(float(params["at"]), zone(self.app.settings.get("user.timezone")))
                when = (scheduled if scheduled.tzinfo else scheduled.astimezone()).isoformat()
            reminder = self.app.scheduler.add(str(params.get("text") or idea["data"]["title"]), str(when or "in 1 hour"),
                                              conversation_id=self.app.conversations.main_id())
            stored = self.app.store.get_reminder(reminder["id"])  # verify after acting
            if not stored or stored["status"] != "pending":
                raise ValueError("The reminder didn't save; nothing was done.")
            result.update(reminder_id=stored["id"], due_at=stored["due_at"], verified=True)
        else:  # completion is acknowledged by the chief's turn, never by dispatch alone
            main = self.app.conversations.main_id()
            evidence = "; ".join(f"{e['entity']}:{e['id']}" for e in idea["data"]["evidence"])
            prompt = (f"[Event: the user tapped Do it on idea {idea_id}: {idea['data']['statement']}]\n"
                      f"Write the draft ({action['label']}). Look up the evidence first ({evidence}) with the facts "
                      "tools. Show the draft in the chat only; do not send anything or contact anyone.")
            receipt = {"kind": "idea", "id": idea_id, "token": new_id("draft")}
            self.app.store._update("ideas", "id", idea_id, {"data": {**idea["data"], "pending_draft": receipt["token"]}})
            self._set(idea_id, "discussed", note="Draft requested; waiting for completion.")
            try:
                outcome = await self.app.conversations.run_event(main, prompt, f"Drafting: {idea['data']['title']}",
                                                                 trigger="idea", delivery=receipt)
            except BaseException:
                self.finish_draft(receipt, False)
                raise
            result.update(dispatch=outcome, verified=False)
            if outcome not in ("started", "queued"):
                self.finish_draft(receipt, False)
                raise ValueError("The draft couldn't start; the idea is still open for retry.")
            result["verified"] = self.get(idea_id)["status"] == "done"
            return {"idea": self.get(idea_id), "result": result}
        self._feedback(idea["kind"], 1)
        self.app.store.journal("ideas", f"Did it: {idea['data']['title']}", action["label"], data={"result": result})
        self._set(idea_id, "done", note=f"{action['label']} done")
        return {"idea": self.get(idea_id), "result": result}

    def finish_draft(self, receipt: dict[str, Any], success: bool) -> None:
        idea = self.get(receipt["id"])
        if not idea or idea["data"].get("pending_draft") != receipt["token"]:
            return
        data = {k: v for k, v in idea["data"].items() if k != "pending_draft"}
        self.app.store._update("ideas", "id", idea["id"], {"data": data})
        if idea["status"] not in ("new", "discussed"):
            return  # a dismissed/expired idea is not resurrected by a late completion
        if success:
            self._feedback(idea["kind"], 1)
        self._set(idea["id"], "done" if success else "new",
                  note="Draft completed in chat." if success else "Draft did not complete. You can retry it.")
        self.app.store.journal("ideas", "Draft completed" if success else "Draft interrupted",
                               idea["data"]["title"], data={"idea_id": idea["id"]})

    def recover_after_restart(self) -> None:
        for idea in self.app.store.query("SELECT * FROM ideas WHERE json_extract(data,'$.pending_draft') IS NOT NULL"):
            self.finish_draft({"id": idea["id"], "token": idea["data"]["pending_draft"]}, False)
