import os
from pathlib import Path

import pytest

from weebo import paths, selftest
from weebo.config import Settings
from weebo.store import Store


ENV_KEYS = ("WEEBO_DATA_DIR", "WEEBO_DISABLE_LEGACY", "WEEBO_SKIP_LEGACY_IMPORT")


def snapshot(directory):
    return {path.relative_to(directory): path.read_bytes() if path.is_file() else None
            for path in directory.rglob("*")}


@pytest.mark.parametrize("outcome", ["success", "failure", "exception"])
def test_selftest_preserves_populated_runtime(data_dir, monkeypatch, capsys, outcome):
    settings = Settings()
    settings.update({"user.name": "Morgan", "ui.reduce_motion": True})
    store = Store()
    try:
        conv = store.create_conversation("Existing chat")
        store.add_message(conv["id"], "user", "Keep this message", data={"queued": True})
        store.add_message(conv["id"], "assistant", "Still working", status="streaming")
        task = store.create_task("Existing work", "Keep this task", conversation_id=conv["id"])
        store.update_task(task["id"], status="running", started_at=123)
    finally:
        store.close()
    skill = paths.skills_dir() / "existing-skill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: existing-skill\n---\nKeep these instructions.\n", encoding="utf-8")
    before = snapshot(data_dir)
    monkeypatch.setenv("WEEBO_DISABLE_LEGACY", "0")
    monkeypatch.delenv("WEEBO_SKIP_LEGACY_IMPORT", raising=False)
    previous = {key: os.environ.get(key) for key in ENV_KEYS}
    run = selftest._run
    temporary = []

    async def checked_run():
        isolated = paths.data_dir()
        temporary.append(isolated)
        assert isolated != data_dir
        assert list(isolated.iterdir()) == []
        assert os.environ["WEEBO_DISABLE_LEGACY"] == "1"
        assert os.environ["WEEBO_SKIP_LEGACY_IMPORT"] == "1"
        assert await run() == []
        assert (isolated / "weebo.db").is_file()
        assert (isolated / "skills" / "weebo-self-check" / "SKILL.md").is_file()
        if outcome == "exception":
            raise RuntimeError("injected self-test error")
        return ["injected self-test failure"] if outcome == "failure" else []

    monkeypatch.setattr(selftest, "_run", checked_run)
    if outcome == "exception":
        with pytest.raises(RuntimeError, match="injected self-test error"):
            selftest.main()
    else:
        assert selftest.main() == (1 if outcome == "failure" else 0)
        output = capsys.readouterr().out
        assert ("SELFTEST FAILED" if outcome == "failure" else "SELFTEST OK") in output
    assert {key: os.environ.get(key) for key in ENV_KEYS} == previous
    assert len(temporary) == 1 and not temporary[0].exists()
    # Includes all message/task data, settings bytes, skill files and directory entries.
    assert snapshot(data_dir) == before


@pytest.mark.parametrize("value", [None, ""])
@pytest.mark.parametrize("outcome", ["success", "failure", "exception"])
def test_selftest_restores_unset_and_empty_environment(monkeypatch, value, outcome):
    for key in ENV_KEYS:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    temporary = []

    async def run():
        directory = Path(os.environ["WEEBO_DATA_DIR"])
        temporary.append(directory)
        assert directory.is_dir()
        assert list(directory.iterdir()) == []
        assert os.environ["WEEBO_DISABLE_LEGACY"] == "1"
        assert os.environ["WEEBO_SKIP_LEGACY_IMPORT"] == "1"
        (directory / "probe").write_text("temporary data", encoding="utf-8")
        if outcome == "exception":
            raise RuntimeError("injected self-test error")
        return ["injected self-test failure"] if outcome == "failure" else []

    monkeypatch.setattr(selftest, "_run", run)
    if outcome == "exception":
        with pytest.raises(RuntimeError, match="injected self-test error"):
            selftest.main()
    else:
        assert selftest.main() == (1 if outcome == "failure" else 0)
    assert {key: os.environ.get(key) for key in ENV_KEYS} == dict.fromkeys(ENV_KEYS, value)
    assert len(temporary) == 1 and not temporary[0].exists()
