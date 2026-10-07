"""Conversations: each Weebo chat is a persistent Codex thread.

Turns stream into the UI as they happen (text deltas, commands, file edits, tool
calls, plans, diffs). Typing while Weebo is mid-turn steers the running turn, so
the user never waits to add a thought. Many conversations can run at once.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import log, paths
from ..codex.engine import image_input, text_input
from ..codex.rpc import EngineClosed, RpcError
from . import persona
from .tools import ToolContext, registry

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("conversation")

STATUS_MAP = {"inProgress": "running", "completed": "done", "failed": "failed", "declined": "declined"}
MAX_OUTPUT = 16_000
MAX_DIFF = 200_000


@dataclass
class TurnState:
    trigger: str = "user"
    occurrence_id: str | None = None
    turn_id: str | None = None
    started_at: float = field(default_factory=time.time)
    streams: dict[str, str] = field(default_factory=dict)
    reasoning: str = ""
    outputs: dict[str, int] = field(default_factory=dict)
    diff: str = ""
    final_text: str = ""
    used_memories: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)


@dataclass
class Session:
    conv_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    turn: TurnState | None = None
    pending_context: list[str] = field(default_factory=list)
    followups: list[dict[str, Any]] = field(default_factory=list)


def tools_hash(scope: str = "chat") -> str:
    return hashlib.sha1(json.dumps(registry.specs(scope), sort_keys=True).encode()).hexdigest()[:12]


def sandbox_for(level: str, extra_roots: list[str]) -> tuple[str, str, dict[str, Any]]:
    """Map Weebo's autonomy level to (approvalPolicy, sandbox mode, sandboxPolicy)."""
    if level == "full":
        return "never", "danger-full-access", {"type": "dangerFullAccess"}
    if level == "cautious":
        return "on-request", "read-only", {"type": "readOnly", "networkAccess": False}
    return "on-request", "workspace-write", {
        "type": "workspaceWrite", "writableRoots": extra_roots, "networkAccess": True,
        "excludeTmpdirEnvVar": False, "excludeSlashTmp": False,
    }


_CORRECTION = re.compile(  # openings that push back on the last answer by themselves
    r"^\W*(?:"
    r"no(?:pe)?\b(?![\s,.!]*(?:problem|worries|thanks|thank|rush|need|biggie|prob)\b)"
    r"|wrong\b|incorrect\b|not (?:quite|really|right|what i)"
    r"|that'?s (?:not|wrong|incorrect)|that is (?:not|wrong|incorrect)|that isn'?t|that wasn'?t"
    r"|you (?:forgot|missed|misunderstood|misread|ignored|got (?:it|that) wrong|didn'?t|did not|should(?:n'?t| not) have)"
    r"|i (?:told you|already told you)\b"
    r"|actually,? (?:it'?s|it is|that'?s|that is)\b"
    r"|(?:please )?stop (?:doing|saying|using|adding)|why did you|why would you"
    r")",
    re.IGNORECASE,
)
_RESTATEMENT = re.compile(r"^\W*(?:actually,? )?i (?:said|meant|asked for|asked you|wanted)\b", re.IGNORECASE)
_PUSHBACK = re.compile(r"\b(?:not|instead|wrong|never|didn'?t|don'?t)\b", re.IGNORECASE)

CORRECTION_HINT = (
    "The user's message looks like a correction of your previous reply. Fix the mistake directly. If it reveals "
    "a lasting preference or a lesson about how to help them, save it now with `remember` (kind \"lesson\" or "
    "\"preference\") so it doesn't happen again; skip that for one-off slips."
)


def looks_like_correction(text: str) -> bool:
    """Heuristic: does this message push back on Weebo's last answer? (Errs toward missing, not nagging.)
    "I said ..." only counts with pushback ("I said Friday, not Thursday"), so "I said hi to Sam" doesn't."""
    text = text or ""
    return bool(_CORRECTION.match(text) or (_RESTATEMENT.match(text) and _PUSHBACK.search(text)))


def auto_title(text: str) -> str:
    clean = re.sub(r"\s+", " ", re.sub(r"[`*#>\[\]]|(?<!\w)_|_(?!\w)", "", text)).strip()
    if not clean:
        return "New chat"
    words = clean.split(" ")
    title = " ".join(words[:7])
    if len(words) > 7:
        title += "…"
    return title[:60].rstrip(" ,.;:") or "New chat"


def _error_text(error: dict[str, Any] | None) -> str:
    if not error:
        return "The turn failed."
    message = error.get("message") or "The turn failed."
    try:
        parsed = json.loads(message)
        inner = parsed.get("error", {}) if isinstance(parsed, dict) else {}
        message = inner.get("message") or parsed.get("message") or message
    except (json.JSONDecodeError, AttributeError):
        pass
    return message


def _tail(text: str | None, limit: int = MAX_OUTPUT) -> str:
    text = text or ""
    return text if len(text) <= limit else "…" + text[-limit:]


def _mcp_result_text(result: dict[str, Any] | None) -> str:
    if not result:
        return ""
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return _tail("\n".join(parts), 4000)


def item_to_message(item: dict[str, Any]) -> tuple[str, str, str, dict[str, Any], str] | None:
    """Translate a Codex ThreadItem into (kind, role, content, data, status)."""
    kind = item.get("type")
    status = STATUS_MAP.get(item.get("status") or "completed", "done")
    if kind == "agentMessage":
        return "text", "assistant", item.get("text", ""), {"phase": item.get("phase")}, "done"
    if kind == "plan":
        return "text", "assistant", item.get("text", ""), {"phase": "plan"}, "done"
    if kind == "reasoning":
        summary = "\n\n".join(s for s in item.get("summary") or [] if s)
        return "reasoning", "assistant", summary, {}, "done"
    if kind == "commandExecution":
        actions = [{"type": a.get("type"), "name": a.get("name"), "path": a.get("path"), "query": a.get("query")}
                   for a in item.get("commandActions") or [] if isinstance(a, dict)]
        return "command", "tool", item.get("command", ""), {
            "cwd": item.get("cwd"), "exitCode": item.get("exitCode"), "durationMs": item.get("durationMs"),
            "output": _tail(item.get("aggregatedOutput")), "actions": actions,
        }, status
    if kind == "fileChange":
        changes = []
        for change in item.get("changes") or []:
            change_kind = (change.get("kind") or {}).get("type", "update")
            changes.append({"path": change.get("path"), "kind": change_kind, "diff": _tail(change.get("diff"), 40_000)})
        summary = ", ".join(Path(c["path"] or "").name for c in changes[:4])
        return "file_change", "tool", summary, {"changes": changes}, status
    if kind == "mcpToolCall":
        return "tool", "tool", f"{item.get('server')}.{item.get('tool')}", {
            "source": "mcp", "server": item.get("server"), "tool": item.get("tool"),
            "arguments": item.get("arguments"), "output": _mcp_result_text(item.get("result")),
            "error": (item.get("error") or {}).get("message"), "durationMs": item.get("durationMs"),
        }, status
    if kind == "dynamicToolCall":
        output = "\n".join(c.get("text", "") for c in item.get("contentItems") or [] if c.get("type") == "inputText")
        images = [c.get("imageUrl") for c in item.get("contentItems") or [] if c.get("type") == "inputImage"]
        if item.get("status") == "completed" and item.get("success") is False:
            status = "failed"
        return "tool", "tool", item.get("tool", ""), {
            "source": "weebo", "tool": item.get("tool"), "arguments": item.get("arguments"),
            "output": _tail(output, 4000), "has_image": bool(images), "durationMs": item.get("durationMs"),
        }, status
    if kind == "webSearch":
        action = item.get("action") or {}
        query = item.get("query") or action.get("query") or action.get("url") or ""
        return "web_search", "tool", query, {"action": action}, "done"
    if kind == "collabAgentToolCall":
        states = {k: (v or {}).get("status") for k, v in (item.get("agentsStates") or {}).items()}
        return "subagent", "tool", item.get("prompt") or "", {
            "tool": item.get("tool"), "receivers": item.get("receiverThreadIds"), "states": states,
            "model": item.get("model"),
        }, STATUS_MAP.get(item.get("status") or "completed", "done")
    if kind == "imageView":
        return "image_view", "tool", item.get("path", ""), {"path": item.get("path")}, "done"
    if kind == "imageGeneration":
        return "image", "assistant", item.get("revisedPrompt") or "", {
            "saved_path": item.get("savedPath"), "status": item.get("status"),
        }, "done"
    if kind in ("enteredReviewMode", "exitedReviewMode"):
        return "review", "assistant", item.get("review", ""), {"phase": kind}, "done"
    if kind == "contextCompaction":
        return "notice", "event", "Compressed older context to keep the conversation going.", {}, "done"
    return None


class ConversationManager:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.sessions: dict[str, Session] = {}
        self.thread_conv: dict[str, str] = {}

    # ------------------------------------------------------------------ basics
    def session(self, conv_id: str) -> Session:
        session = self.sessions.get(conv_id)
        if session is None:
            session = self.sessions[conv_id] = Session(conv_id)
        return session

    def busy(self, conv_id: str) -> bool:
        session = self.sessions.get(conv_id)
        return bool(session and session.turn)

    def active_turns(self) -> list[dict[str, Any]]:
        out = []
        for conv_id, session in self.sessions.items():
            if session.turn:
                out.append({"conversation_id": conv_id, "trigger": session.turn.trigger,
                            "started_at": session.turn.started_at})
        return out

    def live_state(self, conv_id: str) -> dict[str, Any]:
        """Partial streaming state so a freshly loaded page can catch up mid-turn."""
        session = self.sessions.get(conv_id)
        if not session or not session.turn:
            return {"busy": False}
        turn = session.turn
        return {
            "busy": True, "trigger": turn.trigger, "started_at": turn.started_at, "reasoning": turn.reasoning,
            "streams": {f"{conv_id}:{k}": v for k, v in turn.streams.items()},
        }

    def create(self, title: str = "New chat", cwd: str | None = None, kind: str = "chat") -> dict[str, Any]:
        if cwd:
            cwd = str(Path(cwd).expanduser())
            if not Path(cwd).is_dir():
                raise ValueError(f"Folder not found: {cwd}")
        conv = self.app.store.create_conversation(title=title, kind=kind, cwd=cwd)
        self.app.bus.publish("conv.created", {"conversation": conv})
        return conv

    def desk(self) -> dict[str, Any]:
        """Weebo's own pinned conversation for proactive briefs, routines and dreams."""
        conv = self.app.store.find_conversation_by_kind("desk")
        if conv is None:
            conv = self.app.store.create_conversation(title="Weebo's Desk", kind="desk")
            self.app.store.add_message(
                conv["id"], "assistant",
                "This is my desk. Briefings, routines, dream insights and agent check-ins land here, "
                "so your other chats stay clean. Reply any time.",
            )
            self.app.bus.publish("conv.created", {"conversation": conv})
        return conv

    async def delete(self, conv_id: str) -> None:
        conv = self.app.store.get_conversation(conv_id)
        if not conv:
            return
        session = self.sessions.pop(conv_id, None)
        if session and session.turn and session.turn.turn_id and conv.get("thread_id"):
            try:
                await self.app.engine.interrupt(conv["thread_id"], session.turn.turn_id)
            except (RpcError, EngineClosed, asyncio.TimeoutError):
                pass
        if conv.get("thread_id"):
            self.thread_conv.pop(conv["thread_id"], None)
            self.app.engine.unroute(conv["thread_id"])
            asyncio.create_task(self.app.engine.archive_thread(conv["thread_id"]))
        self.app.store.delete_conversation(conv_id)
        self.app.bus.publish("conv.deleted", {"conversation_id": conv_id})

    # ------------------------------------------------------------------ sending
    async def send(self, conv_id: str, text: str, images: list[str] | None = None) -> dict[str, Any]:
        conv = self.app.store.get_conversation(conv_id)
        if conv is None:
            raise ValueError("Conversation not found")
        text = (text or "").strip()
        images = [p for p in (images or []) if Path(p).is_file()]
        if not text and not images:
            raise ValueError("Message is empty")
        session = self.session(conv_id)
        self.app.heartbeat.touch()
        async with session.lock:
            # Re-read inside the lock: a message sent just before this one may have created the thread meanwhile.
            conv = self.app.store.get_conversation(conv_id) or conv
            message = self.app.store.add_message(conv_id, "user", text, data={"images": [Path(p).name for p in images]})
            if conv["title"] in ("New chat", "") and conv["kind"] == "chat" and text:
                conv = self.app.store.update_conversation(conv_id, title=auto_title(text)) or conv
                self.app.bus.publish("conv.updated", {"conversation": conv})
            self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": message})
            inputs = ([text_input(text)] if text else []) + [image_input(p) for p in images]

            if session.turn and session.turn.turn_id and conv.get("thread_id"):
                try:
                    await self.app.engine.steer(conv["thread_id"], session.turn.turn_id, inputs)
                    message = self.app.store.update_message(message["id"], data={**message["data"], "steered": True})
                    self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": message})
                    return message  # type: ignore[return-value]
                except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
                    logger.info("Steer failed (%s)", exc)
            # A failed steer does not confirm that the server turn has finished.
            # Only start again after a terminal notification cleared the active turn.
            if session.turn is not None:
                raise ValueError("Could not add your message to the active turn. Please retry after it finishes.")
            if text and looks_like_correction(text):
                self._on_correction(session, conv_id, text)
            await self._start_turn(session, conv, inputs, trigger="user", query=text)
        return message

    async def run_event(self, conv_id: str, prompt: str, notice: str, trigger: str = "event",
                        notice_kind: str = "notice", notice_data: dict | None = None,
                        occurrence_id: str | None = None) -> str:
        """Run a turn Weebo starts itself (routine, agent report, brief). The prompt is hidden;
        the UI shows ``notice`` instead of a user bubble. Return the dispatch outcome
        so a caught startup error cannot be mistaken for an accepted turn."""
        conv = self.app.store.get_conversation(conv_id)
        if conv is None:
            if occurrence_id:
                self.app.scheduler.delivery_interrupted(occurrence_id, "The routine's conversation was deleted.")
            return "failed"
        session = self.session(conv_id)
        async with session.lock:
            if occurrence_id:
                occurrence = self.app.store.get_routine(occurrence_id)
                if not occurrence:
                    return "failed"
                if occurrence["status"] not in ("queued", "failed"):
                    return occurrence["status"]
            if session.turn:
                if not occurrence_id or not any(f.get("occurrence_id") == occurrence_id for f in session.followups):
                    session.followups.append({"prompt": prompt, "notice": notice, "trigger": trigger,
                                              "notice_kind": notice_kind, "notice_data": notice_data,
                                              "occurrence_id": occurrence_id})
                return "queued"
            if occurrence_id and not self.app.scheduler.delivery_starting(occurrence_id):
                return occurrence["status"]
            # A scheduler tick can start a durable queue entry before its in-memory
            # followup is drained (for example after an interrupted foreground turn).
            if occurrence_id:
                session.followups[:] = [f for f in session.followups if f.get("occurrence_id") != occurrence_id]
            if notice:
                note = self.app.store.add_message(conv_id, "event", notice, kind=notice_kind, data=notice_data or {})
                self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": note})
            return await self._start_turn(session, conv, [text_input(prompt)], trigger=trigger, query=prompt,
                                          occurrence_id=occurrence_id)

    def add_context(self, conv_id: str, text: str) -> None:
        self.session(conv_id).pending_context.append(text)

    def _on_correction(self, session: Session, conv_id: str, text: str) -> None:
        """Learn from being corrected now, not at the next dream: nudge Weebo to save the lesson, and keep
        the moment as a behavior check so future versions of Weebo are tested against it."""
        session.pending_context.append(CORRECTION_HINT)
        try:
            case = self.app.evals.capture_correction(conv_id, text)
            if case:
                logger.info("Saved a behavior check from a correction: %s", case["title"])
        except Exception as exc:  # learning must never break sending a message
            logger.warning("Couldn't record the correction: %s", exc)

    async def interrupt(self, conv_id: str) -> bool:
        conv = self.app.store.get_conversation(conv_id)
        session = self.sessions.get(conv_id)
        if not conv or not session or not session.turn:
            return False
        session.followups.clear()
        if session.turn.turn_id and conv.get("thread_id"):
            try:
                await self.app.engine.interrupt(conv["thread_id"], session.turn.turn_id)
                return True
            except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
                logger.warning("Interrupt failed: %s", exc)
        self._finish_turn(conv_id, "interrupted", None)
        return True

    # ------------------------------------------------------------------ turns
    async def _start_turn(self, session: Session, conv: dict[str, Any], inputs: list[dict[str, Any]],
                          trigger: str, query: str, occurrence_id: str | None = None) -> str:
        state = session.turn = TurnState(trigger=trigger, occurrence_id=occurrence_id)
        self.app.bus.publish("conv.turn.started", {"conversation_id": conv["id"], "trigger": trigger})
        requested = False
        try:
            thread_id = await self._ensure_thread(session, conv)
            conv = self.app.store.get_conversation(conv["id"]) or conv
            extra = list(session.pending_context)
            session.pending_context.clear()
            context, used = persona.turn_context(self.app, conv, query, extra)
            state.used_memories = used
            overrides = self._turn_overrides(conv)
            await self.app.engine.ensure_ready()
            requested = True
            turn = await self.app.engine.start_turn(thread_id, inputs, context=context, **overrides)
            if not turn.get("id"):
                raise ValueError("The engine did not confirm a turn id.")
            if occurrence_id:
                self.app.scheduler.delivery_started(occurrence_id, turn["id"])
            if session.turn and not session.turn.turn_id:
                session.turn.turn_id = turn["id"]
            return "started"
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            logger.warning("Could not start turn in %s: %s", conv["id"], message)
            if not isinstance(exc, (EngineClosed, RpcError)):
                logger.exception("Unexpected turn start failure")
            self.app.diagnostics.record("turn_start_failed", message)
            err = self.app.store.add_message(conv["id"], "event", message, kind="error")
            self.app.bus.publish("conv.message", {"conversation_id": conv["id"], "message": err})
            outcome = "failed"
            if occurrence_id:
                if state.turn_id:
                    # A notification already confirmed acceptance, even if the
                    # request response was lost. Never retry this occurrence.
                    occurrence = self.app.store.get_routine(occurrence_id)
                    if occurrence and occurrence["status"] == "started" and occurrence["finished_at"]:
                        outcome = "started"
                    else:
                        outcome = "interrupted"
                        self.app.scheduler.delivery_interrupted(occurrence_id,
                                                                "Routine started but its engine response was lost: " + message)
                elif requested and not isinstance(exc, RpcError):
                    outcome = "interrupted"
                    self.app.scheduler.delivery_interrupted(occurrence_id, "Routine startup was not confirmed: " + message)
                else:
                    self.app.scheduler.delivery_failed(occurrence_id, message)
            if session.turn is state:
                self._finish_turn(conv["id"], "failed", None, publish_error=False)
            return outcome

    async def _ensure_thread(self, session: Session, conv: dict[str, Any]) -> str:
        engine = self.app.engine
        thread_id = conv.get("thread_id")
        current_hash = tools_hash("chat")
        meta = conv.get("meta") or {}
        if thread_id and thread_id in engine.loaded_threads:
            self._route(conv["id"], thread_id)
            return thread_id
        approval, sandbox_mode, _ = sandbox_for(self.app.settings.get("autonomy.level"), [])
        cwd = conv.get("cwd") or str(paths.workspace_dir())
        if thread_id and meta.get("tools_hash") == current_hash:
            try:
                self._route(conv["id"], thread_id)
                await engine.resume_thread(
                    thread_id, cwd=cwd, approvalPolicy=approval, sandbox=sandbox_mode,
                    developerInstructions=persona.developer_instructions(self.app, "chat"),
                )
                return thread_id
            except (RpcError, asyncio.TimeoutError) as exc:
                logger.info("Could not resume thread %s (%s); starting fresh", thread_id, exc)
                self.app.engine.unroute(thread_id)
        if thread_id:
            # Old thread is gone or has outdated tools: carry the recent dialogue over.
            recap = self._recap(conv["id"])
            if recap:
                session.pending_context.insert(0, recap)
        result = await engine.start_thread(
            cwd=cwd, approvalPolicy=approval, sandbox=sandbox_mode,
            developerInstructions=persona.developer_instructions(self.app, "chat"),
            dynamicTools=registry.specs("chat"), serviceName="weebo", ephemeral=False,
            model=engine.default_model(self.app.settings.get("codex.model")),
        )
        new_thread = result["thread"]["id"]
        meta["tools_hash"] = current_hash
        self.app.store.update_conversation(conv["id"], thread_id=new_thread, meta=meta)
        self._route(conv["id"], new_thread)
        return new_thread

    def _recap(self, conv_id: str) -> str:
        rows = self.app.store.recent_dialogue(conv_id, limit=16)
        if not rows:
            return ""
        lines = ["Recap of this conversation so far (restored after Weebo restarted):"]
        for row in rows[:-1] if rows and rows[-1]["role"] == "user" else rows:
            who = "User" if row["role"] == "user" else "Weebo"
            lines.append(f"{who}: {row['content'][:600]}")
        return "\n".join(lines)

    def _turn_overrides(self, conv: dict[str, Any]) -> dict[str, Any]:
        settings = self.app.settings
        engine = self.app.engine
        model = engine.default_model(settings.get("codex.model"))
        effort = engine.clamp_effort(model, settings.get("codex.chat_effort"))
        roots = [str(paths.workspace_dir()), str(paths.uploads_dir())]
        approval, _, policy = sandbox_for(settings.get("autonomy.level"), roots)
        overrides: dict[str, Any] = {"model": model, "effort": effort, "approvalPolicy": approval,
                                     "sandboxPolicy": policy, "summary": "auto",
                                     "cwd": conv.get("cwd") or str(paths.workspace_dir())}
        info = engine.model_info(model) or {}
        tiers = [t.get("id") for t in info.get("serviceTiers") or []]
        if settings.get("codex.fast_mode") and "priority" in tiers:
            overrides["serviceTier"] = "priority"
        return overrides

    def _route(self, conv_id: str, thread_id: str) -> None:
        self.thread_conv[thread_id] = conv_id
        self.app.engine.route(
            thread_id,
            listener=lambda method, params, c=conv_id: self._on_event(c, method, params),
            request_handler=lambda method, params, c=conv_id: self._on_request(c, method, params),
        )

    # ------------------------------------------------------------------ codex → weebo
    async def _on_request(self, conv_id: str, method: str, params: dict[str, Any]) -> Any:
        if method == "item/tool/call":
            session = self.session(conv_id)
            if session.turn:
                session.turn.tools_used.append(params.get("tool", ""))
            ctx = ToolContext(self.app, params.get("threadId", ""), params.get("turnId", ""), conversation_id=conv_id,
                              trigger=session.turn.trigger if session.turn else "event")
            result = await registry.call(ctx, params.get("tool", ""), params.get("arguments") or {})
            return result.to_codex()
        return await self.app.interactions.ask(method, params, conversation_id=conv_id)

    def _on_event(self, conv_id: str, method: str, params: dict[str, Any]) -> None:
        session = self.session(conv_id)
        bus = self.app.bus
        store = self.app.store
        event_turn_id = params.get("turnId") or (params.get("turn") or {}).get("id")
        if event_turn_id:
            if session.turn is None or session.turn.turn_id is None:
                if method != "turn/started":
                    return
            elif event_turn_id != session.turn.turn_id:
                return
        if method == "turn/started":
            if session.turn is None:
                session.turn = TurnState(trigger="external")
                bus.publish("conv.turn.started", {"conversation_id": conv_id, "trigger": "external"})
            session.turn.turn_id = (params.get("turn") or {}).get("id") or session.turn.turn_id
            if session.turn.occurrence_id:
                self.app.scheduler.delivery_started(session.turn.occurrence_id, session.turn.turn_id)
            return
        if method == "engine/closed":
            if session.turn:
                if session.turn.occurrence_id:
                    self.app.scheduler.delivery_interrupted(session.turn.occurrence_id,
                                                            "The Codex engine stopped while the routine may have been running.")
                err = store.add_message(conv_id, "event", "The Codex engine stopped mid-turn. It restarts on its own; "
                                        "send your message again in a moment.", kind="error")
                bus.publish("conv.message", {"conversation_id": conv_id, "message": err})
                self._finish_turn(conv_id, "failed", None, publish_error=False)
            return
        turn = session.turn
        if method == "item/agentMessage/delta":
            if turn is None:
                return
            item_id = params.get("itemId", "")
            turn.streams[item_id] = turn.streams.get(item_id, "") + params.get("delta", "")
            bus.publish("conv.delta", {"conversation_id": conv_id, "message_id": f"{conv_id}:{item_id}",
                                       "delta": params.get("delta", "")})
            return
        if method == "item/reasoning/summaryTextDelta":
            if turn is not None:
                turn.reasoning = (turn.reasoning + params.get("delta", ""))[-2000:]
            bus.publish("conv.reasoning", {"conversation_id": conv_id, "delta": params.get("delta", "")})
            return
        if method == "item/reasoning/summaryPartAdded":
            if turn is not None:
                turn.reasoning = ""
            bus.publish("conv.reasoning", {"conversation_id": conv_id, "reset": True})
            return
        if method == "item/commandExecution/outputDelta":
            if turn is None:
                return
            item_id = params.get("itemId", "")
            sent = turn.outputs.get(item_id, 0)
            if sent < 64_000:
                delta = params.get("delta", "")
                turn.outputs[item_id] = sent + len(delta)
                bus.publish("conv.output", {"conversation_id": conv_id, "message_id": f"{conv_id}:{item_id}",
                                            "delta": delta})
            return
        if method in ("item/started", "item/completed"):
            self._on_item(conv_id, session, params.get("item") or {}, completed=method == "item/completed")
            return
        if method == "turn/plan/updated":
            plan = [{"step": s.get("step"), "status": s.get("status")} for s in params.get("plan") or []]
            turn_id = params.get("turnId") or (turn.turn_id if turn else "")
            message = store.upsert_message(f"{conv_id}:plan:{turn_id}", conv_id, "assistant", "plan",
                                           params.get("explanation") or "", {"steps": plan}, turn_id)
            bus.publish("conv.message", {"conversation_id": conv_id, "message": message})
            return
        if method == "turn/diff/updated":
            if turn is not None:
                turn.diff = params.get("diff") or ""
            return
        if method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") or {}
            window = usage.get("modelContextWindow")
            last = (usage.get("last") or {}).get("inputTokens")
            bus.publish("conv.usage", {"conversation_id": conv_id, "contextTokens": last, "contextWindow": window,
                                       "total": usage.get("total")})
            return
        if method == "error":
            if params.get("willRetry"):
                bus.publish("conv.status", {"conversation_id": conv_id,
                                            "text": "Connection hiccup, retrying…"})
            return
        if method == "model/rerouted":
            note = store.add_message(conv_id, "event", f"Model rerouted to {params.get('toModel') or params.get('model') or 'another model'}.",
                                     kind="notice")
            bus.publish("conv.message", {"conversation_id": conv_id, "message": note})
            return
        if method == "turn/completed":
            turn_info = params.get("turn") or {}
            self._finish_turn(conv_id, turn_info.get("status") or "completed", turn_info.get("error"))
            return

    def _on_item(self, conv_id: str, session: Session, item: dict[str, Any], completed: bool) -> None:
        item_type = item.get("type")
        if item_type in ("userMessage", "hookPrompt", "sleep", "subAgentActivity", None):
            return
        if completed:
            self.app.skills.note_item(item)
        turn = session.turn
        message_id = f"{conv_id}:{item.get('id')}"
        if item_type == "agentMessage" and not completed:
            message = self.app.store.upsert_message(message_id, conv_id, "assistant", "text", "",
                                                    {"phase": item.get("phase")}, turn.turn_id if turn else None,
                                                    status="streaming")
            self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": message})
            return
        if item_type == "reasoning" and not completed:
            return
        mapped = item_to_message(item)
        if mapped is None:
            return
        kind, role, content, data, status = mapped
        if kind == "reasoning" and not content.strip():
            return
        if kind == "image" and data.get("saved_path"):
            data["url"] = self._publish_image(data["saved_path"])
        if completed and kind == "text" and turn is not None:
            turn.streams.pop(str(item.get("id")), None)
            if data.get("phase") != "commentary":
                turn.final_text = content
        if not completed and status == "done":
            status = "running"
        message = self.app.store.upsert_message(message_id, conv_id, role, kind, content, data,
                                                turn.turn_id if turn else None, status)
        self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": message})

    def _publish_image(self, saved_path: str) -> str | None:
        src = Path(saved_path)
        if not src.is_file():
            return None
        dest = paths.uploads_dir() / f"gen_{src.name}"
        try:
            shutil.copyfile(src, dest)
        except OSError:
            return None
        return f"/uploads/{dest.name}"

    def _finish_turn(self, conv_id: str, status: str, error: dict | None, publish_error: bool = True) -> None:
        session = self.session(conv_id)
        turn = session.turn
        session.turn = None
        store = self.app.store
        if turn and turn.occurrence_id:
            occurrence = store.get_routine(turn.occurrence_id)
            if occurrence and occurrence["status"] == "started":
                store.update_routine(turn.occurrence_id, finished_at=time.time())
        conv = store.get_conversation(conv_id)
        if turn and conv and conv.get("thread_id"):
            self.app.interactions.cancel_turn(conv["thread_id"], turn.turn_id)
        # Any message still marked streaming/running belongs to a turn that is now over.
        for row in store.query("SELECT id FROM messages WHERE conversation_id=? AND status IN ('streaming','running')",
                               (conv_id,)):
            final_status = "done" if status == "completed" else "interrupted"
            partial = (turn.streams.get(row["id"].split(":", 1)[-1]) if turn else None)
            values: dict[str, Any] = {"status": final_status}
            if partial:
                values["content"] = partial
            msg = store.update_message(row["id"], **values)
            self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": msg})
        if status == "failed" and publish_error:
            text = _error_text(error)
            err = store.add_message(conv_id, "event", text, kind="error")
            self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": err})
            self.app.diagnostics.record("turn_failed", text)
        if turn and turn.diff.strip():
            files = re.findall(r"^\+\+\+ b/(.+)$", turn.diff, flags=re.MULTILINE)
            added = len(re.findall(r"^\+(?!\+\+)", turn.diff, flags=re.MULTILINE))
            removed = len(re.findall(r"^-(?!--)", turn.diff, flags=re.MULTILINE))
            diff_msg = store.add_message(conv_id, "event", f"{len(files)} file(s) changed", kind="diff",
                                         data={"diff": _tail(turn.diff, MAX_DIFF), "files": files,
                                               "added": added, "removed": removed}, turn_id=turn.turn_id)
            self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": diff_msg})
        self.app.bus.publish("conv.turn.completed", {
            "conversation_id": conv_id, "status": status, "trigger": turn.trigger if turn else "",
            "final_text": (turn.final_text if turn else "")[:600],
        })
        if conv:
            store.update_conversation(conv_id, unread=1)
            self._name_thread_once(conv)
        if turn:
            self.app.on_turn_finished(conv_id, turn, status)
        if session.followups and status != "interrupted":
            event = session.followups.pop(0)
            asyncio.create_task(self.run_event(conv_id, **event))

    def _name_thread_once(self, conv: dict[str, Any]) -> None:
        """Codex persists a thread on its first turn; name it then so it reads well in the Codex app."""
        thread_id = conv.get("thread_id")
        meta = dict(conv.get("meta") or {})
        if not thread_id or meta.get("named_thread") == thread_id:
            return
        meta["named_thread"] = thread_id
        self.app.store.update_conversation(conv["id"], meta=meta)
        title = conv.get("title") or "Weebo chat"
        asyncio.create_task(self.app.engine.set_thread_name(thread_id, f"Weebo · {title}"))

    # ------------------------------------------------------------------ startup hygiene
    def recover_after_restart(self) -> None:
        """Messages left mid-stream by a previous run can never finish; mark them interrupted."""
        rows = self.app.store.query("SELECT id, conversation_id, kind FROM messages WHERE status IN ('streaming','running','pending')")
        for row in rows:
            status = "expired" if row["kind"] in ("approval", "question") else "interrupted"
            self.app.store.update_message(row["id"], status=status)
