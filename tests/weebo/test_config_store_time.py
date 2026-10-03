import json
import time
from datetime import datetime, timedelta

import pytest

from weebo.config import Settings, SettingsError
from weebo.store import Store, fts_query
from weebo.timeparse import (TimeParseError, describe_recurrence, next_occurrence, normalize_recurrence,
                             parse_duration, parse_when)


# ---------------------------------------------------------------- settings
def test_settings_defaults_and_dotted_update(tmp_path):
    s = Settings(tmp_path / "settings.json")
    assert s.get("autonomy.level") == "balanced"
    changed = s.update({"autonomy.level": "full", "agents": {"max_parallel": "5"}})
    assert changed == {"autonomy.level": "full", "agents.max_parallel": 5}
    reloaded = Settings(tmp_path / "settings.json")
    assert reloaded.get("agents.max_parallel") == 5


@pytest.mark.parametrize("patch, message", [
    ({"nope.key": 1}, "Unknown setting"),
    ({"autonomy.level": "reckless"}, "must be one of"),
    ({"agents.max_parallel": 99}, "between"),
    ({"autonomy.daily_brief_time": "25:00"}, "HH:MM"),
    ({"autonomy.proactive": "maybe"}, "true or false"),
])
def test_settings_rejects_bad_values(tmp_path, patch, message):
    s = Settings(tmp_path / "settings.json")
    with pytest.raises(SettingsError, match=message):
        s.update(patch)


def test_settings_repairs_hand_edited_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"autonomy": {"level": "chaos", "usage_ceiling_percent": 50}, "server": "oops"}))
    s = Settings(path)
    assert s.get("autonomy.level") == "balanced"
    assert s.get("autonomy.usage_ceiling_percent") == 50
    assert s.get("server.port") == 5050


def test_settings_corrupt_file_is_backed_up(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    s = Settings(path)
    assert s.get("server.port") == 5050
    assert (tmp_path / "settings.corrupt.json").exists()


def test_settings_listener_fires_on_change_only(tmp_path):
    s = Settings(tmp_path / "settings.json")
    seen = []
    s.on_change(seen.append)
    s.update({"voice.speak_replies": True})
    s.update({"voice.speak_replies": True})
    assert seen == [{"voice.speak_replies": True}]


# ---------------------------------------------------------------- store
def test_conversation_and_messages_roundtrip(tmp_path):
    store = Store(tmp_path / "w.db")
    conv = store.create_conversation("Hello")
    m1 = store.add_message(conv["id"], "user", "hi")
    m2 = store.upsert_message("x:1", conv["id"], "assistant", "text", "", status="streaming")
    store.upsert_message("x:1", conv["id"], "assistant", "text", "done!", status="done")
    msgs = store.list_messages(conv["id"])
    assert [m["id"] for m in msgs] == [m1["id"], m2["id"]]
    assert msgs[1]["content"] == "done!" and msgs[1]["status"] == "done"
    assert store.list_messages(conv["id"], before_seq=msgs[1]["seq"])[0]["id"] == m1["id"]
    store.delete_conversation(conv["id"])
    assert store.get_conversation(conv["id"]) is None
    assert store.list_messages(conv["id"]) == []


def test_desk_is_pinned_first(tmp_path):
    store = Store(tmp_path / "w.db")
    store.create_conversation("a")
    desk = store.create_conversation("Desk", kind="desk")
    store.create_conversation("b")
    assert store.list_conversations()[0]["id"] == desk["id"]


def test_memory_search_ranks_and_filters(tmp_path):
    store = Store(tmp_path / "w.db")
    store.add_memory("User loves dark roast coffee in the morning", "preference", 4)
    store.add_memory("User's dog is named Biscuit", "person", 3)
    store.add_memory("Project Hearth is a survival game", "project", 2)
    hits = store.search_memories("what coffee do I like?")
    assert hits and "coffee" in hits[0]["text"]
    assert store.search_memories("dog", kinds=["project"]) == []
    assert store.search_memories("the a of") == []


def test_fts_query_sanitizes_operators():
    assert fts_query('NEAR(") OR drop table') == '"near"* OR "drop"* OR "table"*'
    assert fts_query("") == ""


def test_due_reminders_and_kv(tmp_path):
    store = Store(tmp_path / "w.db")
    now = time.time()
    due = store.add_reminder("past", now - 5)
    store.add_reminder("future", now + 3600)
    assert [r["id"] for r in store.due_reminders(now)] == [due["id"]]
    store.kv_set("k", {"a": 1})
    assert store.kv_get("k") == {"a": 1}
    assert store.kv_get("missing", 7) == 7


# ---------------------------------------------------------------- time parsing
NOW = datetime(2026, 10, 3, 9, 0)  # a Saturday


@pytest.mark.parametrize("text, expected", [
    ("in 20 minutes", NOW + timedelta(minutes=20)),
    ("in 1h30m", NOW + timedelta(hours=1, minutes=30)),
    ("in 2 hours and 5 minutes", NOW + timedelta(hours=2, minutes=5)),
    ("tomorrow 9am", datetime(2026, 10, 4, 9, 0)),
    ("tomorrow", datetime(2026, 10, 4, 9, 0)),
    ("today at 5:30pm", datetime(2026, 10, 3, 17, 30)),
    ("8am", datetime(2026, 10, 4, 8, 0)),  # already passed today -> tomorrow
    ("monday 6pm", datetime(2026, 10, 5, 18, 0)),
    ("saturday 8am", datetime(2026, 10, 10, 8, 0)),  # today already past -> next week
    ("noon", datetime(2026, 10, 3, 12, 0)),
    ("2026-10-04T15:30", datetime(2026, 10, 4, 15, 30)),
    ("2026-10-04 15:30", datetime(2026, 10, 4, 15, 30)),
])
def test_parse_when(text, expected):
    assert parse_when(text, NOW) == expected


@pytest.mark.parametrize("text", ["", "whenever", "in a bit", "25pm"])
def test_parse_when_rejects(text):
    with pytest.raises(TimeParseError):
        parse_when(text, NOW)


def test_recurrence_normalization_and_next():
    assert normalize_recurrence("every day") == "daily"
    assert normalize_recurrence("every 30 minutes") == "every 1800s"
    assert describe_recurrence("every 1800s") == "every 30 minutes"
    with pytest.raises(TimeParseError):
        normalize_recurrence("fortnightly-ish")
    due = datetime(2026, 10, 2, 8, 0).timestamp()  # Friday
    now = datetime(2026, 10, 3, 9, 0).timestamp()  # Saturday
    assert datetime.fromtimestamp(next_occurrence(due, "daily", now)) == datetime(2026, 10, 4, 8, 0)
    assert datetime.fromtimestamp(next_occurrence(due, "weekdays", now)) == datetime(2026, 10, 5, 8, 0)
    assert next_occurrence(due, "", now) is None
    assert parse_duration("90s") == 90
