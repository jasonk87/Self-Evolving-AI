"""A rejected launch must never recover another process's work or undo its upgrade."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from contextlib import ExitStack
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession

from weebo import paths, supervisor
from weebo.instance import DataDirectoryInUse, data_directory_lock
from weebo.server import main
from weebo.store import Store
from weebo.supervisor import STARTUP_CONFLICT_CODE


def snapshot(store):
    return {row["name"]: store.query(f'SELECT * FROM "{row["name"]}" ORDER BY 1')
            for row in store.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


@pytest.fixture
def active_store():
    store = Store()
    conv = store.create_conversation("Active work")
    store.add_message(conv["id"], "assistant", "Still writing", status="streaming")
    store.add_message(conv["id"], "assistant", "Choose one", kind="question", status="pending")
    store.add_message(conv["id"], "user", "Next request", data={"queued": True})
    task = store.create_task("Active task", "Continue working", conversation_id=conv["id"])
    store.update_task(task["id"], status="running", thread_id="active-thread")
    store.create_task("Queued task", "Wait for the active task", conversation_id=conv["id"])
    yield store
    store.close()


@pytest.mark.parametrize("conflict", ["data", "port"])
def test_second_launch_preserves_all_rows_and_never_rolls_back(active_store, data_dir, monkeypatch, conflict):
    pending = data_dir / "evolution_pending.json"
    pending.write_text(json.dumps({"merged_commit": "pending-upgrade"}), encoding="utf-8")
    helper = paths.sub("tailnet") / "status.json"
    helper.write_text('{"state": "running"}', encoding="utf-8")
    before, note, helper_state = snapshot(active_store), pending.read_bytes(), helper.read_bytes()
    with ExitStack() as resources:
        listener = resources.enter_context(socket.create_server(("127.0.0.1", 0)))
        port = listener.getsockname()[1]
        if conflict == "data":
            listener.close()  # The same data directory is exclusive even on a different, free port.
            resources.enter_context(data_directory_lock(data_dir))
            monkeypatch.setenv("WEEBO_DATA_DIR", str(data_dir / ".." / data_dir.name))
        child = subprocess.run(
            [sys.executable, "-m", "weebo", "--child", "--no-browser", "--host", "127.0.0.1", "--port", str(port)],
            cwd=paths.PROJECT_ROOT, capture_output=True, text=True, timeout=20,
        )
        assert child.returncode == STARTUP_CONFLICT_CODE, child.stdout + child.stderr
        assert snapshot(active_store) == before

        # Feed the actual rejected child's result to the supervisor with an upgrade still pending.
        launch = Mock(return_value=Mock(wait=Mock(return_value=child.returncode)))
        rollback, stop_helpers, sleep = Mock(return_value=False), Mock(), Mock()
        monkeypatch.setattr(supervisor.subprocess, "Popen", launch)
        monkeypatch.setattr(supervisor, "_rollback", rollback)
        monkeypatch.setattr(supervisor, "_stop_helpers", stop_helpers)
        monkeypatch.setattr(supervisor.time, "sleep", sleep)
        assert supervisor.supervise([]) == STARTUP_CONFLICT_CODE
        launch.assert_called_once()
        rollback.assert_not_called()
        stop_helpers.assert_not_called()
        sleep.assert_not_called()
        assert snapshot(active_store) == before
        assert pending.read_bytes() == note and helper.read_bytes() == helper_state
        assert not (helper.parent / "stop").exists()

    # A failed launch cannot leave its data lock held.
    with data_directory_lock(data_dir):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["data", "port"])
async def test_conflicts_are_rejected_before_app_construction(data_dir, monkeypatch, conflict):
    constructor = Mock(side_effect=AssertionError("App constructed before ownership checks"))
    monkeypatch.setattr(main, "WeeboApp", constructor)
    with ExitStack() as resources:
        listener = resources.enter_context(socket.create_server(("127.0.0.1", 0)))
        port = listener.getsockname()[1]
        if conflict == "data":
            listener.close()
            resources.enter_context(data_directory_lock(paths.data_dir()))
        assert await main.serve("127.0.0.1", port, False) == STARTUP_CONFLICT_CODE
    constructor.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_start", [False, True])
async def test_startup_reserves_listener_and_releases_resources(data_dir, monkeypatch, fake_engine, fail_start):
    from weebo.app import WeeboApp

    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
    instances, listening = [], asyncio.Event()
    real_start = WeeboApp.start

    def construct():
        weebo = WeeboApp()
        weebo.engine = fake_engine
        instances.append(weebo)
        return weebo

    async def start(weebo):
        with pytest.raises(DataDirectoryInUse), data_directory_lock(data_dir):
            pass
        with socket.socket() as contender, pytest.raises(OSError):
            if os.name == "posix":
                contender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            contender.bind(("127.0.0.1", port))
            contender.listen()
        if fail_start:
            raise RuntimeError("startup failed")
        await real_start(weebo, with_engine=False, with_background=False)

    async def watch(weebo):
        listening.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "WeeboApp", construct)
    monkeypatch.setattr(WeeboApp, "start", start)
    monkeypatch.setattr(main, "_watch_supervisor", watch)
    if fail_start:
        with pytest.raises(RuntimeError, match="startup failed"):
            await main.serve("127.0.0.1", port, False)
    else:
        serving = asyncio.create_task(main.serve("127.0.0.1", port, False))
        try:
            await asyncio.wait_for(listening.wait(), 5)
            async with ClientSession() as client:
                async with client.get(f"http://127.0.0.1:{port}/") as response:
                    assert response.status == 200
                    assert "Weebo" in await response.text()
        finally:
            if instances:
                instances[0].shutdown_event.set()
            await asyncio.wait_for(serving, 5)
    assert instances[0]._stopping
    with data_directory_lock(data_dir), socket.create_server(("127.0.0.1", port)):
        pass


def test_data_lock_is_released_after_process_termination(data_dir):
    paths.data_dir()
    child = subprocess.Popen(
        [sys.executable, "-u", "-c",
         "import sys; from weebo import paths; from weebo.instance import data_directory_lock; "
         "lock = data_directory_lock(paths.data_dir()); lock.__enter__(); "
         "print('locked', flush=True); sys.stdin.readline()"],
        cwd=paths.PROJECT_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(DataDirectoryInUse), data_directory_lock(data_dir):
            pass
    finally:
        child.kill()
        child.communicate(timeout=5)
    with data_directory_lock(data_dir):
        pass


def test_real_early_crashes_still_trigger_upgrade_rollback(data_dir, monkeypatch):
    pending = paths.data_dir() / "evolution_pending.json"
    pending.write_text('{"merged_commit": "pending-upgrade"}', encoding="utf-8")
    launch = Mock(side_effect=[Mock(wait=Mock(return_value=1)), Mock(wait=Mock(return_value=0))])
    rollback, stop_helpers = Mock(return_value=True), Mock()
    monkeypatch.setattr(supervisor.subprocess, "Popen", launch)
    monkeypatch.setattr(supervisor, "_rollback", rollback)
    monkeypatch.setattr(supervisor, "_stop_helpers", stop_helpers)
    assert supervisor.supervise([]) == 0
    assert launch.call_count == 2
    rollback.assert_called_once_with(pending)
    stop_helpers.assert_called_once()
