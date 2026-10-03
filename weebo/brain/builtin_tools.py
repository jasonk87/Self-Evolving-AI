"""Weebo's built-in tools. Codex calls these through ``item/tool/call``."""

from __future__ import annotations

import asyncio
import base64
import io
import webbrowser
from datetime import datetime
from typing import Any

from ..memory.memory import KINDS, MemoryError_
from ..timeparse import TimeParseError, describe_recurrence
from .tools import ToolContext, ToolError, ToolResult, registry

ALL = ("chat", "agent", "background")


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%a %b %d %I:%M %p")


# ---------------------------------------------------------------- memory
@registry.register(
    "remember",
    "Save one durable memory (a concise statement) about the user, their world, or how they like Weebo to work. "
    "Near-duplicates are merged automatically. Never store secrets.",
    {
        "properties": {
            "text": {"type": "string", "description": "One self-contained statement, e.g. 'User prefers dark roast coffee.'"},
            "kind": {"type": "string", "enum": list(KINDS), "description": "Category of the memory."},
            "importance": {"type": "integer", "minimum": 1, "maximum": 5, "description": "5 = core identity/preference, 1 = trivia."},
        },
        "required": ["text"],
    },
    scopes=ALL,
)
async def remember(ctx: ToolContext, text: str, kind: str = "fact", importance: int = 3) -> ToolResult:
    source = f"task:{ctx.task_id}" if ctx.task_id else f"conversation:{ctx.conversation_id}"
    try:
        memory, action = ctx.app.memory.remember(text, kind, importance, source=source)
    except MemoryError_ as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Memory {action}: [{memory['id']}] {memory['text']}")


@registry.register(
    "recall",
    "Search Weebo's long-term memory. Returns matching memories with ids.",
    {
        "properties": {
            "query": {"type": "string", "description": "What to look for."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25},
        },
        "required": ["query"],
    },
    scopes=("chat", "agent"),
)
async def recall(ctx: ToolContext, query: str, limit: int = 8) -> ToolResult:
    results = ctx.app.memory.recall(query, limit=limit)
    if not results:
        return ToolResult("No matching memories.")
    lines = [f"[{m['id']}] ({m['kind']}, importance {m['importance']}) {m['text']}" for m in results]
    return ToolResult("\n".join(lines))


@registry.register(
    "forget",
    "Delete a memory by id (use recall first to find the id). Use when the user asks you to forget something or a memory is wrong.",
    {"properties": {"memory_id": {"type": "string"}}, "required": ["memory_id"]},
)
async def forget(ctx: ToolContext, memory_id: str) -> ToolResult:
    if not ctx.app.memory.forget(memory_id):
        raise ToolError(f"No memory with id {memory_id}.")
    return ToolResult(f"Forgot {memory_id}.")


@registry.register(
    "update_memory",
    "Correct or re-rank an existing memory.",
    {
        "properties": {
            "memory_id": {"type": "string"},
            "text": {"type": "string"},
            "importance": {"type": "integer", "minimum": 1, "maximum": 5},
            "pinned": {"type": "boolean", "description": "Pinned memories are always in context."},
        },
        "required": ["memory_id"],
    },
)
async def update_memory(ctx: ToolContext, memory_id: str, text: str | None = None, importance: int | None = None,
                        pinned: bool | None = None) -> ToolResult:
    try:
        memory = ctx.app.memory.update(memory_id, text=text, importance=importance,
                                       pinned=None if pinned is None else int(pinned))
    except MemoryError_ as exc:
        raise ToolError(str(exc)) from exc
    if memory is None:
        raise ToolError(f"No memory with id {memory_id}.")
    return ToolResult(f"Updated [{memory['id']}] {memory['text']}")


# ---------------------------------------------------------------- reminders & routines
@registry.register(
    "set_reminder",
    "Schedule a reminder or an autonomous routine. action 'notify' pings the user with the text. action 'prompt' makes "
    "Weebo itself run the text as an instruction at that time (for routines like 'check the weather and tell me if I "
    "need an umbrella'). Times are the user's local time.",
    {
        "properties": {
            "text": {"type": "string", "description": "Reminder text, or the instruction to run for action=prompt."},
            "when": {"type": "string", "description": "e.g. 'in 20 minutes', 'tomorrow 9am', 'friday 6pm', or ISO '2026-10-04T15:30'."},
            "recurrence": {"type": "string", "description": "Optional: hourly, daily, weekdays, weekly, monthly, or 'every 30 minutes'."},
            "action": {"type": "string", "enum": ["notify", "prompt"]},
        },
        "required": ["text", "when"],
    },
)
async def set_reminder(ctx: ToolContext, text: str, when: str, recurrence: str = "", action: str = "notify") -> ToolResult:
    try:
        reminder = ctx.app.scheduler.add(text, when, recurrence, action, ctx.conversation_id)
    except TimeParseError as exc:
        raise ToolError(str(exc)) from exc
    repeat = f", repeating {describe_recurrence(reminder['recurrence'])}" if reminder["recurrence"] else ""
    kind = "Routine" if action == "prompt" else "Reminder"
    return ToolResult(f"{kind} {reminder['id']} set for {_fmt_time(reminder['due_at'])}{repeat}.")


@registry.register("list_reminders", "List pending reminders and routines.")
async def list_reminders(ctx: ToolContext) -> ToolResult:
    reminders = ctx.app.store.list_reminders()
    if not reminders:
        return ToolResult("No pending reminders.")
    lines = []
    for r in reminders:
        repeat = f" ({describe_recurrence(r['recurrence'])})" if r["recurrence"] else ""
        lines.append(f"{r['id']} {_fmt_time(r['due_at'])}{repeat} [{r['action']}]: {r['text']}")
    return ToolResult("\n".join(lines))


@registry.register(
    "cancel_reminder", "Cancel a reminder or routine by id.",
    {"properties": {"reminder_id": {"type": "string"}}, "required": ["reminder_id"]},
)
async def cancel_reminder(ctx: ToolContext, reminder_id: str) -> ToolResult:
    if not ctx.app.scheduler.cancel(reminder_id):
        raise ToolError(f"No pending reminder {reminder_id}.")
    return ToolResult(f"Cancelled {reminder_id}.")


# ---------------------------------------------------------------- parallel agents
@registry.register(
    "start_agent",
    "Launch a background Codex agent for a long or multi-step job. Returns immediately with a task id; the agent works "
    "in parallel and its report is delivered to you automatically when it finishes. Give complete, self-contained "
    "instructions: the agent does not see this conversation.",
    {
        "properties": {
            "title": {"type": "string", "description": "Short name shown to the user, e.g. 'Build snake game'."},
            "instructions": {"type": "string", "description": "Full task description with goals, constraints and where to work."},
            "cwd": {"type": "string", "description": "Absolute working directory. Defaults to Weebo's workspace."},
            "effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh"]},
        },
        "required": ["title", "instructions"],
    },
)
async def start_agent(ctx: ToolContext, title: str, instructions: str, cwd: str = "", effort: str = "") -> ToolResult:
    try:
        task = await ctx.app.agents.start(title, instructions, cwd=cwd or None, effort=effort or None,
                                          conversation_id=ctx.conversation_id)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    position = "starting now" if task["status"] == "running" else "queued (other agents are busy)"
    return ToolResult(f"Agent {task['id']} \"{task['title']}\" is {position} in {task['cwd']}.")


@registry.register(
    "agent_status",
    "Check background agents. With task_id, returns that agent's latest progress; otherwise lists recent agents.",
    {"properties": {"task_id": {"type": "string"}}},
)
async def agent_status(ctx: ToolContext, task_id: str = "") -> ToolResult:
    return ToolResult(ctx.app.agents.describe(task_id or None))


@registry.register(
    "message_agent",
    "Send extra instructions to a running background agent (it adjusts mid-task).",
    {"properties": {"task_id": {"type": "string"}, "message": {"type": "string"}}, "required": ["task_id", "message"]},
)
async def message_agent(ctx: ToolContext, task_id: str, message: str) -> ToolResult:
    try:
        await ctx.app.agents.message(task_id, message)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Sent to {task_id}.")


@registry.register(
    "stop_agent", "Stop a running background agent.",
    {"properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
)
async def stop_agent(ctx: ToolContext, task_id: str) -> ToolResult:
    try:
        await ctx.app.agents.stop(task_id)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Stopping {task_id}.")


# ---------------------------------------------------------------- self-evolution
@registry.register(
    "propose_improvement",
    "Propose a change to Weebo's own code, UI or abilities (self-evolution). Your own ideas first go to the Council, "
    "which decides whether they are worth building. Approved work is built by a Codex agent in an isolated git "
    "worktree, tested, code-reviewed, and merged only with the user's approval (per the evolution settings).",
    {
        "properties": {
            "title": {"type": "string", "description": "Short imperative title, e.g. 'Add weather tool'."},
            "description": {"type": "string", "description": "Exactly what should change and how to verify it works."},
            "rationale": {"type": "string", "description": "Why: the user request or evidence (errors, limits) behind it."},
            "requested_by_user": {"type": "boolean", "description": (
                "True only if the user explicitly asked for this change. False when it is your own idea: "
                "your own ideas are vetted by the Council before anything is built.")},
        },
        "required": ["title", "description"],
    },
    scopes=ALL,
)
async def propose_improvement(ctx: ToolContext, title: str, description: str, rationale: str = "",
                              requested_by_user: bool = False) -> ToolResult:
    # Only a live user turn can carry a user request; routines, briefs, reports and agents are Weebo's own initiative.
    if ctx.task_id:
        source = "agent"
    elif ctx.trigger != "user":
        source = {"routine": "routine", "brief": "brief"}.get(ctx.trigger, "self")
    else:
        source = "user" if requested_by_user else "conversation"
    proposal = await ctx.app.evolution.propose(title, description, rationale, source=source,
                                               conversation_id=ctx.conversation_id)
    return ToolResult(f"Proposal {proposal['id']} \"{proposal['title']}\": {_evolution_state(ctx, proposal)} "
                      "Describe exactly this state to the user; call evolution_status before claiming any progress.")


def _evolution_state(ctx: ToolContext, proposal: dict[str, Any]) -> str:
    """Plain-language, truthful description of where a proposal actually is right now."""
    status = proposal["status"]
    if status == "queued":
        ahead = ctx.app.evolution.queue_position(proposal["id"])
        return ("queued; a Codex build agent starts on it next." if not ahead
                else f"queued behind {ahead} other build(s).")
    meta = proposal.get("meta") or {}
    if status in ("building", "checking"):
        stage = meta.get("stage") or status
        task_id = proposal.get("task_id")
        progress = ctx.app.agents.live(task_id).get("progress") if task_id and stage == "building" else ""
        phase = {"building": "being built by a Codex agent", "testing": "running its tests",
                 "reviewing": "in Codex code review"}.get(stage, "being verified")
        return f"{phase}, round {meta.get('round') or 1} of 3{': ' + progress if progress else ''}."
    council_reason = (meta.get("council") or {}).get("reason", "")
    return {
        "vetting": "with the Council, which is deciding whether it is worth building. Nothing is being built yet.",
        "declined": f"declined by the Council ({council_reason}) Nothing will be built unless the user presses Build anyway.",
        "proposed": "saved as an idea; nothing is being built. The user can press Build in the Evolution panel.",
        "ready": "built and verified; waiting for the user to review and merge.",
        "merging": "merging now.",
        "merged": "merged into Weebo.",
        "failed": "the last build attempt failed verification.",
        "conflict": "it conflicts with newer code and needs a rebuild.",
        "rejected": "rejected.", "discarded": "discarded.", "rolled_back": "rolled back.",
    }.get(status, status + ".")


@registry.register("evolution_status", "List recent self-improvement proposals and their status.")
async def evolution_status(ctx: ToolContext) -> ToolResult:
    proposals = ctx.app.store.list_proposals(limit=15)
    if not proposals:
        return ToolResult("No self-improvement proposals yet.")
    return ToolResult("\n".join(f"{p['id']} \"{p['title']}\": {_evolution_state(ctx, p)}" for p in proposals))


# ---------------------------------------------------------------- skills
@registry.register(
    "save_skill",
    "Save a reusable procedure as a skill (a SKILL.md Codex loads in future sessions). Use for workflows you figured "
    "out that will come up again, e.g. 'deploy my blog' or 'check package updates'.",
    {
        "properties": {
            "name": {"type": "string", "description": "kebab-case name, e.g. 'check-weather'."},
            "description": {"type": "string", "description": "One line: when to use this skill."},
            "instructions": {"type": "string", "description": "Markdown step-by-step instructions, including exact commands."},
        },
        "required": ["name", "description", "instructions"],
    },
    scopes=("chat", "agent"),
)
async def save_skill(ctx: ToolContext, name: str, description: str, instructions: str) -> ToolResult:
    try:
        skill = await ctx.app.skills.save(name, description, instructions, source=ctx.task_id or ctx.conversation_id or "")
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Skill '{skill['name']}' saved at {skill['path']}.")


# ---------------------------------------------------------------- user-facing
@registry.register(
    "notify_user",
    "Send the user a notification (shows in the app and as a desktop notification). Use for important results from "
    "routines or background work, not for normal chat replies.",
    {"properties": {"title": {"type": "string"}, "body": {"type": "string"}}, "required": ["title"]},
    scopes=ALL,
)
async def notify_user(ctx: ToolContext, title: str, body: str = "") -> ToolResult:
    ctx.app.notify("weebo", title[:140], body[:4000], {"conversation_id": ctx.conversation_id, "task_id": ctx.task_id})
    return ToolResult("Notification sent.")


@registry.register(
    "screenshot",
    "Capture the user's screen so you can see it. Only use when the user asks you to look at their screen.",
    {"properties": {"monitor": {"type": "integer", "minimum": 0, "description": "0 = all monitors, 1 = primary (default)."}}},
)
async def screenshot(ctx: ToolContext, monitor: int = 1) -> ToolResult:
    try:
        import mss
        from PIL import Image
    except ImportError as exc:
        raise ToolError("Screen capture needs the 'mss' and 'pillow' packages.") from exc

    def grab() -> str:
        with mss.mss() as sct:
            index = monitor if 0 <= monitor < len(sct.monitors) else 1
            shot = sct.grab(sct.monitors[index])
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        image.thumbnail((1600, 1600))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()

    url = await asyncio.to_thread(grab)
    ctx.app.bus.publish("weebo.mood", {"mood": "looking", "conversation_id": ctx.conversation_id})
    return ToolResult(f"Screenshot captured at {datetime.now():%I:%M:%S %p}.", images=[url])


@registry.register(
    "open_in_browser",
    "Open a web page in the user's default browser (only http/https).",
    {"properties": {"url": {"type": "string"}}, "required": ["url"]},
)
async def open_in_browser(ctx: ToolContext, url: str) -> ToolResult:
    if not url.lower().startswith(("http://", "https://")):
        raise ToolError("Only http(s) URLs can be opened.")
    opened = await asyncio.to_thread(webbrowser.open, url, 2)
    return ToolResult("Opened." if opened else "Could not open a browser on this machine.", success=bool(opened))


def _legacy_description() -> str:
    from ..legacy.bridge import LegacyBridge
    return (
        "Use one of Weebo 1.x's built-in abilities (they run on Codex too). Prefer your own tools when they do the "
        "job; use these for: \n" + LegacyBridge.describe_catalog()
    )


@registry.register(
    "weebo1_tool",
    _legacy_description,
    {
        "properties": {
            "name": {"type": "string", "description": "Tool name from the list."},
            "arguments": {"type": "object", "description": "Keyword arguments for the tool."},
        },
        "required": ["name"],
    },
    scopes=("chat", "agent"),
)
async def weebo1_tool(ctx: ToolContext, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
    text, success, extras = await ctx.app.legacy.run(name, arguments or {}, conversation_id=ctx.conversation_id,
                                                     task_id=ctx.task_id)
    if ctx.conversation_id and (extras.get("html") or extras.get("images")):
        kind = "widget" if extras.get("html") else "images"
        message = ctx.app.store.add_message(ctx.conversation_id, "assistant", "", kind=kind,
                                            data={"tool": name, **extras}, turn_id=ctx.turn_id or None)
        ctx.app.bus.publish("conv.message", {"conversation_id": ctx.conversation_id, "message": message})
    return ToolResult(text, success=success)


@registry.register("weebo_status", "Weebo's own status: engine, plan usage, agents, autonomy, memory and evolution.")
async def weebo_status(ctx: ToolContext) -> ToolResult:
    return ToolResult(ctx.app.status_text())


SAFE_SETTING_PREFIXES = ("voice.", "ui.", "user.", "autonomy.daily_brief", "autonomy.dream", "autonomy.self_audit",
                         "autonomy.quiet_hours", "autonomy.proactive", "agents.max_parallel", "codex.chat_effort",
                         "codex.agent_effort", "codex.fast_mode")


@registry.register(
    "update_settings",
    "Change Weebo settings when the user asks (e.g. {'voice.speak_replies': true, 'autonomy.daily_brief_time': '07:45'}). "
    "Safety-critical settings (autonomy level, evolution mode, server) can only be changed by the user in Settings.",
    {"properties": {"changes": {"type": "object", "description": "Map of dotted setting keys to new values."}},
     "required": ["changes"]},
)
async def update_settings(ctx: ToolContext, changes: dict[str, Any]) -> ToolResult:
    from ..config import SettingsError

    blocked = [k for k in changes if not k.startswith(SAFE_SETTING_PREFIXES)]
    if blocked:
        raise ToolError(f"These need the user to change them in Settings: {', '.join(blocked)}")
    try:
        changed = ctx.app.settings.update(changes)
    except SettingsError as exc:
        raise ToolError(str(exc)) from exc
    if not changed:
        return ToolResult("Nothing changed (already set).")
    return ToolResult("Updated: " + ", ".join(f"{k}={v}" for k, v in changed.items()))
