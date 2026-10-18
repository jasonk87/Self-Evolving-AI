"""Tools for the team: the chief's (scope "chat") and each bot's own (scope "bot", granted per bot)."""

from __future__ import annotations

import time
from typing import Any

from ..brain.builtin_tools import _need_user, asked_by_user
from ..brain.tools import ToolContext, ToolError, ToolResult, registry
from .manager import AUTONOMY, BUCKETS, EFFORTS, GRANTABLE, OUTCOMES, TIERS, BotError

BOT = ("bot",)
_PROFILE = {
    "name": {"type": "string"},
    "job": {"type": "string", "description": "One paragraph: what this bot is for."},
    "goals": {"type": "array", "items": {"type": "string"}},
    "tier": {"type": "string", "enum": list(TIERS), "description": "fast (cheap), standard, or capable (most able)."},
    "effort": {"type": "string", "enum": list(EFFORTS)},
    "tools": {"type": "array", "items": {"type": "string", "enum": list(GRANTABLE)}},
    "connectors": {"type": "array", "items": {"type": "string"}, "description": "Connector ids (apps or MCP)."},
    "autonomy": {"type": "array", "items": {"type": "string", "enum": list(AUTONOMY)},
                 "description": "What it may do without asking."},
    "turns_per_day": {"type": "integer", "minimum": 1, "maximum": 500},
    "routines": {"type": "array", "items": {"type": "object", "properties": {
        "text": {"type": "string"}, "when": {"type": "string"}, "recurrence": {"type": "string"}}}},
    "report_default": {"type": "string", "enum": list(BUCKETS)},
    "avatar": {"type": "string"},
}


def _bot_error(exc: Exception) -> ToolError:
    return ToolError(str(exc))


def _actor(ctx: ToolContext) -> str:
    return "user" if asked_by_user(ctx) else (ctx.bot_id or "chief")


def _line(bots, bot: dict[str, Any]) -> str:
    data = bot["data"]
    return (f"{bot['name']} [{bot['id']}] {bot['status']} · {data.get('tier')} tier, effort {data.get('effort')} · "
            f"{bots.turns_today(bot['id'])}/{bots.budget_of(bot)} turns today · {(data.get('job') or '')[:120]}")


# ---------------------------------------------------------------- the chief's tools
@registry.register("list_bots", "List your team of bots with status, tier, today's usage and job.")
async def list_bots(ctx: ToolContext) -> ToolResult:
    bots = ctx.app.bots
    rows = bots.list(("active", "paused", "proposed"))
    if not rows:
        return ToolResult("No bots yet. Propose one with propose_bot when a lane of work emerges.")
    return ToolResult("\n".join(_line(bots, b) for b in rows))


@registry.register(
    "bot_status", "One bot in detail: profile, standing rules, open jobs, recent reports and usage.",
    {"properties": {"bot": {"type": "string", "description": "Bot name or id."}}, "required": ["bot"]},
)
async def bot_status(ctx: ToolContext, bot: str) -> ToolResult:
    bots = ctx.app.bots
    try:
        row = bots.require(bot)
    except BotError as exc:
        raise _bot_error(exc) from exc
    data = row["data"]
    lines = [_line(bots, row), f"Model: {bots.model_for(row)} · autonomy: {', '.join(data.get('autonomy') or [])}",
             f"Tools: {', '.join(bots.tool_names(row))}", f"Connectors: {', '.join(data.get('connectors') or []) or 'none'}",
             f"Goals: {'; '.join(data.get('goals') or []) or 'none'}"]
    if data.get("rules"):
        lines.append("Standing rules: " + "; ".join(r.get("text", "") for r in data["rules"]))
    for job in bots.jobs(row["id"], limit=5):
        lines.append(f"Job {job['id']} ({job['status']}): {job['data'].get('instructions', '')[:120]}"
                     + (f" → {job['data']['result_summary'][:160]}" if job["data"].get("result_summary") else ""))
    for report in ctx.app.reports.search(bot_id=row["id"], limit=5):
        lines.append(f"Report ({report['bucket']}, {report['data'].get('outcome')}): {report['data'].get('summary', '')[:160]}")
    return ToolResult("\n".join(lines))


@registry.register(
    "propose_bot",
    "Write up a new specialist bot for the user to approve (a card appears in the chat; nothing is created until they "
    "say yes). Pick the cheapest tier that does the job and only the tools it needs.",
    {"properties": {**_PROFILE, "why": {"type": "string", "description": "Why this bot, and why this tier and tools."}},
     "required": ["name", "job", "tier", "why"]},
)
async def propose_bot(ctx: ToolContext, **profile: Any) -> ToolResult:
    if ctx.bot_id:
        raise ToolError("Bots can't create bots. Suggest it to the chief in a report.")
    try:
        bot = ctx.app.bots.propose(profile, actor=_actor(ctx))
    except BotError as exc:
        raise _bot_error(exc) from exc
    return ToolResult(f"Proposed {bot['name']} [{bot['id']}]. Its card is in the chat; it starts once the user "
                      "approves it.")


@registry.register(
    "approve_bot", "Approve a proposed bot. Only when the user just said yes to it in their own message.",
    {"properties": {"bot": {"type": "string"}}, "required": ["bot"]},
)
async def approve_bot(ctx: ToolContext, bot: str) -> ToolResult:
    _need_user(ctx, "creating a bot")
    try:
        row = ctx.app.bots.approve(bot, actor="user")
    except BotError as exc:
        raise _bot_error(exc) from exc
    return ToolResult(f"{row['name']} is on the team and has its own chat now.")


@registry.register(
    "decline_bot", "Drop a bot proposal (it won't be created).",
    {"properties": {"bot": {"type": "string"}}, "required": ["bot"]},
)
async def decline_bot(ctx: ToolContext, bot: str) -> ToolResult:
    try:
        row = ctx.app.bots.reject(bot, actor=_actor(ctx))
    except BotError as exc:
        raise _bot_error(exc) from exc
    return ToolResult(f"Dropped the {row['name']} proposal.")


@registry.register(
    "assign_job",
    "Give a bot a job. It starts working in its own chat and reports back to you with a short summary.",
    {"properties": {
        "bot": {"type": "string"}, "instructions": {"type": "string", "description": "Self-contained: the bot doesn't see this chat."},
        "done_criteria": {"type": "string"},
        "report_on": {"type": "string", "description": "When to report: done, fail, change, or a condition."},
        "deadline": {"type": "string"},
        "tell_user": {"type": "boolean", "description": "True when the user asked to hear back about this job."},
    }, "required": ["bot", "instructions"]},
)
async def assign_job(ctx: ToolContext, bot: str, instructions: str, done_criteria: str = "",
                     report_on: str = "done, fail", deadline: str = "", tell_user: bool = False) -> ToolResult:
    if ctx.bot_id:
        raise ToolError("Bots don't give each other jobs; ask the chief in a report.")
    try:
        job = await ctx.app.bots.assign_job(bot, instructions, done_criteria, report_on, deadline, tell_user,
                                            actor="chief", hops=ctx.hops + 1)
    except BotError as exc:
        raise _bot_error(exc) from exc
    if asked_by_user(ctx):
        ctx.app.growth.note_request(instructions, bot=job["bot_id"])
    return ToolResult(f"Job {job['id']} is with {ctx.app.bots.require(job['bot_id'])['name']} ({job['dispatch']}).")


@registry.register(
    "message_bot", "Send one of your bots a message (it answers in its own chat and reports back if needed).",
    {"properties": {"bot": {"type": "string"}, "text": {"type": "string"}, "job_id": {"type": "string"}},
     "required": ["bot", "text"]},
)
async def message_bot(ctx: ToolContext, bot: str, text: str, job_id: str = "") -> ToolResult:
    if ctx.bot_id:
        raise ToolError("Bots don't message each other; the chief relays.")
    try:
        outcome = await ctx.app.bots.message(bot, text, actor="chief", hops=ctx.hops + 1, job_id=job_id or None)
    except BotError as exc:
        raise _bot_error(exc) from exc
    return ToolResult(f"Sent ({outcome}).")


@registry.register(
    "update_bot",
    "Change a bot's profile. Free: job and goals wording, routines, pausing (status), a cheaper tier or lower effort, "
    "fewer tools, rules the user stated (rules_add). Anything adding power or cost becomes a card for the user unless "
    "the user asked for it in their own message just now.",
    {"properties": {"bot": {"type": "string"},
                    "changes": {"type": "object", "properties": {
                        **_PROFILE, "status": {"type": "string", "enum": ["active", "paused"]},
                        "rules_add": {"type": "string"}, "rules_remove": {"type": "string"}}},
                    "reason": {"type": "string"}},
     "required": ["bot", "changes"]},
)
async def update_bot(ctx: ToolContext, bot: str, changes: dict[str, Any], reason: str = "") -> ToolResult:
    if ctx.bot_id:
        raise ToolError("Use propose_profile_change for your own profile.")
    try:
        outcome, row = ctx.app.bots.change(bot, changes, actor=_actor(ctx), user_said_yes=asked_by_user(ctx),
                                           reason=reason)
    except BotError as exc:
        raise _bot_error(exc) from exc
    if outcome == "needs_user":
        return ToolResult(f"That adds power or cost ({'; '.join(row['data']['reasons'])}), so it's waiting for the "
                          f"user's yes on its card (change {row['id']}).")
    return ToolResult(f"Updated {row['name']}.")


@registry.register(
    "review_bot_change",
    "Decide on a profile change a bot asked for. Approving a change that adds power or cost puts it on a card for the "
    "user (or applies it, if the user said yes to it in their own message).",
    {"properties": {"change_id": {"type": "string"}, "approve": {"type": "boolean"}},
     "required": ["change_id", "approve"]},
)
async def review_bot_change(ctx: ToolContext, change_id: str, approve: bool) -> ToolResult:
    try:
        change = ctx.app.bots.review_change(change_id, approve, actor=_actor(ctx), user_said_yes=asked_by_user(ctx))
    except BotError as exc:
        raise _bot_error(exc) from exc
    return ToolResult({"applied": "Applied.", "rejected": "Declined.",
                       "proposed": "It adds power or cost, so it's on a card for the user now."}.get(change["status"],
                                                                                                    change["status"]))


@registry.register(
    "propose_retire_bot",
    "Propose retiring a bot (a card for the user). Default is archive: its chat stays readable, its thread is put "
    "away, its routines stop. delete=true only if the user explicitly wants it gone with its history.",
    {"properties": {"bot": {"type": "string"}, "reason": {"type": "string"}, "delete": {"type": "boolean"}},
     "required": ["bot", "reason"]},
)
async def propose_retire_bot(ctx: ToolContext, bot: str, reason: str, delete: bool = False) -> ToolResult:
    try:
        row = ctx.app.bots.propose_retire(bot, reason, delete, actor=_actor(ctx))
    except BotError as exc:
        raise _bot_error(exc) from exc
    return ToolResult(f"Proposed retiring {row['name']}; the user decides on its card.")


@registry.register(
    "retire_bot",
    "Retire a bot now. Only when the user said so in their own message. delete=true only when they explicitly said "
    "to delete it and its history; otherwise it's archived.",
    {"properties": {"bot": {"type": "string"}, "delete": {"type": "boolean"}}, "required": ["bot"]},
)
async def retire_bot(ctx: ToolContext, bot: str, delete: bool = False) -> ToolResult:
    _need_user(ctx, "retiring a bot")
    try:
        row = await ctx.app.bots.retire(bot, delete=delete, actor="user")
    except BotError as exc:
        raise _bot_error(exc) from exc
    return ToolResult("Deleted it and its chat." if row is None else f"Archived {row['name']}; its chat stays readable.")


@registry.register(
    "list_bot_reports", "Search the team's reports, including log-only ones (never transcripts).",
    {"properties": {"bot": {"type": "string"}, "bucket": {"type": "string", "enum": list(BUCKETS)},
                    "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
)
async def list_bot_reports(ctx: ToolContext, bot: str = "", bucket: str = "", query: str = "", limit: int = 15) -> ToolResult:
    bot_id = None
    if bot:
        row = ctx.app.bots.get(bot)
        if not row:
            raise ToolError(f"No bot {bot!r}.")
        bot_id = row["id"]
    rows = ctx.app.reports.search(bot_id=bot_id, bucket=bucket or None, query=query, limit=limit)
    if not rows:
        return ToolResult("No matching reports.")
    return ToolResult("\n".join(f"{r['id']} {r['data'].get('bot_name')} ({r['bucket']}, {r['data'].get('outcome')}): "
                                f"{r['data'].get('summary', '')[:200]}" for r in rows))


# ---------------------------------------------------------------- reporting and approval policy (the chief's)
@registry.register(
    "set_report_rule",
    "Save how the user wants to hear about something: interrupt (now), brief (the daily brief) or log (searchable, "
    "never pushed). From what they said in their own message, e.g. 'don't care about nightly PR fixes' → "
    "scope Coder, match 'PR fix', bucket log. Trouble (failures, stuck bots) always reaches at least the brief.",
    {"properties": {"scope": {"type": "string", "description": "Bot name or id, or * for all bots."},
                    "match": {"type": "string", "description": "Words that must all appear, or a glob, or *."},
                    "bucket": {"type": "string", "enum": list(BUCKETS)}},
     "required": ["scope", "match", "bucket"]},
)
async def set_report_rule(ctx: ToolContext, scope: str, match: str, bucket: str) -> ToolResult:
    _need_user(ctx, "changing what you hear about")
    try:
        rule = ctx.app.reports.add_rule(scope, match, bucket, actor="user")
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Saved rule {rule['id']}: \"{rule['match']}\" → {rule['bucket']}.")


@registry.register("list_report_rules", "List the reporting rules (what goes to interrupt, brief or log).")
async def list_report_rules(ctx: ToolContext) -> ToolResult:
    rules = ctx.app.reports.rules()
    if not rules:
        return ToolResult("No reporting rules; each bot's default applies (usually the brief).")
    return ToolResult("\n".join(f"{r['id']}: {r['scope']} \"{r['match']}\" → {r['bucket']}" for r in rules))


@registry.register(
    "remove_report_rule", "Remove a reporting rule. Only when the user asks.",
    {"properties": {"rule_id": {"type": "string"}}, "required": ["rule_id"]},
)
async def remove_report_rule(ctx: ToolContext, rule_id: str) -> ToolResult:
    _need_user(ctx, "changing what you hear about")
    if not ctx.app.reports.remove_rule(rule_id, actor="user"):
        raise ToolError(f"No rule {rule_id}.")
    return ToolResult("Removed.")


@registry.register("list_standing_rules", "List standing permissions (what bots may do without asking the user).")
async def list_standing_rules(ctx: ToolContext) -> ToolResult:
    rules = ctx.app.approvals.rules()
    if not rules:
        return ToolResult("No standing permissions.")
    return ToolResult("\n".join(f"{r['id']}: {r['effect']} {r['action']} {r['target'] or '(anything)'} for "
                                f"{r['scope']}" + (" (once)" if r["data"].get("once") else "") for r in rules))


@registry.register(
    "allow_always",
    "Save a standing permission the user just gave in their own message, e.g. Coder may always run 'git push' "
    "to its PR branches. action is an autonomy kind (send, spend, contact_people, delete, act...) or command / "
    "file_change; target narrows it (a command's first words, a folder, a connector:tool).",
    {"properties": {"bot": {"type": "string", "description": "Bot name/id or *."},
                    "action": {"type": "string"}, "target": {"type": "string"}, "note": {"type": "string"}},
     "required": ["bot", "action"]},
)
async def allow_always(ctx: ToolContext, bot: str, action: str, target: str = "", note: str = "") -> ToolResult:
    _need_user(ctx, "standing permissions")
    try:
        rule = ctx.app.approvals.add_rule(bot, action, target, "user", note=note)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"Saved standing permission {rule['id']}.")


@registry.register(
    "revoke_standing_rule", "Revoke a standing permission (always allowed: it only takes power away).",
    {"properties": {"rule_id": {"type": "string"}}, "required": ["rule_id"]},
)
async def revoke_standing_rule(ctx: ToolContext, rule_id: str) -> ToolResult:
    if not ctx.app.approvals.revoke(rule_id, actor=_actor(ctx)):
        raise ToolError(f"No rule {rule_id}.")
    return ToolResult("Revoked.")


@registry.register(
    "decide_request",
    "Settle an item on the 'needs you' card when the user answered it in their own message: approve (once), always "
    "(and remember it), or deny.",
    {"properties": {"item_id": {"type": "string"}, "decision": {"type": "string", "enum": ["approve", "always", "deny"]}},
     "required": ["item_id", "decision"]},
)
async def decide_request(ctx: ToolContext, item_id: str, decision: str) -> ToolResult:
    if decision != "deny":
        _need_user(ctx, "approving that")
    try:
        item = await ctx.app.approvals.resolve(item_id, decision, actor=_actor(ctx))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"{item['status'].capitalize()}.")


# ---------------------------------------------------------------- a bot's own tools
def _me(ctx: ToolContext) -> dict[str, Any]:
    bot = ctx.app.bots.get(ctx.bot_id) if ctx.bot_id else None
    if bot is None:
        raise ToolError("Only a bot can use this.")
    return bot


@registry.register(
    "report_to_chief",
    "Report to the chief: the outcome and a summary of at most three lines (only the summary reaches the chief). "
    "Set needs_attention for trouble or anything the user must decide.",
    {"properties": {"outcome": {"type": "string", "enum": list(OUTCOMES)},
                    "summary": {"type": "string", "description": "Three short lines at most."},
                    "job_id": {"type": "string"}, "needs_attention": {"type": "boolean"},
                    "details_ref": {"type": "string", "description": "Where the details are (this chat by default)."}},
     "required": ["outcome", "summary"]},
    scopes=BOT,
)
async def report_to_chief(ctx: ToolContext, outcome: str, summary: str, job_id: str = "", needs_attention: bool = False,
                          details_ref: str = "") -> ToolResult:
    bot = _me(ctx)
    session = ctx.app.conversations.sessions.get(ctx.conversation_id or "")
    if session and session.turn:
        session.turn.reported = True
    job_id = job_id or (session.turn.job_id if session and session.turn else "") or ""
    report = ctx.app.reports.file(bot, outcome, summary, job_id=job_id or None, needs_attention=needs_attention,
                                  hops=ctx.hops, details_ref=details_ref or f"{ctx.conversation_id}/{ctx.turn_id}")
    return ToolResult(f"Reported to the chief ({report['bucket']}).")


@registry.register(
    "save_standing_rule",
    "Save how the user wants you to do your job, when they tell you so in this chat (it becomes part of your profile "
    "and the chief gets a one-line note).",
    {"properties": {"text": {"type": "string"}}, "required": ["text"]},
    scopes=BOT,
)
async def save_standing_rule(ctx: ToolContext, text: str) -> ToolResult:
    bot = _me(ctx)
    if not asked_by_user(ctx):
        raise ToolError("Only the user can give you a standing rule, in their own message in your chat.")
    try:
        _, row = ctx.app.bots.change(bot["id"], {"rules_add": text}, actor="user", user_said_yes=True)
    except BotError as exc:
        raise _bot_error(exc) from exc
    ctx.app.conversations.add_context(ctx.app.conversations.main_id(),
                                      f"{row['name']} saved a standing rule from the user: {text[:200]}")
    return ToolResult("Saved. I'll work that way from now on.")


@registry.register(
    "propose_profile_change",
    "Ask the chief for a change to your own profile (a rule, a tool, a connector, a different tier or budget), with "
    "your reasons. You can't apply it yourself.",
    {"properties": {"changes": {"type": "object", "properties": {**_PROFILE, "rules_add": {"type": "string"}}},
                    "reason": {"type": "string"}},
     "required": ["changes", "reason"]},
    scopes=BOT,
)
async def propose_profile_change(ctx: ToolContext, changes: dict[str, Any], reason: str) -> ToolResult:
    bot = _me(ctx)
    patch = dict(changes or {})
    if "rules_add" in patch:
        patch["rules"] = bot["data"].get("rules", []) + [{"text": str(patch.pop("rules_add"))[:500],
                                                          "added_by": bot["id"], "added_at": time.time()}]
    try:
        change = ctx.app.bots.request_change(bot, patch, reason)
    except BotError as exc:
        raise _bot_error(exc) from exc
    ctx.app.reports.file(bot, "update", f"Asks for a profile change ({change['id']}): {reason[:200]}", hops=ctx.hops,
                         source="bot")
    return ToolResult(f"Asked the chief (change {change['id']}).")


@registry.register(
    "report_capability_gap",
    "Log something you couldn't do because no tool or connector exists for it (e.g. 'driving directions'). Recurring "
    "gaps lead the chief to propose a connector.",
    {"properties": {"need": {"type": "string", "description": "What capability was missing, in a few words."},
                    "detail": {"type": "string"}},
     "required": ["need"]},
    scopes=("chat", "bot"),
)
async def report_capability_gap(ctx: ToolContext, need: str, detail: str = "") -> ToolResult:
    gap = ctx.app.growth.log_gap(need, detail, by=ctx.bot_id or "chief")
    return ToolResult(f"Logged ({gap['count']} time(s) so far).")
