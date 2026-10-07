"""Did an upgrade actually help?

A proposal can name the recorded failures it addresses (diagnostic signatures: the self-audit and dreams
cite them; the evolution engine keeps only ones that exist). When the fix goes live (at merge for UI-only
changes; for Python changes, when Weebo restarts into them) those failures are marked fixed. If one recurs,
Diagnostics flags it regressed and the upgrade's outcome becomes "regressed"; if none recurs for a few days,
the outcome is "held". Outcomes feed the Council's history and the self-audit, so Weebo learns which kinds
of changes work instead of only which ones merged.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..app import WeeboApp

WINDOW_DAYS = 3

STATUS_WORDS = {
    "merged": "merged", "rolled_back": "rolled back after merging", "declined": "declined by the Council",
    "rejected": "rejected by the user", "discarded": "discarded", "failed": "failed verification",
    "conflict": "hit a merge conflict", "ready": "built, waiting for review", "proposed": "waiting for a decision",
}


def describe(proposal: dict[str, Any]) -> str:
    """One line for prompts: status, what happened after, and why it was declined if it was."""
    meta = proposal.get("meta") or {}
    status = proposal["status"]
    words = STATUS_WORDS.get(status, status.replace("_", " "))
    outcome = meta.get("outcome") or {}
    if status == "merged":
        if outcome.get("result") == "held":
            words += f"; the failures it fixed stayed fixed for {WINDOW_DAYS}+ days"
        elif outcome.get("result") == "regressed":
            words += "; BUT the failure it was meant to fix came back"
        elif meta.get("addresses") and meta.get("awaiting_live"):
            words += "; goes live when Weebo next restarts"
        elif meta.get("addresses"):
            words += "; still watching whether the fix holds"
        else:
            words += "; no measured outcome (it named no recorded failure)"
    reason = (meta.get("council") or {}).get("reason") if status == "declined" else ""
    return f"- [{words}] {proposal['title']}" + (f" (Council: {reason[:160]})" if reason else "")


def track_record(app: "WeeboApp", limit: int = 20, exclude: str = "") -> str:
    lines = [describe(p) for p in app.store.list_proposals(limit=limit + 1) if p["id"] != exclude]
    return "\n".join(lines[:limit]) or "(none yet)"


def review(app: "WeeboApp") -> list[dict[str, Any]]:
    """Settle the outcome of merged upgrades that named the failures they fix. Cheap: no model calls."""
    now, settled = time.time(), []
    for proposal in app.store.list_proposals(limit=200, statuses=("merged",)):
        meta = dict(proposal.get("meta") or {})
        outcome = meta.get("outcome") or {}
        addresses = meta.get("addresses") or []
        # Watched from when the fix went live, not from the merge (Python fixes wait for a restart).
        live_at = None if meta.get("awaiting_live") else (meta.get("live_at") or meta.get("merged_at"))
        if outcome.get("result") in ("held", "regressed") or not addresses or not live_at:
            continue
        entries = [e for e in (app.diagnostics.get(s) for s in addresses) if e and e.get("fixed_by") == proposal["id"]]
        came_back = [e for e in entries if e.get("regressed_at")]  # status may since be "reviewed" by an audit
        if came_back:
            details = [f"{e['kind']}: {e['message'][:200]} (x{e['count'] - e.get('count_at_fix', e['count'])} since)"
                       for e in came_back]
            meta["outcome"] = {"result": "regressed", "at": now, "issues": details}
            app.store.journal("evolution", f"A fix didn't hold: {proposal['title']}", "\n".join(details),
                              {"proposal_id": proposal["id"]})
            app.notify("evolution", "A self-upgrade didn't fix the problem",
                       f"{proposal['title']}: the failure came back. Weebo will look at it again in its next audit.",
                       {"proposal_id": proposal["id"]})
        elif now - float(live_at) >= WINDOW_DAYS * 86400:
            meta["outcome"] = {"result": "held", "at": now}
            app.store.journal("evolution", f"Fix held: {proposal['title']}",
                              f"No recurrence of the {len(addresses)} failure(s) it addressed in {WINDOW_DAYS} days.",
                              {"proposal_id": proposal["id"]})
        else:
            continue
        app.store.update_proposal(proposal["id"], meta=meta)
        app.evolution._publish(proposal["id"])
        settled.append({"proposal_id": proposal["id"], **meta["outcome"]})
    return settled


def stats(app: "WeeboApp") -> dict[str, int]:
    merged = app.store.list_proposals(limit=500, statuses=("merged", "rolled_back"))
    results = [((p.get("meta") or {}).get("outcome") or {}).get("result") for p in merged]
    return {"merged": sum(1 for p in merged if p["status"] == "merged"),
            "rolled_back": sum(1 for p in merged if p["status"] == "rolled_back"),
            "held": results.count("held"), "regressed": results.count("regressed")}
