"""Parallel background agents. Each agent is its own Codex thread with its own turn,
so several run at the same time while the chat stays responsive."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import log, paths
from ..brain import persona
from ..brain.conversation import item_to_message, sandbox_for
from ..brain.tools import ToolContext, registry
from ..codex.engine import text_input
from ..codex.rpc import EngineClosed, RpcError

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("agents")

ACTIVE = ("queued", "running")


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
    progress: str = ""
    files: set[str] = field(default_factory=set)
    commands: int = 0
    cancelled: bool = False


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
                    tools_scope: str = "agent", developer_instructions: str | None = None) -> dict[str, Any]:
        title = (title or "").strip()[:120] or "Background task"
        if not instructions or not instructions.strip():
            raise ValueError("Agent instructions are empty.")
        resolved = self.resolve_cwd(cwd)
        task_meta = {"effort": effort, "tools_scope": tools_scope, **(meta or {})}
        if developer_instructions:
            task_meta["developer_instructions"] = developer_instructions
        task = self.app.store.create_task(title, instructions.strip(), kind=kind, cwd=resolved,
                                          conversation_id=conversation_id, meta=task_meta)
        self.app.store.add_task_event(task["id"], "created", f"Queued in {resolved}")
        self.queue.append(task["id"])
        self.app.bus.publish("task.updated", {"task": task})
        self._pump()
        return self.app.store.get_task(task["id"]) or task

    async def wait(self, task_id: str, timeout: float | None = None) -> dict[str, Any]:
        task = self.app.store.get_task(task_id)
        if task and task["status"] not in ACTIVE:
            return task
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(task_id, []).append(future)
        return await asyncio.wait_for(future, timeout)

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
            return
        run = self.runs.get(task_id)
        if run is None:
            raise ValueError(f"Agent {task_id} is not running.")
        run.cancelled = True
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
        return {"progress": run.progress, "files": sorted(run.files), "commands": run.commands}

    def describe(self, task_id: str | None = None) -> str:
        store = self.app.store
        if task_id:
            task = store.get_task(task_id)
            if not task:
                return f"No agent {task_id}."
            lines = [f"{task['id']} \"{task['title']}\" — {task['status']}"]
            run = self.runs.get(task_id)
            if run:
                lines.append(f"Progress: {run.progress or 'working'}; commands run: {run.commands}; files touched: {len(run.files)}")
            events = store.list_task_events(task_id, limit=12)
            for event in events[-8:]:
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
            self.app.bus.publish("task.updated", {"task": task})

    # ------------------------------------------------------------------ internals
    def _pump(self) -> None:
        if self.app.engine.status != "ready":
            return
        limit = int(self.app.settings.get("agents.max_parallel") or 3)
        while self.queue and len(self.runs) < limit:
            task_id = self.queue.popleft()
            run = self.runs[task_id] = AgentRun(task_id)
            self.app.store.update_task(task_id, status="running", started_at=time.time())
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
            result = await engine.start_thread(
                cwd=task["cwd"], approvalPolicy=approval, sandbox=sandbox_mode,
                developerInstructions=meta.get("developer_instructions") or persona.developer_instructions(self.app, "agent"),
                dynamicTools=registry.specs(scope), serviceName="weebo-agent", ephemeral=False, model=model,
            )
            run.thread_id = result["thread"]["id"]
            self.app.store.update_task(run.task_id, thread_id=run.thread_id)
            if run.cancelled:  # stopped while the thread was being created: never start the turn
                run.status = "cancelled"
                return
            engine.route(run.thread_id,
                         listener=lambda m, p, r=run: self._on_event(r, m, p),
                         request_handler=lambda m, p, r=run: self._on_request(r, m, p))
            context = f"Agent task id: {run.task_id}\nLaunched by Weebo at {time.strftime('%Y-%m-%d %H:%M')}"
            overrides: dict[str, Any] = {"model": model, "effort": effort, "approvalPolicy": approval,
                                         "sandboxPolicy": policy}
            if meta.get("output_schema") and engine.features.output_schema:
                overrides["outputSchema"] = meta["output_schema"]
            turn = await engine.start_turn(run.thread_id, [text_input(task["prompt"])], context=context, **overrides)
            run.turn_id = run.turn_id or turn["id"]
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
            if run.thread_id:
                engine.unroute(run.thread_id)
                if run.turn_id:  # the thread exists on disk once a turn ran; label it for the Codex app
                    asyncio.create_task(engine.set_thread_name(run.thread_id, f"Weebo agent · {task['title']}"))
            self.runs.pop(run.task_id, None)
            status = "cancelled" if run.cancelled and run.status != "failed" else run.status
            self._finalize(run.task_id, status, run.final_text or run.last_message, run.error)
            self._pump()

    def _finalize(self, task_id: str, status: str, summary: str, error: str) -> None:
        store = self.app.store
        task = store.update_task(task_id, status=status, finished_at=time.time(), summary=summary or "", error=error or "")
        if task is None:
            return
        store.add_task_event(task_id, "finished", f"{status}: {error}" if error else status)
        self.app.bus.publish("task.updated", {"task": task})
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
        self.app.notify("agent", title, (task.get("summary") or task.get("error") or "")[:600], {"task_id": task["id"]})
        conv_id = task.get("conversation_id")
        if not conv_id or not self.app.store.get_conversation(conv_id):
            return
        data = {"task_id": task["id"], "status": task["status"], "summary": task.get("summary", "")[:8000],
                "error": task.get("error", ""), "cwd": task.get("cwd"), "took": took}
        report = (f"Background agent report [{task['id']}] \"{task['title']}\" — {task['status']} after {took}.\n"
                  f"Working directory: {task.get('cwd')}\n"
                  f"{(task.get('summary') or task.get('error') or 'No report.')[:6000]}")
        conversations = self.app.conversations
        if (self.app.settings.get("agents.auto_followup") and task["status"] in ("completed", "failed")
                and self._followup_budget_ok()):
            self.app.count_background_turn("agent_report")
            prompt = (f"[Event: one of your background agents just finished]\n{report}\n\n"
                      "Tell the user what came back in a few friendly sentences: the outcome, where to find it, "
                      "and the obvious next step if there is one. Don't repeat the whole report.")
            asyncio.create_task(conversations.run_event(conv_id, prompt, title, trigger="agent_report",
                                                        notice_kind="agent_report", notice_data=data))
        else:
            note = self.app.store.add_message(conv_id, "event", title, kind="agent_report", data=data)
            self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": note})
            conversations.add_context(conv_id, report)

    # ------------------------------------------------------------------ codex → agent
    async def _on_request(self, run: AgentRun, method: str, params: dict[str, Any]) -> Any:
        task = self.app.store.get_task(run.task_id) or {}
        if method == "item/tool/call":
            ctx = ToolContext(self.app, params.get("threadId", ""), params.get("turnId", ""),
                              conversation_id=task.get("conversation_id"), task_id=run.task_id, trigger="agent")
            result = await registry.call(ctx, params.get("tool", ""), params.get("arguments") or {})
            self._event(run, "tool", f"{params.get('tool')} → {'ok' if result.success else 'failed'}")
            return result.to_codex()
        if method == "item/tool/requestUserInput":
            # Agents run unattended: answer with no input so they proceed on assumptions.
            return {"answers": {}}
        return await self.app.interactions.ask(method, params, task_id=run.task_id)

    def _event(self, run: AgentRun, kind: str, content: str, data: dict | None = None) -> None:
        event = self.app.store.add_task_event(run.task_id, kind, content[:4000], data)
        self.app.bus.publish("task.event", {"task_id": run.task_id, **event})

    def _on_event(self, run: AgentRun, method: str, params: dict[str, Any]) -> None:
        if method == "turn/started":
            run.turn_id = (params.get("turn") or {}).get("id") or run.turn_id
            return
        if method == "engine/closed":
            run.status, run.error = "failed", "The Codex engine restarted mid-task."
            run.done.set()
            return
        if method == "item/reasoning/summaryTextDelta":
            run.progress = (run.progress + params.get("delta", ""))[-240:]
            return
        if method == "item/reasoning/summaryPartAdded":
            run.progress = ""
            return
        if method == "turn/plan/updated":
            steps = [f"[{'x' if s.get('status') == 'completed' else ' '}] {s.get('step')}" for s in params.get("plan") or []]
            self._event(run, "plan", "\n".join(steps), {"plan": params.get("plan")})
            return
        if method == "item/started":
            item = params.get("item") or {}
            if item.get("type") == "commandExecution":
                run.progress = f"Running: {item.get('command', '')[:160]}"
                self.app.bus.publish("task.progress", {"task_id": run.task_id, "progress": run.progress})
            return
        if method == "item/completed":
            item = params.get("item") or {}
            mapped = item_to_message(item)
            if mapped is None:
                return
            kind, _role, content, data, status = mapped
            if kind == "text":
                run.last_message = content
                if data.get("phase") != "commentary":
                    run.final_text = content
                run.progress = content[:240]
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
