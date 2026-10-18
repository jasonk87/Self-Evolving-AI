"""New findings from the memory/config/scheduling/git/skills review."""
import asyncio
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from weebo.config import Settings, SettingsError
from weebo.timeparse import TimeParseError, parse_when, next_occurrence


@pytest.mark.parametrize("heading", ["git status", "Running npm test", "Get-Date", "echo hello; exit"])
def test_plain_status_rejects_unquoted_commands(heading):
    from weebo.brain.activity import reasoning_heading
    assert reasoning_heading(f"**{heading}**") == ""
    assert reasoning_heading(f"**Checking the result**\n**{heading}**") == "Checking the result"


@pytest.mark.parametrize("text", ["tomorrowish 9am", "tomorrow 9am garbage", "tomorrow 9am UTC",
                                  "2026-99-99", "9am or 10am", "afternoonish", "13:999"])
def test_time_parser_does_not_guess_after_ignoring_input(text):
    with pytest.raises(TimeParseError):
        parse_when(text, datetime(2026, 1, 1, 8))


@pytest.mark.parametrize("key,value", [("voice.rate", True), ("voice.pitch", False),
                                        ("user.timezone", "Not/AZone")])
def test_settings_reject_invalid_types_and_zones(tmp_path, key, value):
    settings = Settings(tmp_path / "settings.json")
    with pytest.raises(SettingsError):
        settings.update({key: value})


def test_relative_times_and_hourly_recurrence_use_elapsed_time_across_dst():
    tz = ZoneInfo("America/Chicago")
    spring = datetime(2026, 3, 8, 1, 30, tzinfo=tz)
    assert parse_when("in 2 hours", spring).timestamp() - spring.timestamp() == 7200
    fall = datetime(2026, 11, 1, 1, 30, tzinfo=tz, fold=0)
    assert next_occurrence(fall.timestamp(), "hourly", fall.timestamp(), timezone=tz.key) == fall.timestamp() + 3600
    assert next_occurrence(fall.timestamp(), "every 3600s", fall.timestamp(), timezone=tz.key) == fall.timestamp() + 3600


@pytest.mark.parametrize("text,fold,expected_minutes", [("1:45am", 1, 15), ("today 1:15am", 0, 45),
                                                         ("sunday 1:45am", 1, 15)])
def test_clock_time_uses_upcoming_occurrence_in_repeated_dst_hour(text, fold, expected_minutes):
    now = datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/Chicago"), fold=fold)
    assert parse_when(text, now).timestamp() - now.timestamp() == expected_minutes * 60


def test_absolute_zone_and_dst_gap():
    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 3, 7, 9, tzinfo=tz)
    assert parse_when("2026-03-08T09:00:00+09:00", now).timestamp() == datetime(2026, 3, 8, tzinfo=timezone.utc).timestamp()
    with pytest.raises(TimeParseError, match="clocks change"):
        parse_when("tomorrow 2:30am", now)


def test_calendar_recurrences_preserve_original_day_and_clock():
    tz = ZoneInfo("America/Chicago")
    jan = datetime(2026, 1, 31, 9, tzinfo=tz).timestamp()
    feb = next_occurrence(jan, "monthly", jan, timezone=tz.key, anchor_at=jan)
    march = next_occurrence(feb, "monthly", feb, timezone=tz.key, anchor_at=jan)
    assert datetime.fromtimestamp(march, tz) == datetime(2026, 3, 31, 9, tzinfo=tz)
    before = datetime(2026, 3, 7, 2, 30, tzinfo=tz).timestamp()
    gap = next_occurrence(before, "daily", before, timezone=tz.key, anchor_at=before)
    assert datetime.fromtimestamp(gap, tz).hour == 3
    after = next_occurrence(gap, "daily", gap, timezone=tz.key, anchor_at=before)
    assert datetime.fromtimestamp(after, tz) == datetime(2026, 3, 9, 2, 30, tzinfo=tz)


@pytest.mark.asyncio
async def test_scheduler_uses_configured_zone_and_keeps_recurrence_anchor(app):
    app.settings.update({"user.timezone": "Asia/Tokyo"})
    reminder = app.scheduler.add("Monthly check", "2030-01-31 09:00", "monthly")
    expected = datetime(2030, 1, 31, 9, tzinfo=ZoneInfo("Asia/Tokyo")).timestamp()
    assert reminder["due_at"] == expected
    await app.scheduler._fire(reminder, expected)
    feb = app.store.get_reminder(reminder["id"])
    await app.scheduler._fire(feb, feb["due_at"])
    march = app.store.get_reminder(reminder["id"])
    assert datetime.fromtimestamp(march["due_at"], ZoneInfo("Asia/Tokyo")).day == 31


@pytest.mark.asyncio
async def test_manual_heartbeat_activity_is_reserved_before_task_runs(app, monkeypatch):
    async def work():
        await asyncio.Event().wait()

    monkeypatch.setattr(app.heartbeat, "dream", work)
    await app.heartbeat.trigger("dream")
    with pytest.raises(ValueError, match="already busy"):
        await app.heartbeat.trigger("dream")


@pytest.mark.asyncio
async def test_failed_dream_keeps_learning_checkpoint(app, monkeypatch):
    from weebo.brain.mind import ThinkError
    app.store.kv_set("last_dream", 100)
    app.store.add_message(app.conversations.main_id(), "user", "A useful conversation to learn from.")

    async def failed(*args, **kwargs):
        raise ThinkError("Timed out")

    monkeypatch.setattr(app.mind, "think", failed)
    with pytest.raises(ThinkError):
        await app.heartbeat.dream()
    assert app.store.kv_get("last_dream") == 100
    assert app.store.kv_get("last_dream_attempt") > 100


@pytest.mark.asyncio
async def test_archiving_memory_removes_semantic_vector_and_restore_reindexes(app):
    from tests.weebo.test_learning_loop import FakeEmbedder
    memory = app.memory
    memory.embedder, memory.semantic = FakeEmbedder(), "ready"
    mem, _ = memory.remember("User drives a sedan to work")
    memory.index_pending()
    memory.update(mem["id"], status="archived")
    assert mem["id"] not in memory._vectors
    assert memory.search("which car do I have") == []
    memory.update(mem["id"], status="active")
    assert memory._wake.is_set() and memory.index_pending() == 1
    assert memory.search("which car do I have")[0]["id"] == mem["id"]


@pytest.mark.asyncio
async def test_skill_description_roundtrips_yaml_punctuation(app):
    description = 'Use when: testing # checks with "quotes" and \\paths'
    result = await app.skills.save("test-description", description, "1. Check the result.")
    from pathlib import Path
    content = Path(result["path"]).read_text(encoding="utf-8")
    value = content.split("description: ", 1)[1].splitlines()[0]
    assert json.loads(value) == description
    assert next(s for s in app.skills.list() if s["name"] == "test-description")["description"] == description
    with pytest.raises(ValueError):
        await app.skills.save("empty-instructions", "Example", "  \n ")
    with pytest.raises(ValueError):
        await app.skills.save("con", "Example", "Windows device name must not be written")


@pytest.mark.asyncio
async def test_skills_refuse_linked_folders_and_preserve_metadata_if_delete_fails(app, monkeypatch):
    from pathlib import Path
    import shutil
    await app.skills.save("safe-skill", "Example", "Check it.")
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path.name == "safe-skill" or original(path))
    with pytest.raises(ValueError, match="without links"):
        await app.skills.save("safe-skill", "Example", "Cannot overwrite through a junction.")
    with pytest.raises(ValueError, match="without links"):
        app.skills.read("safe-skill")
    with pytest.raises(ValueError, match="without links"):
        await app.skills.delete("safe-skill")
    monkeypatch.setattr(Path, "is_junction", original)

    def denied(path):
        raise PermissionError("File is in use")

    monkeypatch.setattr(shutil, "rmtree", denied)
    with pytest.raises(PermissionError):
        await app.skills.delete("safe-skill")
    assert "safe-skill" in app.skills._meta()
    assert "Check it." in app.skills.read("safe-skill")


@pytest.mark.asyncio
async def test_git_paths_with_spaces_unicode_and_renames(tmp_path):
    from weebo.evolution import git as gitlib
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    await gitlib.git(repo, "init", "-q", check=True)
    await gitlib.git(repo, "config", "user.name", "Test", check=True)
    await gitlib.git(repo, "config", "user.email", "test@example.invalid", check=True)
    original = repo / "space file.py"
    original.write_text("before = 1\n")
    await gitlib.git(repo, "add", "-A", check=True)
    await gitlib.git(repo, "commit", "-qm", "base", check=True)
    base = await gitlib.head(repo)
    original.write_text("after = 2\n")
    unicode_file = repo / "café.py"
    unicode_file.write_text("value = 3\n")
    assert set(await gitlib.working_changes(repo)) == {"space file.py", "café.py"}
    assert await gitlib.dirty_tracked(repo) == ["space file.py"]
    await gitlib.git(repo, "add", "-A", check=True)
    await gitlib.git(repo, "commit", "-qm", "change", check=True)
    assert ("space file.py", 1, "after = 2") in await gitlib.added_lines(repo, base)
    assert set(await gitlib.changed_files(repo, base)) == {"space file.py", "café.py"}
    await gitlib.git(repo, "mv", "space file.py", "renamed file.py", check=True)
    assert set(await gitlib.dirty_tracked(repo)) == {"space file.py", "renamed file.py"}


@pytest.mark.asyncio
async def test_cancelled_git_archive_reaps_process(tmp_path, monkeypatch):
    from weebo.evolution import git as gitlib
    entered = asyncio.Event()

    class Process:
        returncode = None
        killed = reaped = False

        async def communicate(self):
            entered.set()
            if not self.killed:
                await asyncio.Event().wait()
            self.reaped = True
            return b"", b""

        def kill(self):
            self.killed = True

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(gitlib.extract_tree(tmp_path, "HEAD", "tests", tmp_path / "dest"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed and process.reaped
