"""Self-diagnostics: Weebo keeps a tally of its own failures.

Repeated failures become evidence for self-evolution proposals ("I failed to do
X four times this week; here is a fix"), which is what makes evolution grounded
in real problems instead of speculative refactors.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import TYPE_CHECKING, Any

from . import log

if TYPE_CHECKING:
    from .store import Store

logger = log.get("diagnostics")

_KEY = "diagnostics"
_MAX = 200


def _signature(kind: str, message: str) -> str:
    # Strip volatile details (ids, numbers, paths) so the same bug groups together.
    core = re.sub(r"[0-9a-f]{8,}|\d+", "#", message.lower())
    core = re.sub(r"[a-z]:\\[^\s'\"]+|/[^\s'\"]+", "<path>", core)
    return hashlib.sha1(f"{kind}|{core[:300]}".encode()).hexdigest()[:16]


class Diagnostics:
    def __init__(self, store: "Store"):
        self.store = store

    def record(self, kind: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        items: dict[str, Any] = self.store.kv_get(_KEY, {}) or {}
        sig = _signature(kind, message)
        now = time.time()
        entry = items.get(sig) or {"id": sig, "kind": kind, "message": message[:2000], "count": 0,
                                   "first_seen": now, "status": "open", "data": data or {}}
        entry["count"] += 1
        entry["last_seen"] = now
        entry["message"] = message[:2000]
        if entry.get("status") == "fixed":
            entry["status"] = "regressed"
        items[sig] = entry
        if len(items) > _MAX:
            oldest = sorted(items.values(), key=lambda e: e.get("last_seen", 0))[: len(items) - _MAX]
            for old in oldest:
                items.pop(old["id"], None)
        self.store.kv_set(_KEY, items)
        logger.debug("diagnostic %s x%s: %s", kind, entry["count"], message[:200])
        return entry

    def open_issues(self, min_count: int = 1, since: float = 0.0) -> list[dict[str, Any]]:
        items = (self.store.kv_get(_KEY, {}) or {}).values()
        return sorted(
            [e for e in items if e.get("status") in ("open", "regressed") and e["count"] >= min_count
             and e.get("last_seen", 0) >= since],
            key=lambda e: (e["count"], e.get("last_seen", 0)),
            reverse=True,
        )

    def set_status(self, sig: str, status: str) -> None:
        items = self.store.kv_get(_KEY, {}) or {}
        if sig in items:
            items[sig]["status"] = status
            self.store.kv_set(_KEY, items)

    def all(self) -> list[dict[str, Any]]:
        return sorted((self.store.kv_get(_KEY, {}) or {}).values(), key=lambda e: e.get("last_seen", 0), reverse=True)
