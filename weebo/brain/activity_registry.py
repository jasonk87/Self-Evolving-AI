"""Everything Weebo is doing right now, in one honest, prioritized list.

Entries come from real events only: chat and bot turns (and the items inside them), approvals waiting on the user,
background agents, self-upgrades, dreaming and audits. The highest-priority entry is "now showing":

1. the reply being written for the user right now,
2. anything waiting on the user (an approval or question that blocks work),
3. work the user asked for (agents, bot jobs, reports on them),
4. Weebo's and the bots' own background work.

When an entry hasn't had an event for a while it says so ("Still running the tests · 3 min") instead of pretending
to be busy. With nothing to show the state is idle, or sleeping during quiet hours.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from . import activity

if TYPE_CHECKING:
    from ..app import WeeboApp

REPLY, WAITING, REQUESTED, BACKGROUND = 1, 2, 3, 4
KINDS = ("thinking", "coding", "reviewing", "researching", "delegating", "writing", "calling", "syncing",
         "sleeping", "dreaming", "waiting")
# How long each kind may go quiet before the display says so (multiples of activity.stale_seconds).
QUIET_FACTOR = {"thinking": 1, "writing": 1, "coding": 2, "reviewing": 3, "researching": 2, "delegating": 4,
                "calling": 2, "syncing": 1, "dreaming": 10, "sleeping": 10, "waiting": 0}
USER_TRIGGERS = {"user"}
REQUESTED_TRIGGERS = {"job", "chief", "agent_report", "bot_report", "followup", "idea"}
PUBLISH_EVERY = 5.0  # seconds: how often pure "still alive" refreshes go out


@dataclass
class Entry:
    id: str
    owner: str  # chief | <bot id> | <task id> | system
    owner_name: str
    priority: int
    kind: str
    words: str
    started_at: float = field(default_factory=time.time)
    last_event_at: float = field(default_factory=time.time)
    waiting_on_user: bool = False
    link: dict[str, Any] = field(default_factory=dict)


def elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


class ActivityRegistry:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.entries: dict[str, Entry] = {}
        self._last_publish = 0.0
        self._unsubscribe: list[Any] = []

    def start(self) -> None:
        bus = self.app.bus
        for topic, handler in (
            ("conv.turn.started", self._turn_started), ("conv.turn.completed", self._turn_completed),
            ("conv.message", self._message), ("conv.delta", self._alive), ("conv.reasoning", self._alive),
            ("conv.output", self._alive), ("interaction.pending", self._asked), ("interaction.resolved", self._answered),
            ("task.updated", self._task), ("task.progress", self._task_progress), ("weebo.activity", self._heartbeat),
            ("evolution.updated", self._evolution),
        ):
            self._unsubscribe.append(bus.subscribe(topic, handler))

    # ------------------------------------------------------------------ bookkeeping
    def put(self, entry: Entry) -> None:
        old = self.entries.get(entry.id)
        if old:
            entry.started_at = old.started_at
        self.entries[entry.id] = entry
        self._publish(force=old is None or (old.words, old.kind, old.priority) != (entry.words, entry.kind, entry.priority))

    def drop(self, entry_id: str) -> None:
        if self.entries.pop(entry_id, None) is not None:
            self._publish(force=True)

    def touch(self, entry_id: str, words: str | None = None, kind: str | None = None) -> None:
        entry = self.entries.get(entry_id)
        if not entry:
            return
        changed = (words and words != entry.words) or (kind and kind != entry.kind)
        entry.last_event_at = time.time()
        if words:
            entry.words = words
        if kind in KINDS:
            entry.kind = kind
        self._publish(force=bool(changed))

    def _publish(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_publish < PUBLISH_EVERY:
            return
        self._last_publish = now
        self.app.bus.publish("activity.updated", {"activity": self.snapshot()})

    # ------------------------------------------------------------------ what's showing
    def threshold(self, kind: str) -> float:
        return float(self.app.settings.get("activity.stale_seconds")) * QUIET_FACTOR.get(kind, 2)

    def describe(self, entry: Entry, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        quiet = now - entry.last_event_at
        limit = self.threshold(entry.kind)
        stale = bool(limit) and quiet > limit
        words = entry.words
        if stale:
            words = f"Still {words[:1].lower()}{words[1:]}" if not words.lower().startswith("still") else words
            if quiet > limit * 6:
                words += f" (no word for {elapsed(quiet)})"
        return {**asdict(entry), "stale": stale, "quiet_seconds": round(quiet), "elapsed": elapsed(now - entry.started_at),
                "display": f"{words} · {elapsed(now - entry.started_at)}", "stale_after": limit}

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        entries = sorted(self.entries.values(), key=lambda e: (e.priority, -e.last_event_at))
        described = [self.describe(e, now) for e in entries]
        waiting = self._needs_you()
        if waiting:
            described = sorted(described + [waiting], key=lambda e: (e["priority"], -e["last_event_at"]))
        top = described[0] if described else None
        if top:
            state = "waiting" if top["waiting_on_user"] else "working"
        else:
            quiet = False
            try:
                quiet = self.app.heartbeat.quiet()
            except Exception:
                pass
            state = "sleeping" if quiet else "idle"
        return {"state": state, "now": top, "entries": described, "more": max(0, len(described) - 1), "at": now}

    def _needs_you(self) -> dict[str, Any] | None:
        """Items the team needs the user for, as one waiting entry (they block bots' work)."""
        try:
            items = self.app.approvals.open_items()
        except Exception:
            return None
        if not items:
            return None
        first = min(i["created_at"] for i in items)
        entry = Entry("needs-you", "chief", "Weebo", WAITING, "waiting",
                      f"{len(items)} thing{'s' if len(items) > 1 else ''} from the team need{'' if len(items) > 1 else 's'} you",
                      started_at=first, last_event_at=max(i["updated_at"] for i in items), waiting_on_user=True,
                      link={"type": "card", "id": "needs-you"})
        return self.describe(entry)

    # ------------------------------------------------------------------ event handlers
    def _owner(self, conv_id: str) -> tuple[str, str]:
        bot = self.app.bots.for_conversation(conv_id)
        if bot:
            return bot["id"], bot["name"]
        return "chief", "Weebo"

    def _turn_started(self, _topic: str, data: dict[str, Any]) -> None:
        conv_id = data.get("conversation_id") or ""
        trigger = data.get("trigger") or ""
        owner, name = self._owner(conv_id)
        if trigger in USER_TRIGGERS:
            priority, words = REPLY, ("Writing you a reply" if owner == "chief" else f"{name} is answering you")
        elif trigger in REQUESTED_TRIGGERS:
            priority, words = REQUESTED, (f"{name} is on a job" if owner != "chief" else "Catching up on the team's work")
        else:
            priority = BACKGROUND
            words = {"brief": "Putting your brief together", "routine": f"{name}: running a routine",
                     "workflow": "Running a workflow"}.get(trigger, f"{name}: working on its own")
        self.put(Entry(f"turn:{conv_id}", owner, name, priority, "thinking", words,
                       link={"type": "conversation", "id": conv_id}))

    def _turn_completed(self, _topic: str, data: dict[str, Any]) -> None:
        self.drop(f"turn:{data.get('conversation_id')}")

    def _message(self, _topic: str, data: dict[str, Any]) -> None:
        conv_id = data.get("conversation_id") or ""
        message = data.get("message") or {}
        if message.get("kind") == "needs_you":
            self._publish(force=True)
        entry_id = f"turn:{conv_id}"
        if entry_id not in self.entries:
            return
        kind = message.get("kind")
        mdata = message.get("data") or {}
        if kind in activity.WORK_KINDS and message.get("status") == "running":
            self.touch(entry_id, mdata.get("doing"), mdata.get("activity"))
        elif kind == "text" and message.get("role") == "assistant" and message.get("status") == "streaming":
            entry = self.entries[entry_id]
            words = "Writing you a reply" if entry.priority == REPLY and entry.owner == "chief" else \
                f"{entry.owner_name} is writing"
            self.touch(entry_id, words, "writing")
        else:
            self.touch(entry_id)

    def _alive(self, _topic: str, data: dict[str, Any]) -> None:
        entry_id = f"turn:{data.get('conversation_id')}"
        if entry_id in self.entries:
            if _topic == "conv.delta":
                entry = self.entries[entry_id]
                self.touch(entry_id, entry.words if entry.kind == "writing" else None, "writing")
            else:
                self.touch(entry_id)

    def _asked(self, _topic: str, data: dict[str, Any]) -> None:
        ask = data.get("interaction") or {}
        who = ask.get("agent") or "Weebo"
        # Raw commands (and their arguments/secrets) belong on the approval card,
        # never in the plain-language activity slot.
        what = {"command": "run a command", "file_change": "edit files", "permissions": "grant permissions",
                "elicitation": "use a connector"}.get(ask.get("kind"), "take an action")
        words = f"{who} is waiting for your OK to {what}" if ask.get("kind") != "question" else \
            f"{who} has a question for you"
        self.put(Entry(f"ask:{ask.get('id')}", ask.get("task_id") or "chief", who, WAITING, "waiting", words,
                       waiting_on_user=True, link={"type": "conversation", "id": ask.get("conversation_id"),
                                                   "message": ask.get("message_id")}))

    def _answered(self, _topic: str, data: dict[str, Any]) -> None:
        self.drop(f"ask:{data.get('id')}")

    def _task(self, _topic: str, data: dict[str, Any]) -> None:
        task = data.get("task") or {}
        entry_id = f"task:{task.get('id')}"
        if task.get("status") not in ("queued", "running"):
            self.drop(entry_id)
            return
        evolution = task.get("kind") == "evolution"
        conv = task.get("conversation_id")
        bot = self.app.bots.for_conversation(conv) if conv else None
        priority = BACKGROUND if evolution or task.get("kind") == "audit" or bot else REQUESTED
        words = "Building an upgrade to myself" if evolution else (
            "Waiting for an agent slot" if task.get("status") == "queued" else f"Agent: {task.get('title', '')[:60]}")
        existing = self.entries.get(entry_id)
        if existing and task.get("status") == "running" and existing.words.startswith("Agent:"):
            words = existing.words  # keep its live progress
        self.put(Entry(entry_id, task.get("id") or "", task.get("title") or "Agent", priority,
                       "coding" if evolution else "delegating", words, link={"type": "task", "id": task.get("id")}))

    def _task_progress(self, _topic: str, data: dict[str, Any]) -> None:
        entry_id = f"task:{data.get('task_id')}"
        if entry_id in self.entries and data.get("progress"):
            self.touch(entry_id, f"Agent: {data['progress']}"[:120], data.get("activity") or None)

    def _heartbeat(self, _topic: str, data: dict[str, Any]) -> None:
        name = data.get("activity") or ""
        entry_id = f"system:{name}"
        if data.get("state") != "started":
            self.drop(entry_id)
            return
        kind, words = {"dream": ("dreaming", "Dreaming: tidying my memories"),
                       "audit": ("reviewing", "Reviewing my own code"),
                       "brief": ("writing", "Putting your brief together"),
                       "eval": ("reviewing", "Rehearsing past conversations")}.get(name, ("thinking", "Thinking"))
        self.put(Entry(entry_id, "system", "Weebo", BACKGROUND, kind, words))

    def _evolution(self, _topic: str, data: dict[str, Any]) -> None:
        proposal = data.get("proposal") or {}
        entry_id = f"evolution:{proposal.get('id')}"
        if proposal.get("status") == "merging":
            self.put(Entry(entry_id, "system", "Weebo", BACKGROUND, "coding", "Merging an upgrade"))
        else:
            self.drop(entry_id)
