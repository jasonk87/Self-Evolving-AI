"""The chief's tools for connectors, workflows and the capabilities registry (the growth loop)."""

from __future__ import annotations

from typing import Any

from ..brain.builtin_tools import _need_user, asked_by_user
from ..brain.tools import ToolContext, ToolError, ToolResult, registry


def _actor(ctx: ToolContext) -> str:
    return "user" if asked_by_user(ctx) else "chief"


@registry.register(
    "list_connectors",
    "List connectors (ChatGPT apps and MCP servers) with status, risk labels and which bots use them, plus the "
    "user's ChatGPT apps that Codex can see.",
)
async def list_connectors(ctx: ToolContext) -> ToolResult:
    rows = ctx.app.connectors.list()
    lines = []
    for row in rows:
        tools = ", ".join(f"{t['name']} ({t['risk']})" for t in row["data"].get("tools", []))
        lines.append(f"{row['name']} [{row['id']}] {row['kind']} · {row['status']}"
                     + (f" · failing ×{row['data'].get('failures')}" if row["data"].get("flagged") else "")
                     + (f" · tools: {tools}" if tools else ""))
    apps = await ctx.app.connectors.apps()
    if apps.get("available"):
        ctx.app.store.kv_set("connectors.apps_cache", apps["apps"])
        names = [f"{a.get('name') or a.get('id')}{'' if a.get('isAccessible', True) else ' (not connected)'}"
                 for a in apps["apps"][:40]]
        lines.append("ChatGPT apps: " + (", ".join(names) or "none"))
    else:
        lines.append(f"ChatGPT apps: unavailable ({apps.get('reason')})")
    return ToolResult("\n".join(lines) or "No connectors registered.")


@registry.register(
    "register_connector",
    "Register a connector from a manifest so it can be tested and offered to the user for approval. MCP: "
    "{name, kind:'mcp', transport:'stdio'|'http', command:[...] or url, description, tools:[{name, description, "
    "risk:'read'|'act'|'spend'|'contact_people', test_call?:{arguments}}], rules:{tool: rule}}. App: {name, "
    "kind:'app', app_id, tools}. Registering grants nothing.",
    {"properties": {"manifest": {"type": "object"}}, "required": ["manifest"]},
)
async def register_connector(ctx: ToolContext, manifest: dict[str, Any]) -> ToolResult:
    try:
        row = ctx.app.connectors.register(manifest, actor=_actor(ctx))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Registered {row['name']} [{row['id']}]. Next: test_connector.")


@registry.register(
    "test_connector", "Test a registered connector in isolation (start it, list its tools, make its read-only test "
    "calls). A passing test puts an approval card in front of the user.",
    {"properties": {"connector": {"type": "string"}}, "required": ["connector"]},
)
async def test_connector(ctx: ToolContext, connector: str) -> ToolResult:
    try:
        row = await ctx.app.connectors.test(connector)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    test = row["data"].get("test") or {}
    if row["status"] == "tested":
        return ToolResult(f"{row['name']} passed: tools {', '.join(test.get('found') or [])}. Its approval card is "
                          "in the chat.")
    return ToolResult(f"{row['name']} failed its test: {test.get('error') or 'see the Capabilities page'}",
                      success=False)


@registry.register(
    "approve_connector",
    "Approve a tested connector. Only when the user said yes in their own message. Tools that spend money or "
    "contact people need a rule each, in the user's words.",
    {"properties": {"connector": {"type": "string"}, "rules": {"type": "object"}}, "required": ["connector"]},
)
async def approve_connector(ctx: ToolContext, connector: str, rules: dict[str, str] | None = None) -> ToolResult:
    _need_user(ctx, "approving a connector")
    try:
        row = ctx.app.connectors.approve(connector, actor="user", rules=rules or {})
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Approved {row['name']}. Give it to a bot with update_bot (connectors).")


@registry.register(
    "disable_connector", "Disable a connector (always allowed: it only takes power away).",
    {"properties": {"connector": {"type": "string"}}, "required": ["connector"]},
)
async def disable_connector(ctx: ToolContext, connector: str) -> ToolResult:
    try:
        row = ctx.app.connectors.disable(connector, actor=_actor(ctx))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Disabled {row['name']}.")


@registry.register(
    "propose_workflow",
    "Propose a workflow: a repeatable multi-step job, possibly across bots, optionally on a schedule. It runs only "
    "once the user approves its card. steps: [{bot, instructions, outputs}] (each step gets the previous one's result).",
    {"properties": {"name": {"type": "string"}, "summary": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "object", "properties": {
                        "bot": {"type": "string"}, "instructions": {"type": "string"}, "outputs": {"type": "string"}}}},
                    "when": {"type": "string", "description": "First run, e.g. 'tomorrow 7am'."},
                    "recurrence": {"type": "string", "description": "e.g. 'daily', 'weekdays', 'every monday'."},
                    "why": {"type": "string"}},
     "required": ["name", "steps"]},
)
async def propose_workflow(ctx: ToolContext, name: str, steps: list[dict[str, Any]], summary: str = "",
                           when: str = "", recurrence: str = "", why: str = "") -> ToolResult:
    schedule = {"when": when, "recurrence": recurrence} if when or recurrence else None
    try:
        row = ctx.app.growth.propose_workflow(name, summary or name, steps, schedule, actor=_actor(ctx), why=why)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Proposed workflow {row['id']}; the user approves it on its card.")


@registry.register("list_workflows", "List workflows with status, runs, failures and skips.")
async def list_workflows(ctx: ToolContext) -> ToolResult:
    rows = ctx.app.growth.workflows()
    if not rows:
        return ToolResult("No workflows.")
    return ToolResult("\n".join(f"{w['name']} [{w['id']}] {w['status']} · runs {w['data'].get('runs', 0)}, "
                                f"failures {w['data'].get('failures', 0)}, skips {w['data'].get('skips', 0)}"
                                for w in rows))


@registry.register(
    "run_workflow", "Run an active workflow now.",
    {"properties": {"workflow": {"type": "string"}}, "required": ["workflow"]},
)
async def run_workflow(ctx: ToolContext, workflow: str) -> ToolResult:
    try:
        row = await ctx.app.growth.run_workflow(workflow, actor=_actor(ctx))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Started run {row['data'].get('runs')} of {row['name']}.")


@registry.register(
    "set_workflow_status", "Approve (active), pause, retire or reject a workflow. Approving needs the user's yes.",
    {"properties": {"workflow": {"type": "string"},
                    "status": {"type": "string", "enum": ["active", "paused", "retired", "rejected"]}},
     "required": ["workflow", "status"]},
)
async def set_workflow_status(ctx: ToolContext, workflow: str, status: str) -> ToolResult:
    row = ctx.app.growth.get_workflow(workflow)
    if status == "active" and (not row or row["status"] == "proposed"):
        _need_user(ctx, "approving a workflow")
    try:
        row = ctx.app.growth.set_workflow_status(workflow, status, actor=_actor(ctx))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"{row['name']} is {row['status']}.")


@registry.register("capabilities", "Everything Weebo can do: connectors, apps, skills, workflows, bots, open "
                   "capability gaps and pending proposals.")
async def capabilities(ctx: ToolContext) -> ToolResult:
    caps = ctx.app.growth.capabilities()
    lines = [f"Connectors: {', '.join(c['name'] + ' (' + c['status'] + ')' for c in caps['connectors']) or 'none'}",
             f"Skills: {len(caps['skills'])}", f"Workflows: {', '.join(w['name'] for w in caps['workflows']) or 'none'}",
             f"Bots: {', '.join(b['name'] + ' (' + b['status'] + ')' for b in caps['bots']) or 'none'}"]
    for gap in caps["gaps"][:8]:
        lines.append(f"Gap ({gap['count']}×, {gap['status']}): {gap['data'].get('need')}")
    return ToolResult("\n".join(lines))
