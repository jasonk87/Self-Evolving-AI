"""Fresh, scoped query results and same-turn evidence receipts."""

from __future__ import annotations

import time
from collections import OrderedDict
from copy import deepcopy

from .repository import FactsRepository
from .sync import FactsSync


class Facts:
    def __init__(self, app):
        self.app = app
        self.repository = FactsRepository(app.store)
        self.sync = FactsSync(self.repository, app.settings, app.bus)
        self._receipts: OrderedDict[tuple[str, str], dict] = OrderedDict()

    async def query(self, entity: str, *, thread_id: str = "", turn_id: str = "", **filters) -> dict:
        self.repository.table(entity)
        sources = await self.sync.refresh(entity, filters.get("source"))
        rows = self.repository.query(entity, **filters)
        now = time.time()
        facts, unverified = [], []
        for row in rows:
            p = row.get("provenance") or {}
            local = p.get("source", "").startswith("local:")
            if local:
                # The local store IS the source; this query just re-read it.
                p["fetched_at"] = now
            fresh = local or now - p.get("fetched_at", 0) < p.get("ttl_seconds", 0)
            source_failed = any(s["source"] == p.get("source") and not s["ok"] for s in sources)
            if p.get("source", "").startswith("app:") and p["source"] not in self.sync.sources:
                # A raw connector read certifies these particular records for
                # their TTL, not a complete mailbox sync. Keep complete=False.
                source_failed = False
            if fresh and not source_failed:
                facts.append(row)
            else:
                unverified.append({"id": row["id"], "source": p.get("source"), "fetched_at": p.get("fetched_at"),
                                   "reason": "Source freshness could not be verified"})
        result = {"entity": entity, "facts": facts, "unverified": unverified, "sources": sources,
                  "complete": not unverified and all(s["ok"] for s in sources), "queried_at": now,
                  "next_offset": filters.get("offset", 0) + len(rows) if len(rows) >= filters.get("limit", 100) else None}
        if thread_id and turn_id:
            receipt = self._receipts.setdefault((thread_id, turn_id), {})
            # Multiple slices in one turn must not erase earlier evidence.
            previous = receipt.get(entity, {}).get("facts", [])
            accumulated = {row["id"]: row for row in previous}
            accumulated.update((row["id"], row) for row in facts)
            for row in unverified:
                accumulated.pop(row["id"], None)
            receipt[entity] = {**deepcopy(result), "facts": list(accumulated.values())}
            self._receipts.move_to_end((thread_id, turn_id))
            while len(self._receipts) > 256:
                self._receipts.popitem(last=False)
        return result

    def evidence(self, thread_id: str, turn_id: str) -> dict:
        return deepcopy(self._receipts.get((thread_id, turn_id), {}))

    def clear_turn(self, thread_id: str, turn_id: str) -> None:
        self._receipts.pop((thread_id, turn_id), None)

    def open_items(self, limit: int = 8) -> str:
        # Pointers only: the model MUST fetch the actual facts in this turn.
        commitments = self.repository.query("commitments", status="open", limit=limit)
        threads = self.repository.query("threads", needs_reply=True, limit=max(1, limit - len(commitments)))
        lines = [f"- commitment {r['id']} (lookup required)" for r in commitments]
        lines.extend(f"- email thread {r['id']} needs review (lookup required)" for r in threads[:max(0, limit-len(lines))])
        return "Open items (pointers, not verified facts; use facts tools now):\n" + ("\n".join(lines) or "No stored open items. Sources may not be connected.")
