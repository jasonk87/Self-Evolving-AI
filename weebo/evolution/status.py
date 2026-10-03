"""Plain-language data for the persistent originating-chat proposal card."""

import json

from .council import USER_SOURCES


def chat_status(proposal: dict) -> dict:
    meta = proposal.get("meta") or {}
    status = proposal["status"]
    council = meta.get("council") or {}
    labels = {
        "vetting": "Sent to Council",
        "proposed": "Submitted; waiting for a decision to build",
        "declined": "Council declined to build",
        "queued": "Queued for building",
        "building": "Building the change",
        "checking": "Verifying the change",
        "ready": "Ready for review; awaiting Jason's approval to merge",
        "merging": "Merging the change",
        "merged": "Merged",
        "failed": "Build or verification failed",
        "rejected": "Rejected",
        "discarded": "Discarded",
        "conflict": "Merge conflict; needs review",
        "rolled_back": "Rolled back",
    }
    text = labels.get(status, status.replace("_", " ").capitalize())
    if status == "checking":
        text = {"testing": "Running verification tests", "reviewing": "Reviewing the code"}.get(meta.get("stage"), text)
    if status == "merged" and meta.get("verified_at"):
        text = "Merged and running; startup verified"
    if status == "merged":
        text += "; merged automatically under the existing policy" if meta.get("automatic") else "; Jason approved the merge"
    decision = ""
    if council.get("approved") is True:
        decision = "Council approved building; this is not Jason's approval to merge."
    elif council.get("approved") is False:
        decision = "Council declined to build."
    elif council:
        decision = "Council did not decide."
        if status == "proposed":
            decision += " Awaiting your decision to build."
    elif proposal.get("source") in USER_SOURCES:
        decision = "User-requested change; skips Council. Merge approval is separate."
    reasons = []
    if council.get("reason"):
        reasons.append(council["reason"])
    for key in ("reason", "rejection_reason", "blocked"):
        if meta.get(key) and meta[key] not in reasons:
            reasons.append(meta[key])
    if status in ("failed", "conflict"):
        try:
            gates = json.loads(proposal.get("gate_report") or "{}")
        except (ValueError, TypeError):
            gates = {}
        if gates.get("error"):
            reasons.append(gates["error"])
        for result in gates.get("results", []):
            if not result.get("ok") and not result.get("skipped"):
                reasons.append(f"{result['name']}: {result.get('output') or 'Failed'}")
        if (meta.get("review_blocking") or status == "conflict") and proposal.get("review_report"):
            reasons.append(proposal["review_report"])
    return {"proposal_id": proposal["id"], "title": proposal["title"], "status": status,
            "status_text": text, "decision": decision, "reasons": reasons}
