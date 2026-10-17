"""Facts tools: read freshness at the boundary, never accept model-supplied provenance."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from ..brain.tools import ToolContext, ToolError, registry
from ..timeparse import zone
from .compute import local_day

SCOPES = ("chat", "agent", "background", "bot")


@registry.register("facts_query", "Read current source-backed events, email threads or people. Required in THIS turn before "
                   "stating facts. Unverified or disconnected sources are not empty calendars/inboxes. Times use ISO offsets or epoch seconds.",
                   {"properties": {"entity": {"type": "string", "enum": ["events", "threads", "people"]},
                                   "day": {"type": "string", "description": "For events: today, tomorrow or YYYY-MM-DD in user's timezone"},
                                   "start": {"type": "number"}, "end": {"type": "number"},
                                   "needs_reply": {"type": "boolean"}, "source": {"type": "string"},
                                   "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                                   "offset": {"type": "integer", "minimum": 0}}, "required": ["entity"]}, scopes=SCOPES)
async def facts_query(ctx: ToolContext, entity: str, day: str | None = None, **filters) -> dict:
    if entity not in {"events", "threads", "people"}:
        raise ToolError("Use commitments_list for commitments")
    if day is not None:
        if entity != "events" or "start" in filters or "end" in filters:
            raise ToolError("day is only for events, and cannot be combined with start/end")
        tz = zone(ctx.app.settings.get("user.timezone"))
        today = datetime.now(tz).date()
        target = today if day == "today" else today + timedelta(days=1) if day == "tomorrow" else date.fromisoformat(day)
        # zone() can be a fixed-offset local zone; ISO-aware boundaries also cover that case.
        if hasattr(tz, "key"):
            filters["start"], filters["end"] = local_day(target, tz.key)
        else:
            filters["start"] = datetime.combine(target, datetime.min.time(), tzinfo=tz).timestamp()
            filters["end"] = datetime.combine(target + timedelta(days=1), datetime.min.time(), tzinfo=tz).timestamp()
    return await ctx.app.facts.query(entity, thread_id=ctx.thread_id, turn_id=ctx.turn_id, **filters)


@registry.register("facts_changes_since", "Read the durable facts change log after a numeric cursor. These are historical changes; "
                   "call facts_query for the current state before answering or acting.",
                   {"properties": {"cursor": {"type": "integer", "minimum": 0},
                                   "limit": {"type": "integer", "minimum": 1, "maximum": 500}}}, scopes=SCOPES)
async def facts_changes_since(ctx: ToolContext, cursor: int = 0, limit: int = 100) -> dict:
    rows = ctx.app.facts.repository.changes_since(cursor, limit)
    for row in rows:
        current = ctx.app.facts.repository.get(row["entity"], row["entity_id"])
        row["provenance"] = current["provenance"] if current else None
    return {"changes": rows, "cursor": rows[-1]["id"] if rows else cursor, "historical": True}


@registry.register("commitments_list", "Read durable commitments, oldest due first. A commitment remains here across any chat/context size.",
                   {"properties": {"status": {"type": "string", "enum": ["open", "done", "dropped"]},
                                   "due_before": {"type": "number"}, "limit": {"type": "integer"}, "offset": {"type": "integer"}}}, scopes=SCOPES)
async def commitments_list(ctx: ToolContext, status: str = "open", due_before: float | None = None,
                           limit: int = 100, offset: int = 0) -> dict:
    return await ctx.app.facts.query("commitments", thread_id=ctx.thread_id, turn_id=ctx.turn_id,
                                     status=status, end=due_before, limit=limit, offset=offset)


@registry.register("commitment_add", "Record an explicit commitment, not a guess. Does not send, book, schedule or authorize anything. "
                   "User-owned commitments may only be recorded during a user-started turn.",
                   {"properties": {"text": {"type": "string"}, "owner": {"type": "string", "enum": ["user", "assistant"]},
                                   "due": {"type": "number"}}, "required": ["text", "owner"]}, scopes=SCOPES)
async def commitment_add(ctx: ToolContext, text: str, owner: str, due: float | None = None) -> dict:
    from ..brain.builtin_tools import _need_user
    if owner == "user":
        _need_user(ctx, "Record a commitment on the user's behalf")
    if not ctx.conversation_id or not ctx.turn_id:
        raise ToolError("A conversation and turn are required as the commitment source")
    row, _ = ctx.app.facts.repository.upsert("commitments", {
        "text": text, "owner": owner, "due": due, "source_ref": f"conversation:{ctx.conversation_id}/turn:{ctx.turn_id}",
        "created_at": time.time()}, source="local:commitments", ttl_seconds=86400)
    ctx.app.store.journal("facts.commitment", "Commitment recorded", data={"id": row["id"], "actor": ctx.trigger})
    return row


@registry.register("commitment_update", "Update a durable commitment. Does not perform the promised action. User-owned changes need "
                   "the user's own turn; status done requires the action really to have happened.",
                   {"properties": {"id": {"type": "string"}, "text": {"type": "string"}, "due": {"type": ["number", "null"]},
                                   "status": {"type": "string", "enum": ["open", "done", "dropped"]}}, "required": ["id"]}, scopes=SCOPES)
async def commitment_update(ctx: ToolContext, id: str, **patch) -> dict:
    from ..brain.builtin_tools import _need_user
    previous = ctx.app.facts.repository.get("commitments", id)
    if not previous:
        raise ToolError("Unknown commitment")
    if previous["owner"] == "user":
        _need_user(ctx, "Change a user commitment")
    row, _ = ctx.app.facts.repository.upsert("commitments", {"id": id, **patch}, source="local:commitments", ttl_seconds=86400)
    ctx.app.store.journal("facts.commitment", "Commitment updated", data={"id": id, "actor": ctx.trigger, "fields": list(patch)})
    return row
