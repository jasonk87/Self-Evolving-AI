"""Reminders and routines. A routine (action='prompt') makes Weebo run an instruction
by itself at the scheduled time, which is how recurring autonomous checks work."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .. import log
from ..timeparse import TimeParseError, describe_recurrence, next_occurrence, normalize_recurrence, parse_when

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("scheduler")

MISSED_GRACE = 6 * 3600
ROUTINE_MAX_ATTEMPTS = 3
ROUTINE_RETRY_DELAY = 30


class Scheduler:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def add(self, text: str, when: str, recurrence: str = "", action: str = "notify",
            conversation_id: str | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise TimeParseError("Reminder text is empty.")
        if action not in ("notify", "prompt"):
            raise TimeParseError("action must be 'notify' or 'prompt'.")
        due = parse_when(when).timestamp()
        if due < time.time() - 60:
            raise TimeParseError(f"{datetime.fromtimestamp(due):%b %d %I:%M %p} is in the past.")
        rec = normalize_recurrence(recurrence)
        if action == "prompt" and rec.startswith("every ") and int(rec[6:-1]) < 15 * 60:
            raise TimeParseError("Routines can repeat at most every 15 minutes.")
        reminder = self.app.store.add_reminder(text, due, rec, conversation_id, action)
        self.app.bus.publish("reminder.updated", {"reminder": reminder})
        self.app.store.journal("schedule", f"Scheduled {'routine' if action == 'prompt' else 'reminder'}",
                               f"{text[:200]} — {datetime.fromtimestamp(due):%a %b %d %I:%M %p}"
                               + (f", {describe_recurrence(rec)}" if rec else ""))
        self._wake.set()
        return reminder

    def cancel(self, reminder_id: str) -> bool:
        reminder = self.app.store.get_reminder(reminder_id)
        if reminder is None or reminder["status"] != "pending":
            return False
        reminder = self.app.store.update_reminder(reminder_id, status="cancelled")
        self.app.bus.publish("reminder.updated", {"reminder": reminder})
        return True

    def snooze(self, reminder_id: str, minutes: int = 10) -> dict[str, Any] | None:
        reminder = self.app.store.get_reminder(reminder_id)
        if reminder is None:
            return None
        reminder = self.app.store.update_reminder(reminder_id, status="pending", due_at=time.time() + minutes * 60)
        self.app.bus.publish("reminder.updated", {"reminder": reminder})
        self._wake.set()
        return reminder

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler tick failed")
            self._wake.clear()
            upcoming = self.app.store.list_reminders(limit=1)
            delay = 30.0
            if upcoming:
                delay = max(0.5, min(30.0, upcoming[0]["due_at"] - time.time()))
            try:
                await asyncio.wait_for(self._wake.wait(), delay)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        now = time.time()
        for reminder in self.app.store.due_reminders(now):
            await self._fire(reminder, now)
        for occurrence in self.app.store.pending_routines(now, ROUTINE_MAX_ATTEMPTS):
            data = occurrence["data"]
            await self.app.conversations.run_event(
                occurrence["conversation_id"], data["prompt"], data["notice"], trigger="routine",
                notice_kind="reminder", notice_data=data["notice_data"], occurrence_id=occurrence["id"],
            )

    def recover_after_restart(self) -> None:
        # Queued and confirmed failed starts remain retryable. Anything that may
        # have run must be surfaced, never replayed automatically.
        for occurrence in self.app.store.query(
            "SELECT * FROM routine_occurrences WHERE status='starting' OR (status='started' AND finished_at IS NULL)"
        ):
            self.delivery_interrupted(occurrence["id"], "Weebo restarted while this routine may have been running.")
        for occurrence in self.app.store.query(
            "SELECT * FROM routine_occurrences WHERE status='interrupted' OR (status='failed' AND attempts>=?)",
            (ROUTINE_MAX_ATTEMPTS,),
        ):
            self._delivery_notice(occurrence, self._failure_reason(occurrence), occurrence["status"])

    def delivery_starting(self, occurrence_id: str) -> bool:
        occurrence = self.app.store.get_routine(occurrence_id)
        if (not occurrence or occurrence["status"] not in ("queued", "failed")
                or occurrence["attempts"] >= ROUTINE_MAX_ATTEMPTS or occurrence["next_attempt_at"] > time.time()):
            return False
        self.app.store.update_routine(occurrence_id, status="starting", attempts=occurrence["attempts"] + 1)
        return True

    def delivery_started(self, occurrence_id: str, turn_id: str | None) -> None:
        occurrence = self.app.store.get_routine(occurrence_id)
        if not turn_id or not occurrence or occurrence["status"] != "starting":
            return
        self.app.store.update_routine(occurrence_id, status="started", started_at=time.time(), error="",
                                     data={**occurrence["data"], "turn_id": turn_id})
        self.app.store.journal("routine", "Started a routine", occurrence["data"]["reminder"]["text"][:300],
                               data={"occurrence_id": occurrence_id})

    def delivery_failed(self, occurrence_id: str, error: str) -> None:
        occurrence = self.app.store.get_routine(occurrence_id)
        if not occurrence or occurrence["status"] != "starting":
            return
        exhausted = occurrence["attempts"] >= ROUTINE_MAX_ATTEMPTS
        self.app.store.update_routine(
            occurrence_id, status="failed", error=error,
            next_attempt_at=time.time() + ROUTINE_RETRY_DELAY * 2 ** (occurrence["attempts"] - 1),
        )
        if exhausted:
            occurrence = self.app.store.get_routine(occurrence_id)
            self._delivery_notice(occurrence, self._failure_reason(occurrence), "failed")

    def delivery_interrupted(self, occurrence_id: str, error: str) -> None:
        occurrence = self.app.store.get_routine(occurrence_id)
        if not occurrence or occurrence["status"] == "interrupted":
            return
        occurrence = self.app.store.update_routine(occurrence_id, status="interrupted", error=error, finished_at=time.time())
        self._delivery_notice(occurrence, self._failure_reason(occurrence), "interrupted")

    def _failure_reason(self, occurrence: dict[str, Any]) -> str:
        if occurrence["status"] == "interrupted":
            return occurrence["error"] + " It will not be retried automatically; ask me to run it again if needed."
        return f"Routine could not start after {ROUTINE_MAX_ATTEMPTS} attempts: {occurrence['error']}"

    def _delivery_notice(self, occurrence: dict[str, Any], reason: str, status: str) -> None:
        if occurrence["data"].get("notice_status") == status:
            return
        text = f"Routine: {occurrence['data']['reminder']['text'][:160]}\n{reason}"
        conv_id = occurrence["conversation_id"]
        if not self.app.store.get_conversation(conv_id):
            conv_id = self.app.conversations.desk()["id"]
        data = {**occurrence["data"]["notice_data"], "occurrence_id": occurrence["id"]}
        note = self.app.store.add_message(conv_id, "event", text, kind="error", data=data)
        self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": note})
        self.app.store.journal("routine", "Routine delivery interrupted" if status == "interrupted"
                               else "Routine delivery failed", text, data=data)
        self.app.notify("routine", "Routine needs attention", text, {**data, "conversation_id": conv_id})
        self.app.store.update_routine(occurrence["id"], data={**occurrence["data"], "notice_status": status})

    async def _fire(self, reminder: dict[str, Any], now: float) -> None:
        store = self.app.store
        current = store.get_reminder(reminder["id"])
        if not current or current["status"] != "pending" or current["due_at"] != reminder["due_at"]:
            return
        missed = now - reminder["due_at"] > MISSED_GRACE
        conv_id = reminder.get("conversation_id")
        if not (missed and reminder["recurrence"]) and (not conv_id or not store.get_conversation(conv_id)):
            conv_id = self.app.conversations.desk()["id"]
        suffix = " (missed while Weebo was off)" if missed else ""
        if reminder["action"] == "prompt" and not (missed and reminder["recurrence"]):
            notice = f"Routine: {reminder['text'][:160]}{suffix}"
            prompt = (f"[Event: a scheduled routine just fired{suffix}]\nRun this now: {reminder['text']}\n\n"
                      "Do it, then report back briefly. If the result is important, also call notify_user.")
            occurrence = store.queue_routine(reminder, conv_id, {
                "prompt": prompt, "notice": notice, "reminder": reminder,
                "notice_data": {"reminder_id": reminder["id"], "due_at": reminder["due_at"],
                                "recurrence": reminder["recurrence"], "routine": True},
            })
            if occurrence:
                store.journal("routine", "Queued a routine", reminder["text"][:300],
                              data={"occurrence_id": occurrence["id"]})
        next_due = next_occurrence(reminder["due_at"], reminder["recurrence"], now)
        if next_due:
            updated = store.update_reminder(reminder["id"], due_at=next_due, fired_at=now)
        else:
            updated = store.update_reminder(reminder["id"], status="done", fired_at=now)
        self.app.bus.publish("reminder.updated", {"reminder": updated})
        if missed and reminder["recurrence"]:
            return  # skip stale repeats quietly; the next one is already scheduled
        if reminder["action"] == "prompt":
            return
        message = store.add_message(conv_id, "event", reminder["text"] + suffix, kind="reminder",
                                    data={"reminder_id": reminder["id"], "recurrence": reminder["recurrence"]})
        self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": message})
        self.app.notify("reminder", "Reminder", reminder["text"] + suffix,
                        {"reminder_id": reminder["id"], "conversation_id": conv_id, "speak": True})
        self.app.bus.publish("weebo.mood", {"mood": "alert"})
