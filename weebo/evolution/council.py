"""The Council: should Weebo build an idea it came up with on its own?

Weebo is proactive, so it proposes improvements nobody asked for (from dreams,
self-audits, its agents, or its own initiative in a chat). Before it spends a build
on one (an agent run, the test suite and a code review, all on the user's plan),
the Council asks whether the idea is worth doing at all:

* The Skeptic argues the case against it: speculative, low value, wasteful,
  risky, duplicate, or too vague to build well.
* The Judge weighs the idea against that case and decides.

Ideas the user asked for skip the Council: the user already decided. Weebo 1.x's
council_debate judged finished code; Weebo 2.0's code review does that job now, so
the Council moved to the front where it saves the most work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import log

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("evolution.council")

USER_SOURCES = frozenset({"user"})

SKEPTIC_PROMPT = """You are the Skeptic on Weebo's Council. Weebo is a self-evolving AI assistant, and it wants to
change its own code on its own initiative; the user did not ask for this. Argue the case AGAINST building it,
concretely and briefly (under 180 words). Weigh:
- Evidence: is there a real problem or need behind it (errors, limits hit, repeated user requests), or is it a guess?
- Cost: a build agent, the test suite and a code review all spend the user's ChatGPT plan budget.
- Efficiency and complexity: would it make Weebo slower, heavier, noisier or harder to maintain?
- Risk: security, privacy, more autonomy than the user granted, or fragile changes to core code.
- History: is it a repeat of something already merged, declined, rejected, or failing below?
- Scope: is it clear and small enough to build and verify well in one go?
If the idea is genuinely good, say so plainly instead of inventing objections.

## The idea
Title: {title}
Came from: {source}
What: {description}
Why: {rationale}

## Recent self-improvement history
{history}
"""

JUDGE_PROMPT = """You are the Judge on Weebo's Council. Weebo (a self-evolving AI assistant) wants to build a change
to itself that nobody asked for. Decide whether it is worth building now.

Approve only when the benefit to the user, or to Weebo's reliability or efficiency, is clear and real, outweighs
the cost and risk, and the scope is clear enough to build and verify. Decline ideas that are speculative,
cosmetic-only, duplicates of recent work, risky for little gain, or too vague to build well. When in doubt,
decline: the user can still build any declined idea with one tap.

## The idea
Title: {title}
Came from: {source}
What: {description}
Why: {rationale}

## The Skeptic's case against it
{skeptic}

## Recent self-improvement history
{history}

Answer with JSON: verdict ("approve" or "decline"), value ("low", "medium" or "high": the benefit if built),
and reason (one or two plain sentences for the user explaining the decision).
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "decline"]},
        "value": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "value", "reason"],
    "additionalProperties": False,
}

SOURCE_LABELS = {
    "dream": "Weebo's nightly reflection (dream)",
    "self-audit": "Weebo's audit of its own code",
    "agent": "one of Weebo's background agents",
    "conversation": "Weebo's own idea during a chat (the user did not request it)",
    "routine": "a scheduled routine",
    "brief": "the morning brief",
}


def needs_council(source: str) -> bool:
    return source not in USER_SOURCES


def _history(app: "WeeboApp", proposal_id: str) -> str:
    lines = []
    for other in app.store.list_proposals(limit=25):
        if other["id"] != proposal_id:
            lines.append(f"- [{other['status']}] {other['title']}")
    return "\n".join(lines[:20]) or "(none yet)"


async def convene(app: "WeeboApp", proposal: dict[str, Any]) -> dict[str, Any]:
    """Run the debate. Returns {"approved": True|False|None, "reason", "value", "skeptic"}.
    approved is None when the Council couldn't meet; callers must not build in that case."""
    from ..brain.mind import ThinkError

    fields = {
        "title": proposal["title"],
        "source": SOURCE_LABELS.get(proposal.get("source", ""), proposal.get("source") or "Weebo"),
        "description": proposal["description"][:6000],
        "rationale": (proposal.get("rationale") or "(not given)")[:2000],
        "history": _history(app, proposal["id"]),
    }
    try:
        skeptic = str(await app.mind.think(SKEPTIC_PROMPT.format(**fields), label="council:skeptic", timeout=240)).strip()
        verdict = await app.mind.think(JUDGE_PROMPT.format(skeptic=skeptic[:4000], **fields), JUDGE_SCHEMA,
                                       label="council:judge", timeout=240)
    except (ThinkError, OSError) as exc:
        logger.warning("The Council couldn't meet: %s", exc)
        return {"approved": None, "reason": f"The Council couldn't meet: {exc}", "value": None, "skeptic": ""}
    if not isinstance(verdict, dict) or verdict.get("verdict") not in ("approve", "decline"):
        return {"approved": None, "reason": "The Council gave no clear verdict.", "value": None, "skeptic": skeptic}
    return {
        "approved": verdict["verdict"] == "approve",
        "reason": str(verdict.get("reason") or "").strip()[:1000],
        "value": verdict.get("value"),
        "skeptic": skeptic[:4000],
    }
