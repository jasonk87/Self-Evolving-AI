"""Parallel background agents. Each agent is its own Codex thread with its own turn,
so several run at the same time while the chat stays responsive. Follow-up work on a finished agent's job can
continue that agent's thread (it remembers what it did); finished threads are put away in Codex's archive."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import log, paths
from ..brain import activity, persona
from ..brain.conversation import item_to_message, sandbox_for, tools_hash
from ..brain.tools import ToolContext, ToolResult, registry
from ..codex.engine import text_input
from ..codex.rpc import EngineClosed, RpcError

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("agents")

ACTIVE = ("queued", "running")
# What a continuation takes from the agent it continues: what its Codex thread was set up with.
INHERITED = ("tools_scope", "tools_hash", "developer_instructions", "sandbox_mode", "autonomy")


CARD_EVERY = 2.0  # seconds between progress updates of an agent's chat card


@dataclass
class AgentRun:
    task_id: str
    thread_id: str | None = None
    turn_id: str | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    status: str = "running"
    final_text: str = ""
    last_message: str = ""
    error: str = ""
    progress: str = ""  # plain words for what it's doing now ("Running the tests"), never a command
    activity: str = "thinking"
    reasoning: str = ""
    files: set[str] = field(default_factory=set)
    commands: int = 0
    cancelled: bool = False
    card_synced: float = 0.0
    fresh: bool = False  # this run started its thread (rather than continuing an earlier agent's)


def _duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


class AgentManager:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.runs: dict[str, AgentRun] = {}
        self.queue: deque[str] = deque()
        self._waiters: dict[str, list[asyncio.Future]] = {}
        self._tasks: set[asyncio.Task] = set()
        self.closing = False

    # ------------------------------------------------------------------ public
    def resolve_cwd(self, cwd: str | None) -> str:
        base = self.app.settings.get("agents.default_cwd") or str(paths.workspace_dir())
        if not cwd:
            return str(Path(base).resolve())
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            path = paths.workspace_dir() / path
        path = path.resolve()
        if not path.exists():
            workspace = paths.workspace_dir().resolve()
            if workspace in path.parents:
                path.mkdir(parents=True, exist_ok=True)
            else:
                raise ValueError(f"Folder does not exist: {path}")
        if not path.is_dir():
            raise ValueError(f"Not a folder: {path}")
        return str(path)

    async def start(self, title: str, instructions: str, cwd: str | None = None, effort: str | None = None,
                    conversation_id: str | None = None, kind: str = "agent", meta: dict | None = None,
                    tools_scope: str = "agent", developer_instructions: str | None = None,
                    continue_from: str | None = None) -> dict[str, Any]:
        """Queue an agent. With ``continue_from`` (a finished agent of the same kind) it works in that agent's
        Codex thread, where it remembers the job, so ``instructions`` can be just the follow-up."""
        title = (title or "").strip()[:120] or "Background task"
        if not instructions or not instructions.strip():
            raise ValueError("Agent instructions are empty.")
        task_meta = {"effort": effort, "tools_scope": tools_scope, **(meta or {})}
        if developer_instructions:
            task_meta["developer_instructions"] = developer_instructions
        source = self._continuable(continue_from, kind) if continue_from else None
        owner = self.app.bots.for_conversation(conversation_id)
        if owner:
            if source and source.get("conversation_id") != conversation_id:
                raise ValueError("A bot can continue only its own agents.")
            task_meta["bot_id"] = owner["id"]
        if source:
            # Its thread's tools, instructions and folder are fixed: they come along, whatever else was asked.
            inherited = {k: v for k, v in (source.get("meta") or {}).items() if k in INHERITED}
            task_meta = {**task_meta, **inherited, "continues": source["id"]}
            resolved = self.resolve_cwd(source["cwd"])
        else:
            resolved = self.resolve_cwd(cwd)
            task_meta["tools_hash"] = tools_hash(task_meta.get("tools_scope") or "agent")
        task = self.app.store.create_task(title, instructions.strip(), kind=kind, cwd=resolved,
                                          conversation_id=conversation_id, meta=task_meta)
        if source:
            task = self.app.store.update_task(task["id"], thread_id=source["thread_id"]) or task
            self.app.store.add_task_event(task["id"], "created", f"Continues {source['id']} in {resolved}")
        else:
            self.app.store.add_task_event(task["id"], "created", f"Queued in {resolved}")
        self.queue.append(task["id"])
        self.app.bus.publish("task.updated", {"task": task})
        self._pump()
        self._sync_card(task["id"])
        return self.app.store.get_task(task["id"]) or task

    def _continuable(self, task_id: str, kind: str) -> dict[str, Any]:
        source = self.app.store.get_task(task_id)
        if source is None:
            raise ValueError(f"No agent {task_id}.")
        if source["kind"] != kind:
            raise ValueError(f"{task_id} isn't that kind of agent, so it can't be continued here.")
        if source["status"] in ACTIVE:
            raise ValueError(f"Agent {task_id} is still {source['status']}; send it a message instead.")
        if (source.get("meta") or {}).get("unsettled_thread"):
            raise ValueError("That agent's stop is not confirmed yet; wait for Codex or start a new agent.")
        if not source.get("thread_id") or (source.get("meta") or {}).get("ephemeral"):
            raise ValueError(f"Agent {task_id} left no thread to continue; start a new agent instead.")
        # One turn at a time per thread: a second continuation would collide with the first.
        busy = self.app.store.query_one("SELECT id FROM tasks WHERE thread_id=? AND status IN ('queued', 'running')",
                                        (source["thread_id"],))
        if busy:
            raise ValueError(f"Agent {busy['id']} is already continuing that work; send it a message instead.")
        return source

    async def wait(self, task_id: str, timeout: float | None = None) -> dict[str, Any]:
        task = self.app.store.get_task(task_id)
        if task is None:
            raise ValueError(f"No agent {task_id}.")
        if task["status"] not in ACTIVE:
            return task
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(task_id, []).append(future)
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            waiters = self._waiters.get(task_id, [])
            if future in waiters:
                waiters.remove(future)
            if not waiters:
                self._waiters.pop(task_id, None)

    async def message(self, task_id: str, text: str) -> None:
        run = self.runs.get(task_id)
        if run is None or not run.thread_id or not run.turn_id:
            raise ValueError(f"Agent {task_id} is not running.")
        await self.app.engine.steer(run.thread_id, run.turn_id, [text_input(text)])
        self.app.store.add_task_event(task_id, "steer", text)
        self.app.bus.publish("task.event", {"task_id": task_id, "kind": "steer", "content": text})

    async def stop(self, task_id: str) -> None:
        if task_id in self.queue:
            self.queue.remove(task_id)
            self._finalize(task_id, "cancelled", "", "Cancelled before it started.")
            task = self.app.store.get_task(task_id)
            if task and task.get("thread_id"):
                self.app.threads.put_away(task["thread_id"])
            return
        run = self.runs.get(task_id)
        if run is None:
            raise ValueError(f"Agent {task_id} is not running.")
        run.cancelled = True
        if run.thread_id and not run.turn_id:
            # turn/start may already be running remotely. Keep its route until
            # the acknowledgement gives us the turn id needed to interrupt it.
            return
        if run.thread_id and run.turn_id:
            try:
                await self.app.engine.interrupt(run.thread_id, run.turn_id)
                return
            except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
                logger.warning("Interrupt of %s failed: %s", task_id, exc)
        run.status = "cancelled"
        run.done.set()

    def active(self) -> list[dict[str, Any]]:
        return self.app.store.list_tasks(limit=50, statuses=ACTIVE)

    def live(self, task_id: str) -> dict[str, Any]:
        run = self.runs.get(task_id)
        if not run:
            return {}
        return {"progress": run.progress, "activity": run.activity, "files": sorted(run.files),
                "commands": run.commands}

    def describe(self, task_id: str | None = None) -> str:
        store = self.app.store
        if task_id:
            task = store.get_task(task_id)
            if not task:
                return f"No agent {task_id}."
            lines = [f"{task['id']} \"{task['title']}\" — {task['status']}"]
            if (task.get("meta") or {}).get("continues"):
                lines.append(f"Continues {task['meta']['continues']} (same session, so it remembers that work).")
            run = self.runs.get(task_id)
            if run:
                lines.append(f"Progress: {run.progress or 'working'}; commands run: {run.commands}; files touched: {len(run.files)}")
            events = store.list_task_events(task_id, limit=12)
            for event in events[-8:]:
                if event["kind"] == "command":  # what it did, in words: the user never needs the command line
                    words = activity.describe_command(event["content"])[0]
                    code = (event.get("data") or {}).get("exitCode")
                    lines.append(f"- {words}" + (f" (failed, exit {code})" if code not in (None, 0) else ""))
                else:
                    lines.append(f"- {event['kind']}: {event['content'][:200]}")
            if task.get("summary"):
                lines.append("Report:\n" + task["summary"][:3000])
            if task.get("error"):
                lines.append("Error: " + task["error"])
            return "\n".join(lines)
        tasks = store.list_tasks(limit=12)
        if not tasks:
            return "No agents yet."
        return "\n".join(f"{t['id']} [{t['status']}] {t['title']}" for t in tasks)

    def recover_after_restart(self) -> None:
        """Restore unstarted ordinary agents; running work must never be replayed."""
        for task in self.app.store.query("SELECT * FROM tasks WHERE json_extract(meta, '$.unsettled_thread') = 1"):
            self.app.store.update_task(task["id"], meta={k: v for k, v in task["meta"].items() if k != "unsettled_thread"})
            self.app.threads.later(task["thread_id"])
        known = set(self.queue) | self.runs.keys()
        tasks = self.app.store.query(
            "SELECT * FROM tasks WHERE status IN ('queued', 'running') ORDER BY created_at, rowid"
        )
        for task in tasks:
            task_id = task["id"]
            if task_id in known:
                continue
            if task["kind"] == "agent" and task["status"] == "queued":
                self.queue.append(task_id)
                known.add(task_id)
                event = self.app.store.add_task_event(task_id, "recovered", "Restored to the queue after restart.")
                self.app.bus.publish("task.event", {"task_id": task_id, **event})
            else:
                # Other kinds retain their existing recovery rules (e.g. evolution owns rebuilding).
                task = self.app.store.update_task(task_id, status="interrupted", finished_at=time.time(),
                                                  error="Weebo restarted while this agent was running.")
                if task and task.get("thread_id") and not (task.get("meta") or {}).get("ephemeral"):
                    self.app.threads.later(task["thread_id"])
                if task and task["kind"] == "agent":  # say so in its chat: its card would read "Working" forever
                    self._sync_card(task_id)
                    self._report(task)
            self.app.bus.publish("task.updated", {"task": task})

    # ------------------------------------------------------------------ internals
    def _pump(self) -> None:
        # Not while Weebo is stopping either: an agent started now would be cut off and reported interrupted
        # instead of staying queued for the next start.
        if self.closing or getattr(self.app, "_stopping", False) or self.app.engine.status != "ready":
            return
        limit = int(self.app.settings.get("agents.max_parallel") or 3)
        while self.queue and len(self.runs) < limit:
            task_id = self.queue.popleft()
            run = self.runs[task_id] = AgentRun(task_id)
            self.app.store.update_task(task_id, status="running", started_at=time.time())
            self._sync_card(task_id)
            task = asyncio.create_task(self._run(run), name=f"agent-{task_id}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run(self, run: AgentRun) -> None:
        task = self.app.store.get_task(run.task_id)
        assert task is not None
        meta = task.get("meta") or {}
        engine = self.app.engine
        settings = self.app.settings
        self.app.bus.publish("task.updated", {"task": task})
        self._event(run, "started", "Agent started.")
        self.app.bus.publish("weebo.mood", {"mood": "delegating", "task_id": run.task_id})
        try:
            model = engine.default_model(settings.get("codex.model"))
            effort = engine.clamp_effort(model, meta.get("effort") or settings.get("codex.agent_effort"))
            roots = [task["cwd"], str(paths.workspace_dir())]
            approval, sandbox_mode, policy = sandbox_for(meta.get("autonomy") or settings.get("autonomy.level"), roots)
            special = meta.get("sandbox_mode")
            if special == "read-only-auto":
                # Unattended and read-only: nothing to approve, nothing it can break.
                approval, sandbox_mode, policy = "never", "read-only", {"type": "readOnly", "networkAccess": False}
            elif special == "workspace-write-auto":
                # Unattended but confined to its own folder (used for self-evolution worktrees).
                approval, sandbox_mode = "never", "workspace-write"
                policy = {"type": "workspaceWrite", "writableRoots": [task["cwd"]], "networkAccess": True,
                          "excludeTmpdirEnvVar": False, "excludeSlashTmp": False}
            scope = meta.get("tools_scope") or "agent"
            developer = meta.get("developer_instructions") or persona.developer_instructions(self.app, "agent")
            owner = self.app.bots.get(meta.get("bot_id")) or self.app.bots.for_conversation(task.get("conversation_id"))
            if meta.get("bot_id") and owner is None:
                raise ValueError("The bot that owns this agent no longer exists.")
            specs = registry.specs(scope)
            current_hash = tools_hash(scope)
            extra = {}
            if owner:
                refusal = self.app.bots.refuse_turn(owner, "agent")
                if refusal:
                    raise ValueError(refusal)
                model = self.app.bots.model_for(owner)
                effort = self.app.bots.effort_for(owner)
                approval, sandbox_mode, policy = self.app.bots.sandbox(owner, roots)
                specs = [s for s in specs if self.app.bots.allows_tool(owner, s["name"])]
                current_hash = "bot:" + self.app.bots.tools_hash(owner)
                developer = self.app.bots.developer_instructions(owner) + "\n\n" + developer
                extra = await self.app.connectors.prepare_thread(owner, task["cwd"])
            earlier = ""
            if task.get("thread_id"):  # continuing an earlier agent's work in its own thread
                run.thread_id = await self._reopen(task, approval, sandbox_mode, developer, scope,
                                                      expected_hash=current_hash, extra=extra)
                if run.thread_id is None:
                    earlier = self._earlier_work(task)
            if run.thread_id is None:
                result = await engine.start_thread(
                    cwd=task["cwd"], approvalPolicy=approval, sandbox=sandbox_mode, developerInstructions=developer,
                    dynamicTools=specs, serviceName="weebo-agent",
                    ephemeral=bool(meta.get("ephemeral")), model=model, **extra,
                )
                run.thread_id, run.fresh = result["thread"]["id"], True
                if task.get("thread_id"):  # took over from a thread that couldn't be reopened: it has today's tools
                    self.app.store.update_task(run.task_id, thread_id=run.thread_id,
                                               meta={**meta, "tools_hash": current_hash})
                else:
                    self.app.store.update_task(run.task_id, thread_id=run.thread_id,
                                               **({"meta": {**meta, "tools_hash": current_hash}} if owner else {}))
            if run.cancelled:  # stopped while the thread was being created: never start the turn
                run.status = "cancelled"
                return
            engine.route(run.thread_id,
                         listener=lambda m, p, r=run: self._on_event(r, m, p),
                         request_handler=lambda m, p, r=run: self._on_request(r, m, p))
            context = f"Agent task id: {run.task_id}\nLaunched by Weebo at {time.strftime('%Y-%m-%d %H:%M')}"
            if earlier:
                context += "\n\n" + earlier
            overrides: dict[str, Any] = {"model": model, "effort": effort, "approvalPolicy": approval,
                                         "sandboxPolicy": policy}
            if meta.get("output_schema") and engine.features.output_schema:
                overrides["outputSchema"] = meta["output_schema"]
            if owner:
                owner = self.app.bots.require(owner["id"])
                refusal = self.app.bots.refuse_turn(owner, "agent")
                if refusal:
                    raise ValueError(refusal)
                self.app.bots.count_turn(owner)
                self.app.count_background_turn("bot-agent")
            turn = await engine.start_turn(run.thread_id, [text_input(task["prompt"])], context=context, **overrides)
            run.turn_id = run.turn_id or turn["id"]
            if run.cancelled and not run.done.is_set():
                await self.stop(run.task_id)
            limit = float(settings.get("agents.max_minutes") or 60) * 60
            try:
                await asyncio.wait_for(run.done.wait(), limit)
            except asyncio.TimeoutError:
                run.error = f"Stopped after the {int(limit // 60)} minute limit."
                run.cancelled = True
                try:
                    await engine.interrupt(run.thread_id, run.turn_id)
                    await asyncio.wait_for(run.done.wait(), 30)
                except (RpcError, EngineClosed, asyncio.TimeoutError):
                    pass
                run.status = "failed"
        except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
            run.status, run.error = "failed", str(exc) or type(exc).__name__
        except Exception as exc:
            logger.exception("Agent %s crashed", run.task_id)
            run.status, run.error = "failed", str(exc) or type(exc).__name__
            self.app.diagnostics.record("agent_crash", f"Agent runner crashed: {exc!r}")
        finally:
            if run.thread_id and run.turn_id and not run.done.is_set() and not self.closing:
                final_status = run.status
                try:
                    async with asyncio.timeout(3):
                        await engine.interrupt(run.thread_id, run.turn_id)
                        await run.done.wait()
                except (RpcError, EngineClosed, asyncio.TimeoutError):
                    pass
                if final_status == "failed":
                    run.status = final_status
            if run.thread_id:
                engine.unroute(run.thread_id)
            self.runs.pop(run.task_id, None)
            if not self.closing:  # shutting down: left "running", the next start reports it interrupted in its chat
                status = "cancelled" if run.cancelled and run.status != "failed" else run.status
                self._finalize(run.task_id, status, run.final_text or run.last_message, run.error)
                if run.thread_id:
                    self._put_away(run, task)
                if task.get("thread_id") and task["thread_id"] != run.thread_id:
                    # The thread it was to continue (perhaps un-archived for it) couldn't be used: put it back.
                    self.app.threads.put_away(task["thread_id"])
                self._pump()

    async def _reopen(self, task: dict[str, Any], approval: str, sandbox_mode: str, developer: str,
                      scope: str, expected_hash: str | None = None, extra: dict[str, Any] | None = None) -> str | None:
        """The continued agent's thread, ready for another turn; None when it can't come back (its tools changed
        in an upgrade, or Codex no longer has it), so a fresh thread takes over."""
        thread_id = task["thread_id"]
        if (task.get("meta") or {}).get("tools_hash") != (expected_hash or tools_hash(scope)):
            # A thread keeps the tools it started with: changed or unknown (an agent from before Weebo recorded
            # them) means a new thread with today's tools, told what the earlier agent did.
            return None
        engine = self.app.engine
        await self.app.threads.settle(thread_id)  # it may be being put away right now
        if thread_id in engine.loaded_threads:
            return thread_id
        await self.app.threads.take_back(thread_id)
        try:
            await engine.resume_thread(thread_id, cwd=task["cwd"], approvalPolicy=approval, sandbox=sandbox_mode,
                                       developerInstructions=developer, **(extra or {}))
        except (RpcError, asyncio.TimeoutError) as exc:
            logger.info("Could not reopen thread %s for %s (%s); starting a fresh one", thread_id, task["id"], exc)
            return None
        return thread_id

    def _earlier_work(self, task: dict[str, Any]) -> str:
        """What a fresh thread needs to know when the thread it was meant to continue couldn't be reopened."""
        source = self.app.store.get_task((task.get("meta") or {}).get("continues") or "")
        if not source:
            return ""
        return (f"This continues earlier work by agent {source['id']} \"{source['title']}\", whose own session "
                f"couldn't be reopened. For background only (the instructions are in the message), it was asked:\n"
                f"{source['prompt'][:4000]}\n\nIt reported:\n{(source.get('summary') or source.get('error') or '')[:4000]}")

    def _put_away(self, run: AgentRun, task: dict[str, Any]) -> None:
        """Done with this thread for now: out of the Codex app's thread list (follow-up work brings it back)."""
        if run.turn_id and not run.done.is_set():
            # Never archive or continue a remote turn whose stop wasn't confirmed. Keep cleanup durable and
            # watch for its terminal notification, with expired tools/approvals detached.
            current = self.app.store.get_task(run.task_id)
            self.app.store.update_task(run.task_id, meta={**current["meta"], "unsettled_thread": True})
            self.app.threads.later(run.thread_id)

            def finished(method, params):
                if method not in ("turn/completed", "engine/closed"):
                    return
                run.done.set()
                saved = self.app.store.get_task(run.task_id)
                if saved:
                    self.app.store.update_task(run.task_id,
                                               meta={k: v for k, v in saved["meta"].items() if k != "unsettled_thread"})
                self.app.engine.unroute(run.thread_id)
                if method == "turn/completed":
                    self._put_away(run, task)

            self.app.engine.route(run.thread_id, listener=finished)
            return
        if (task.get("meta") or {}).get("ephemeral") or (run.fresh and not run.turn_id):
            # Nothing on disk: just let Codex unload it (once its turn is over, if one started).
            self.app.engine.release_thread(run.thread_id, finished=run.done.is_set() or not run.turn_id)
            return
        # A thread exists on disk once a turn ran: label it (one this agent continued keeps its first name).
        name = f"Weebo agent · {task['title']}" if run.turn_id and run.fresh else ""
        self.app.threads.put_away(run.thread_id, name)

    async def shutdown(self) -> None:
        """Weebo is stopping: end every agent run before the store they write to closes."""
        self.closing = True
        runs = [task for task in self._tasks if not task.done()]
        for task in runs:
            task.cancel()
        if runs:
            await asyncio.gather(*runs, return_exceptions=True)

    def _finalize(self, task_id: str, status: str, summary: str, error: str) -> None:
        store = self.app.store
        task = store.update_task(task_id, status=status, finished_at=time.time(), summary=summary or "", error=error or "")
        if task is None:
            return
        store.add_task_event(task_id, "finished", f"{status}: {error}" if error else status)
        self.app.bus.publish("task.updated", {"task": task})
        self.app.interactions.cancel_task(task_id)  # nobody can answer for an agent that has stopped
        self._sync_card(task_id)
        for future in self._waiters.pop(task_id, []):
            if not future.done():
                future.set_result(task)
        if task["kind"] == "agent":
            self._report(task)
        if status == "failed":
            self.app.diagnostics.record("agent_failed", f"Agent '{task['title']}' failed: {error or 'no report'}")

    def _followup_budget_ok(self) -> bool:
        """A spoken follow-up is an extra Codex turn: it respects the plan ceiling and the daily background cap.
        Otherwise the report is still posted, just without Weebo narrating it."""
        settings = self.app.settings
        if self.app.engine.usage_percent() >= float(settings.get("autonomy.usage_ceiling_percent")):
            return False
        return self.app.background_turns_today() < int(settings.get("autonomy.max_background_turns_per_day"))

    def _report(self, task: dict[str, Any]) -> None:
        took = _duration((task.get("finished_at") or time.time()) - (task.get("started_at") or task["created_at"]))
        verb = {"completed": "finished", "failed": "failed", "cancelled": "was stopped", "interrupted": "was interrupted"}.get(task["status"], task["status"])
        title = f"Agent “{task['title']}” {verb} ({took})"
        conv_id = task.get("conversation_id")
        in_chat = bool(conv_id and self.app.store.get_conversation(conv_id))
        if not in_chat or self.app.clients == 0:  # an open chat shows it already; otherwise reach the phone/desktop
            self.app.notify("agent", title, (task.get("summary") or task.get("error") or "")[:600],
                            {"task_id": task["id"], "conversation_id": conv_id if in_chat else None})
        if not in_chat:
            return
        data = {"task_id": task["id"], "status": task["status"], "summary": task.get("summary", "")[:8000],
                "error": task.get("error", ""), "cwd": task.get("cwd"), "took": took}
        report = (f"Background agent report [{task['id']}] \"{task['title']}\" — {task['status']} after {took}.\n"
                  f"Working directory: {task.get('cwd')}\n"
                  f"{(task.get('summary') or task.get('error') or 'No report.')[:6000]}")
        # What reaches a turn the user starts is only that it finished, never the agent's own words: text it wrote
        # (or read on a web page) must not ride along with the user's authority. agent_status fetches the report.
        pointer = (f"Background agent {task['id']} \"{task['title']}\" {verb} ({took}). Its report is shown in the "
                   f"chat; call agent_status {task['id']} if you need what it says.")
        conversations = self.app.conversations
        # The result goes in the chat now, whatever happens to Weebo's spoken follow-up (it may wait behind a busy
        # turn, and a Stop or a restart drops what waits).
        note = self.app.store.add_message(conv_id, "event", title, kind="agent_report", data=data)
        self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": note})
        if (self.app.settings.get("agents.auto_followup") and task["status"] in ("completed", "failed")
                and self._followup_budget_ok()):
            self.app.count_background_turn("agent_report")
            prompt = (f"[Event: one of your background agents just finished]\n{report}\n\n"
                      "Tell the user what came back in a few friendly sentences: the outcome, where to find it, "
                      "and the obvious next step if there is one. Don't repeat the whole report.")
            self.app._start_job(conversations.run_event(conv_id, prompt, "", trigger="agent_report",
                                                       fallback=pointer), "agent-report")
        else:
            conversations.add_context(conv_id, pointer)

    # ------------------------------------------------------------------ codex → agent
    async def _on_request(self, run: AgentRun, method: str, params: dict[str, Any]) -> Any:
        task = self.app.store.get_task(run.task_id) or {}
        owner_id = (task.get("meta") or {}).get("bot_id")
        owner = self.app.bots.get(owner_id) if owner_id else self.app.bots.for_conversation(task.get("conversation_id"))
        inactive_owner = (owner_id and owner is None) or (owner and owner["status"] != "active")
        if method == "item/tool/call":
            name = params.get("tool", "")
            if inactive_owner or (owner and not self.app.bots.allows_tool(owner, name)):
                return ToolResult("That tool is outside the owning bot's current scope.", success=False).to_codex()
            ctx = ToolContext(self.app, params.get("threadId", ""), params.get("turnId", ""),
                              conversation_id=task.get("conversation_id"), task_id=run.task_id, trigger="agent",
                              bot_id=owner["id"] if owner else None)
            result = await registry.call(ctx, params.get("tool", ""), params.get("arguments") or {})
            self._event(run, "tool", f"{params.get('tool')} → {'ok' if result.success else 'failed'}")
            return result.to_codex()
        if method == "item/tool/requestUserInput":
            # Agents run unattended: answer with no input so they proceed on assumptions.
            return {"answers": {}}
        if inactive_owner:
            return self.app.interactions.respond(method, params, "decline")
        # The request shows up as a card in the chat that launched the agent, so it's answered where the user is.
        conv_id = task.get("conversation_id")
        if conv_id and not self.app.store.get_conversation(conv_id):
            conv_id = None
        return await self.app.interactions.ask(method, params, conversation_id=conv_id, task_id=run.task_id,
                                               agent=task.get("title") or "")

    def _event(self, run: AgentRun, kind: str, content: str, data: dict | None = None) -> None:
        event = self.app.store.add_task_event(run.task_id, kind, content[:4000], data)
        self.app.bus.publish("task.event", {"task_id": run.task_id, **event})

    def _set_progress(self, run: AgentRun, text: str, kind: str) -> None:
        run.progress, run.activity = text, kind
        self.app.bus.publish("task.progress", {"task_id": run.task_id, "progress": text, "activity": kind})
        if time.monotonic() - run.card_synced >= CARD_EVERY:
            self._sync_card(run.task_id)

    def _sync_card(self, task_id: str) -> None:
        """The agent's card in the chat that launched it: what it's doing now (in plain words), then how it went.
        One card per agent, updated in place (id "agent:<task id>")."""
        store = self.app.store
        task = store.get_task(task_id)
        if not task or task["kind"] != "agent" or not task.get("conversation_id"):
            return
        if not store.get_conversation(task["conversation_id"]):
            return
        run = self.runs.get(task_id)
        if run:
            run.card_synced = time.monotonic()
        data = {"task_id": task_id, "title": task["title"], "status": task["status"],
                "doing": run.progress if run and task["status"] == "running" else "",
                "activity": run.activity if run else "", "created_at": task["created_at"],
                "started_at": task.get("started_at"), "finished_at": task.get("finished_at"),
                "summary": (task.get("summary") or "")[:600], "error": (task.get("error") or "")[:300]}
        message = store.upsert_message(f"agent:{task_id}", task["conversation_id"], "event", "agent_card",
                                       task["title"], data=data)
        self.app.bus.publish("conv.message", {"conversation_id": task["conversation_id"], "message": message})

    def _on_event(self, run: AgentRun, method: str, params: dict[str, Any]) -> None:
        if method == "turn/started":
            run.turn_id = (params.get("turn") or {}).get("id") or run.turn_id
            return
        if method == "engine/closed":
            run.status, run.error = "failed", "The Codex engine restarted mid-task."
            run.done.set()
            return
        if method == "item/reasoning/summaryTextDelta":
            run.reasoning = (run.reasoning + params.get("delta", ""))[-2000:]
            heading = activity.reasoning_heading(run.reasoning)
            if heading and heading != run.progress:
                self._set_progress(run, heading, "thinking")
            return
        if method == "item/reasoning/summaryPartAdded":
            run.reasoning = ""
            return
        if method == "turn/plan/updated":
            steps = [f"[{'x' if s.get('status') == 'completed' else ' '}] {s.get('step')}" for s in params.get("plan") or []]
            self._event(run, "plan", "\n".join(steps), {"plan": params.get("plan")})
            return
        if method == "item/started":
            mapped = item_to_message(params.get("item") or {})
            if mapped and mapped[0] in activity.WORK_KINDS and mapped[0] != "reasoning":
                self._set_progress(run, mapped[3]["doing"], mapped[3]["activity"])
            return
        if method == "item/completed":
            item = params.get("item") or {}
            self.app.skills.note_item(item)
            mapped = item_to_message(item)
            if mapped is None:
                return
            kind, _role, content, data, status = mapped
            if kind == "text":
                run.last_message = content
                if data.get("phase") != "commentary":
                    run.final_text = content
                # Commentary/final answers may contain commands or code fences. They belong in the report,
                # never in the short live-status line.
                self._set_progress(run, "Reporting back" if data.get("phase") != "commentary" else "Working on it",
                                   "thinking")
                self._event(run, "message", content, {"phase": data.get("phase")})
            elif kind == "command":
                run.commands += 1
                exit_code = data.get("exitCode")
                self._event(run, "command", content, {"exitCode": exit_code, "status": status,
                                                       "output": (data.get("output") or "")[-2000:]})
            elif kind == "file_change":
                for change in data.get("changes", []):
                    if change.get("path"):
                        run.files.add(change["path"])
                self._event(run, "file_change", ", ".join(c.get("path", "") for c in data.get("changes", [])),
                            {"status": status})
            elif kind in ("web_search", "tool", "subagent"):
                self._event(run, kind, content, {"status": status})
            self.app.bus.publish("task.progress", {"task_id": run.task_id, "progress": run.progress,
                                                   "files": len(run.files), "commands": run.commands})
            return
        if method == "turn/completed":
            turn = params.get("turn") or {}
            status = turn.get("status") or "completed"
            run.status = {"completed": "completed", "interrupted": "cancelled" if run.cancelled else "interrupted"}.get(status, "failed")
            if status == "failed":
                error = turn.get("error") or {}
                run.error = run.error or str(error.get("message") or "The agent's turn failed.")
            run.done.set()
            return
