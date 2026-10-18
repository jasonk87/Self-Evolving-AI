"""Chief tools for user-chosen goals; no new execution or approval authority."""
from .tools import ToolContext, ToolError, registry
from .builtin_tools import _need_user


@registry.register("feed_source", "Read an exact local evidence id from the Feed. Memories and user messages are "
                   "remembered statements, not verified connector facts. For events, email, people and commitments "
                   "use facts_query instead, to preserve freshness checks and same-turn grounding.",
                   {"properties": {"entity": {"type": "string", "enum": ["memories", "messages", "goals"]},
                                   "id": {"type": "string"}}, "required": ["entity", "id"]})
async def feed_source(ctx: ToolContext, entity: str, id: str) -> dict:
    if entity not in ("memories", "messages", "goals"):
        raise ToolError("Use facts_query for connector facts.")
    row = ctx.app.feed.evidence(entity, id)
    if not row:
        raise ToolError("That source is no longer available.")
    return row


@registry.register("goals_list", "Read the user's chosen goals and linked bot work. Goals do not run automatically. "
                   "Ideas are offers, not goals; completing a job does not prove its goal was achieved.",
                   {"properties": {"include_closed": {"type": "boolean"}}})
async def goals_list(ctx: ToolContext, include_closed: bool = False) -> dict:
    return {"goals": [ctx.app.feed.public_goal(g) for g in ctx.app.feed.goals(include_closed)]}


@registry.register("goal_save", "Record or edit a goal the user explicitly chose in this turn. Never infer consent "
                   "from an idea, a routine or a bot report. This only records intent; it does not schedule or "
                   "delegate anything. Use assign_job only if the user also asked to start work.",
                   {"properties": {"id": {"type": "string"}, "title": {"type": "string"},
                       "details": {"type": "string"}, "next_step": {"type": "string"}, "blocker": {"type": "string"},
                       "due": {"type": ["number", "null"]}, "owner": {"type": "string"},
                       "status": {"type": "string", "enum": ["active", "paused", "done", "dropped"]}}})
async def goal_save(ctx: ToolContext, id: str = "", **fields) -> dict:
    _need_user(ctx, "choose or change a personal goal")
    try:
        if id:
            return ctx.app.feed.update(id, fields)
        return ctx.app.feed.create(fields.pop("title", ""), **fields)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@registry.register("goal_link", "Link a goal to an existing bot job, reminder or commitment the user selected. "
                   "This preserves the existing record and does not start, change or authorize its work.",
                   {"properties": {"id": {"type": "string"}, "entity": {"type": "string", "enum": ["jobs", "reminders", "commitments"]},
                                   "source_id": {"type": "string"}}, "required": ["id", "entity", "source_id"]})
async def goal_link(ctx: ToolContext, id: str, entity: str, source_id: str) -> dict:
    _need_user(ctx, "link existing work to a personal goal")
    try:
        return ctx.app.feed.link(id, entity, source_id)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
