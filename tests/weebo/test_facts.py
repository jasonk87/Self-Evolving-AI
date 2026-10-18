"""Weebo 3 Phase 1: source truth survives context loss and source failures."""

import json

import pytest

from weebo.facts import compute
from weebo.facts.sync import FactBatch
from weebo.store import SCHEMA_VERSION, Store


def event(**changes):
    return {"external_id": "meeting-1", "title": "Planning", "start": 1000, "end": 2000,
            "tz": "America/Chicago", **changes}


class Source:
    id = "test:calendar"
    entity = "events"

    def __init__(self, *batches):
        self.batches, self.calls = list(batches), []

    async def fetch(self, cursor):
        self.calls.append(cursor)
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch


def test_upgrade_keeps_existing_data_and_nested_transactions(tmp_path):
    path = tmp_path / "facts.db"
    store = Store(path)
    store.kv_set("keep", {"important": True})
    store.execute("PRAGMA user_version=3")
    store.close()
    store = Store(path)
    try:
        assert store.kv_get("keep") == {"important": True}
        assert store.query_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
        with pytest.raises(ValueError), store.transaction():
            store.kv_set("outer", 1)
            with store.transaction():
                store.kv_set("inner", 2)
            raise ValueError("rollback both")
        assert store.kv_get("outer") is None and store.kv_get("inner") is None
    finally:
        store.close()


@pytest.mark.parametrize("day,hours", [("2026-03-08", 23), ("2026-11-01", 25), ("2026-06-01", 24)])
def test_day_boundaries_are_dst_aware(day, hours):
    start, end = compute.local_day(day, "America/Chicago")
    assert end - start == hours * 3600
    assert compute.display_time(start, "America/Chicago").startswith(day + "T00:00:00")


def test_ambiguous_and_nonexistent_local_time():
    with pytest.raises(ValueError, match="does not exist"):
        compute.instant("2026-03-08T02:30:00", "America/Chicago")
    with pytest.raises(ValueError, match="ambiguous"):
        compute.instant("2026-11-01T01:30:00", "America/Chicago")
    early = compute.instant("2026-11-01T01:30:00", "America/Chicago", fold=0)
    late = compute.instant("2026-11-01T01:30:00", "America/Chicago", fold=1)
    assert late - early == 3600
    assert compute.instant("2026-11-01T01:30:00-05:00") == early


def test_midnight_dst_and_skipped_local_date():
    start, end = compute.local_day("2026-09-06", "America/Santiago")
    assert end - start == 23 * 3600
    assert compute.display_time(start, "America/Santiago").startswith("2026-09-06T01:00")
    start, end = compute.local_day("2011-12-30", "Pacific/Apia")
    assert start == end


def test_cross_timezone_trip_and_unknown_travel():
    a = {"id": "a", "start": compute.instant("2026-03-10T11:00:00-05:00"),
         "end": compute.instant("2026-03-10T12:00:00-05:00")}
    b = {"id": "b", "start": compute.instant("2026-03-10T11:30:00-07:00"),
         "end": compute.instant("2026-03-10T12:30:00-07:00")}
    assert compute.conflicts([a, b]) == []
    assert compute.travel_window(a, b)["travel_status"] == "unknown"
    assert compute.travel_window(a, b)["leave_by"] is None
    travel = compute.travel_window(a, b, travel_seconds=7200, source="directions:route", buffer_seconds=600)
    assert travel["shortfall_seconds"] == 2400
    assert compute.timezone_difference(a["start"], "America/Chicago", "America/Los_Angeles") == -7200
    with pytest.raises(ValueError, match="source"):
        compute.travel_window(a, b, travel_seconds=7200)


def test_conflicts_back_to_backs_cancelled_and_zero_length():
    events = [{"id": "a", "start": 0, "end": 100}, {"id": "b", "start": 50, "end": 80},
              {"id": "c", "start": 100, "end": 120}, {"id": "d", "start": 60, "end": 60},
              {"id": "x", "start": 0, "end": 1000, "status": "cancelled"}]
    assert compute.conflicts(events) == [{"first": "a", "second": "b"}]
    assert not compute.overlaps(events[0], events[2])
    assert compute.back_to_backs([events[0], events[2]])[0]["back_to_back"]
    assert any(r["from"] == "a" and r["to"] == "c" for r in compute.back_to_backs(events))
    assert compute.due_items([{"id": "old", "status": "open", "due": 1},
                              {"id": "done", "status": "done", "due": 1},
                              {"id": "unknown", "status": "open", "due": None}], 9999)[0]["id"] == "old"


@pytest.mark.parametrize("value", [True, float("inf"), float("nan")])
def test_invalid_instants_rejected(value):
    with pytest.raises(ValueError):
        compute.instant(value)


@pytest.mark.asyncio
async def test_sync_diff_move_cancel_and_artifact_regeneration(app):
    source = Source(FactBatch([event()], "one"), FactBatch([event(start=3000, end=4000)], "two"),
                    FactBatch([event(start=5000, end=6000)], "three"),
                    FactBatch([event(start=5000, end=6000, status="cancelled")], "four"))
    app.facts.sync.register(source)
    repo = app.facts.repository
    assert (await app.facts.sync.sync(source.id, force=True))["changed"] == 1
    fact = repo.query("events")[0]
    repo.save_artifact("brief-line", "brief", {"start": fact["start"]}, [("events", fact["id"])])
    await app.facts.sync.sync(source.id, force=True)
    assert repo.artifact("brief-line")["stale"] == 1
    line = repo.artifact("brief-line", lambda facts: {"start": facts[0]["start"]})
    assert line["stale"] == 0 and line["data"]["start"] == 3000
    await app.facts.sync.sync(source.id, force=True)
    await app.facts.sync.sync(source.id, force=True)
    assert [r["change_kind"] for r in repo.changes_since()] == ["added", "moved", "moved", "cancelled"]
    assert source.calls == [None, "one", "two", "three"]
    assert repo.query("events")[0]["id"] == fact["id"]
    assert repo.query("events")[0]["provenance"]["source"] == source.id


@pytest.mark.asyncio
async def test_failed_partial_sync_is_atomic_and_does_not_refresh_ttl(app):
    source = Source(FactBatch([event()], "one"), FactBatch([event(title="Changed"), event(external_id="bad", end=-1)], "two"))
    app.facts.sync.register(source)
    await app.facts.sync.sync(source.id, force=True)
    before = app.facts.repository.query("events")[0]
    result = await app.facts.sync.sync(source.id, force=True)
    assert not result["ok"]
    assert app.facts.repository.query("events")[0] == before
    assert len(app.facts.repository.changes_since()) == 1
    assert app.store.query_one("SELECT cursor FROM facts_sources")["cursor"] == "one"


@pytest.mark.asyncio
async def test_incremental_empty_refresh_and_no_source_are_not_the_same(app):
    result = await app.facts.query("events")
    assert not result["complete"] and result["sources"][0]["error"] == "No source is connected"
    source = Source(FactBatch([], "one"))
    app.facts.sync.register(source)
    result = await app.facts.query("events")
    assert result["complete"] and result["facts"] == []


@pytest.mark.asyncio
async def test_stale_source_failure_cannot_be_claimed_as_evidence(app):
    source = Source(OSError("https://example.invalid/?key=secret"))
    app.facts.sync.register(source)
    app.facts.repository.upsert("events", event(), source=source.id, fetched_at=1)
    result = await app.facts.query("events", thread_id="t", turn_id="one")
    assert not result["complete"] and not result["facts"] and result["unverified"]
    assert "secret" not in json.dumps(result)
    assert app.facts.evidence("t", "one") and not app.facts.evidence("t", "two")


@pytest.mark.asyncio
async def test_unchanged_sync_does_not_emit_false_changes(app):
    source = Source(FactBatch([event()], "one"), FactBatch([event()], "two"), FactBatch([], "three"))
    app.facts.sync.register(source)
    for _ in range(3):
        assert (await app.facts.sync.sync(source.id, force=True))["ok"]
    assert len(app.facts.repository.changes_since()) == 1


@pytest.mark.asyncio
async def test_commitment_tools_scope_and_context_pointers(app):
    from weebo.brain import persona
    from weebo.brain.tools import ToolContext, registry
    ctx = ToolContext(app, "thread", "turn", conversation_id=app.conversations.main_id())
    result = await registry.call(ctx, "commitment_add", {"text": "Send the example deck", "owner": "user", "due": 1})
    assert result.success
    fact_id = json.loads(result.text)["id"]
    ctx.trigger = "routine"
    assert not (await registry.call(ctx, "commitment_update", {"id": fact_id, "status": "done"})).success
    assert not (await registry.call(ctx, "commitment_add", {"text": "Another promise", "owner": "user"})).success
    result = await registry.call(ctx, "commitments_list", {})
    assert result.success and json.loads(result.text)["facts"][0]["id"] == fact_id
    context, _ = persona.turn_context(app, app.conversations.desk(), "What's due?")
    assert fact_id in context and "Open items" in context
    assert "Send the example deck" not in context
    assert "THIS turn" in persona.developer_instructions(app)
    ctx.trigger = "user"
    assert (await registry.call(ctx, "commitment_update", {"id": fact_id, "status": "done"})).success
    assert not json.loads((await registry.call(ctx, "commitments_list", {})).text)["facts"]


@pytest.mark.asyncio
async def test_facts_settings_are_validated_and_user_gated(app):
    from weebo.brain.tools import ToolContext, registry
    from weebo.config import SettingsError
    with pytest.raises(SettingsError):
        app.settings.update({"facts.ttl_seconds": 0})
    result = await registry.call(ToolContext(app, "t", trigger="routine"), "update_settings", {"changes": {"facts.ttl_seconds": 600}})
    assert not result.success
