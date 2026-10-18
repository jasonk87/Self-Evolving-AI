"""Mandatory, deterministic reliability gate, independent of model/eval budgets.

The running version supplies the probe source; the candidate supplies imports.
An upgrade therefore cannot pass by deleting or weakening its copy of this suite.
"""

from __future__ import annotations

import inspect
import sys
import time
from pathlib import Path

from .git import run


def _probe():
    import tempfile
    from pathlib import Path
    from weebo.facts.compute import instant, local_day, conflicts, due_items
    from weebo.facts.repository import FactsRepository
    from weebo.store import Store

    with tempfile.TemporaryDirectory(prefix="weebo-reliability-") as folder:
        store = Store(Path(folder) / "facts.db")
        try:
            facts = FactsRepository(store)
            source = "eval:calendar"
            initial = {"external_id": "meeting", "title": "Planning", "start": 1000, "end": 2000, "tz": "UTC"}
            row, _ = facts.upsert("events", initial, source=source)
            facts.save_artifact("brief", "brief", {"start": 1000}, [("events", row["id"])])
            for start in (3000, 5000):
                facts.upsert("events", {**initial, "start": start, "end": start+1000}, source=source)
            facts.upsert("events", {**initial, "start": 5000, "end": 6000, "status": "cancelled"}, source=source)
            assert facts.get("events", row["id"])["status"] == "cancelled", "Cancelled event was resurrected"
            assert [r["change_kind"] for r in facts.changes_since()] == ["added", "moved", "moved", "cancelled"]
            assert facts.artifact("brief")["stale"], "Moved fact left a stale brief active"
            refreshed = facts.artifact("brief", lambda rows: {"status": rows[0]["status"]})
            assert refreshed["data"]["status"] == "cancelled" and not refreshed["stale"]

            spring = local_day("2026-03-08", "America/Chicago")
            autumn = local_day("2026-11-01", "America/Chicago")
            assert spring[1]-spring[0] == 23*3600 and autumn[1]-autumn[0] == 25*3600
            trip = [{"id": "flight", "start": instant("2026-03-10T09:00:00-05:00"),
                     "end": instant("2026-03-10T11:00:00-07:00")},
                    {"id": "meeting", "start": instant("2026-03-10T12:00:00-05:00"),
                     "end": instant("2026-03-10T13:00:00-05:00")}]
            assert conflicts(trip), "Cross-timezone conflict was missed"

            conv = store.create_conversation("Deep history")
            person, _ = facts.upsert("people", {"name": "Avery Example", "aliases": ["Avery"], "tz": "Europe/London"},
                                     source="local:user")
            for i in range(210):
                store.add_message(conv["id"], "user", f"Unrelated chat message {i}")
            assert facts.get("people", person["id"])["tz"] == "Europe/London", "History trimming lost a fact"

            for ext, summary in (("one", "Venue is Hall A"), ("two", "Venue is Hall B")):
                facts.upsert("threads", {"external_id": ext, "subject": "Venue", "last_message_at": 1,
                                          "summary": summary}, source="eval:mail")
            emails = facts.query("threads")
            assert len(emails) == 2 and {e["summary"] for e in emails} == {"Venue is Hall A", "Venue is Hall B"}
            assert all(e["provenance"]["source"] == "eval:mail" for e in emails)

            old, _ = facts.upsert("commitments", {"text": "Send the example deck", "owner": "assistant", "due": 100,
                                                   "created_at": 1, "source_ref": "conversation:old"}, source="local:commitments")
            assert due_items(facts.query("commitments"), 100000)[0]["id"] == old["id"], "Old commitment disappeared"
        finally:
            store.close()
    print("5 reliability scenarios passed: event changes, timezones, deep history, contradictory mail, old commitments")


async def check_reliability(worktree: Path) -> dict:
    started = time.perf_counter()
    source = inspect.getsource(_probe) + "\n_probe()\n"
    try:
        result = await run([sys.executable, "-c", source], worktree, timeout=90,
                           env={"PYTHONPATH": str(worktree), "PYTHONDONTWRITEBYTECODE": "1",
                                "WEEBO_SKIP_LEGACY_IMPORT": "1", "WEEBO_DISABLE_LEGACY": "1"})
        return {"ok": result.ok, "summary": result.text()[-6000:], "seconds": time.perf_counter()-started}
    except Exception as exc:
        return {"ok": False, "summary": f"Reliability suite could not run: {type(exc).__name__}",
                "seconds": time.perf_counter()-started}
