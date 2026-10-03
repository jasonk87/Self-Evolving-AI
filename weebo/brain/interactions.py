"""Approvals and questions that pause Codex until the user decides in the UI."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import log
from ..store import new_id

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("interactions")

LEGACY_DECISIONS = {"accept": "approved", "acceptForSession": "approved_for_session", "decline": "denied", "cancel": "abort"}
DECISIONS = ("accept", "acceptForSession", "decline", "cancel")


@dataclass
class Pending:
    id: str
    kind: str  # command | file_change | permissions | question
    method: str
    params: dict[str, Any]
    thread_id: str
    turn_id: str
    conversation_id: str | None = None
    task_id: str | None = None
    message_id: str | None = None
    created_at: float = field(default_factory=time.time)
    future: asyncio.Future | None = None

    def summary(self) -> dict[str, Any]:
        p = self.params
        detail: dict[str, Any] = {"reason": p.get("reason")}
        if self.kind == "command":
            command = p.get("command")
            if isinstance(command, list):
                command = " ".join(command)
            detail.update(command=command, cwd=p.get("cwd"))
        elif self.kind == "file_change":
            changes = p.get("fileChanges") or p.get("changes") or {}
            detail.update(grant_root=p.get("grantRoot"), files=list(changes.keys()) if isinstance(changes, dict) else [])
        elif self.kind == "permissions":
            detail.update(permissions=p.get("permissions"), cwd=p.get("cwd"))
        elif self.kind == "question":
            detail.update(questions=p.get("questions") or [])
        elif self.kind == "confirm":
            detail.update(title=p.get("title"))
        return {
            "id": self.id, "kind": self.kind, "conversation_id": self.conversation_id, "task_id": self.task_id,
            "message_id": self.message_id, "created_at": self.created_at, **detail,
        }


def _kind_for(method: str) -> str:
    if method in ("item/commandExecution/requestApproval", "execCommandApproval"):
        return "command"
    if method in ("item/fileChange/requestApproval", "applyPatchApproval"):
        return "file_change"
    if method == "item/permissions/requestApproval":
        return "permissions"
    if method == "weebo/confirm":
        return "confirm"
    return "question"


class Interactions:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.pending: dict[str, Pending] = {}

    async def ask(self, method: str, params: dict[str, Any], conversation_id: str | None = None,
                  task_id: str | None = None) -> dict[str, Any]:
        pending = Pending(
            id=new_id("ask"), kind=_kind_for(method), method=method, params=params,
            thread_id=params.get("threadId") or params.get("conversationId") or "",
            turn_id=params.get("turnId") or "", conversation_id=conversation_id, task_id=task_id,
        )
        pending.future = asyncio.get_running_loop().create_future()
        self.pending[pending.id] = pending
        summary = pending.summary()
        if conversation_id:
            message = self.app.store.add_message(
                conversation_id, "event", kind="approval" if pending.kind != "question" else "question",
                data=summary, turn_id=pending.turn_id, status="pending",
            )
            pending.message_id = message["id"]
            summary["message_id"] = message["id"]
            self.app.bus.publish("conv.message", {"conversation_id": conversation_id, "message": message})
        if task_id:
            self.app.store.add_task_event(task_id, "approval", "Waiting for your approval", summary)
        self.app.bus.publish("interaction.pending", {"interaction": summary})
        self.app.bus.publish("weebo.mood", {"mood": "asking", "conversation_id": conversation_id})
        if task_id or not conversation_id:
            title = "An agent needs your approval" if task_id else "Weebo needs your approval"
            self.app.notify("approval", title, _describe(summary), {"interaction_id": pending.id, "task_id": task_id})
        try:
            decision, answers = await pending.future
        finally:
            self.pending.pop(pending.id, None)
        return self._response(pending, decision, answers)

    async def confirm(self, title: str, detail: str, conversation_id: str | None = None,
                      task_id: str | None = None, thread_id: str = "", turn_id: str = "") -> bool:
        """Ask the user to approve an action Weebo itself is about to take (not a Codex request)."""
        params = {"threadId": thread_id, "turnId": turn_id, "reason": detail, "title": title}
        response = await self.ask("weebo/confirm", params, conversation_id=conversation_id, task_id=task_id)
        return response.get("decision") in ("accept", "acceptForSession")

    def resolve(self, interaction_id: str, decision: str = "accept", answers: dict[str, list[str]] | None = None) -> bool:
        pending = self.pending.get(interaction_id)
        if pending is None or pending.future is None or pending.future.done():
            return False
        if pending.kind != "question" and decision not in DECISIONS:
            raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
        pending.future.set_result((decision, answers or {}))
        status = "answered" if pending.kind == "question" else decision
        self._finish(pending, status, answers)
        return True

    def cancel_turn(self, thread_id: str, turn_id: str | None = None) -> None:
        for pending in list(self.pending.values()):
            if pending.thread_id == thread_id and (turn_id is None or pending.turn_id == turn_id):
                if pending.future and not pending.future.done():
                    pending.future.set_result(("cancel", {}))
                self._finish(pending, "expired", None)

    def _finish(self, pending: Pending, status: str, answers: dict | None) -> None:
        if pending.message_id and pending.conversation_id:
            data = pending.summary()
            data["decision"] = status
            if answers:
                data["answers"] = answers
            message = self.app.store.update_message(pending.message_id, status=status, data=data)
            if message:
                self.app.bus.publish("conv.message", {"conversation_id": pending.conversation_id, "message": message})
        if pending.task_id:
            self.app.store.add_task_event(pending.task_id, "approval_resolved", f"Decision: {status}", {"id": pending.id})
        self.app.bus.publish("interaction.resolved", {"id": pending.id, "status": status,
                                                      "conversation_id": pending.conversation_id, "task_id": pending.task_id})

    def list(self) -> list[dict[str, Any]]:
        return [p.summary() for p in self.pending.values()]

    @staticmethod
    def _response(pending: Pending, decision: str, answers: dict[str, list[str]]) -> dict[str, Any]:
        if pending.kind == "question":
            return {"answers": {qid: {"answers": list(values)} for qid, values in (answers or {}).items()}}
        if pending.method in ("execCommandApproval", "applyPatchApproval"):
            return {"decision": LEGACY_DECISIONS.get(decision, "denied")}
        if pending.kind == "permissions":
            if decision in ("accept", "acceptForSession"):
                return {"permissions": pending.params.get("permissions") or {},
                        "scope": "session" if decision == "acceptForSession" else "turn"}
            return {"permissions": {}, "scope": "turn"}
        return {"decision": decision}


def _describe(summary: dict[str, Any]) -> str:
    if summary.get("command"):
        return f"Run: {str(summary['command'])[:200]}"
    if summary.get("files"):
        return "Edit: " + ", ".join(summary["files"][:5])
    if summary.get("questions"):
        return summary["questions"][0].get("question", "A question")
    return summary.get("reason") or "Review the request in Weebo."
