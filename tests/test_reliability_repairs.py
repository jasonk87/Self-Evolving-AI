import asyncio
import concurrent.futures
import json
from types import SimpleNamespace

import pytest
from flask import Flask

from ai_assistant.core.approval_manager import ApprovalManager
from ai_assistant.core.background_service import stop_background_services
from ai_assistant.core.persistence import atomic_write_json
from routes import api_bp, views_bp
import routes.chat as chat_routes
import app_globals
import routes.system as system_routes


def _build_app():
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    return app


def test_health_endpoint_reports_ready_components(monkeypatch):
    monkeypatch.setattr(app_globals, "controller", object())
    monkeypatch.setattr(app_globals, "chat_manager", object())
    monkeypatch.setattr(app_globals, "memory_manager", object())
    monkeypatch.setattr(app_globals, "ai_loop", SimpleNamespace(is_closed=lambda: False), raising=False)

    app = Flask(__name__)
    app.register_blueprint(views_bp)
    with app.test_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"


def test_chat_cancel_endpoint_cancels_active_future():
    future = concurrent.futures.Future()
    chat_routes._CHAT_FUTURES["session-cancel"] = future
    app = Flask(__name__)
    app.register_blueprint(chat_routes.chat_bp)
    try:
        with app.test_client() as client:
            response = client.post("/chat/session-cancel/cancel")
        assert response.status_code == 200
        assert response.get_json()["cancelled"] is True
        assert future.cancelled()
    finally:
        chat_routes._CHAT_FUTURES.pop("session-cancel", None)


def test_atomic_json_write_replaces_complete_file(tmp_path):
    path = tmp_path / "state.json"
    atomic_write_json(str(path), {"status": "complete", "items": [1, 2, 3]})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "status": "complete",
        "items": [1, 2, 3],
    }
    assert list(tmp_path.glob("*.tmp")) == []


def test_approval_without_rehydrated_callback_stays_pending(tmp_path):
    manager = object.__new__(ApprovalManager)
    manager.pending_requests = {
        "approval-1": {
            "id": "approval-1",
            "type": "future_request",
            "data": {},
            "description": "Needs a durable handler",
            "timestamp": 1,
            "status": "pending",
        }
    }
    manager.callbacks = {}
    manager.filepath = str(tmp_path / "pending_approvals.json")

    assert asyncio.run(manager.approve_request("approval-1")) is False
    assert "approval-1" in manager.pending_requests


@pytest.mark.asyncio
async def test_background_service_stop_cancels_threadsafe_future(monkeypatch):
    import ai_assistant.core.background_service as background_service

    loop = asyncio.get_running_loop()
    future = asyncio.run_coroutine_threadsafe(asyncio.sleep(60), loop)
    monkeypatch.setattr(background_service, "_background_service_active", True)
    monkeypatch.setattr(background_service, "_background_task", future)

    await stop_background_services()

    assert background_service._background_service_active is False
    assert background_service._background_task is None
    assert future.cancelled() or future.done()


def test_script_execution_reports_nonzero_exit_code(monkeypatch, tmp_path):
    script = tmp_path / "broken.py"
    script.write_text("raise SystemExit(3)\n", encoding="utf-8")

    monkeypatch.setattr(
        system_routes,
        "find_project",
        lambda name: {"root_path": str(tmp_path)},
    )
    monkeypatch.setattr(
        system_routes.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="", stderr="failed", returncode=3),
    )

    app = _build_app()
    with app.test_client() as client:
        response = client.post("/api/run", json={"path": "projects/demo/broken.py"})

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is False
    assert payload["returncode"] == 3


def test_terminal_execution_reports_nonzero_exit_code(monkeypatch):
    monkeypatch.setattr(system_routes.config, "SAFE_MODE", False)
    monkeypatch.setattr(
        system_routes.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="", stderr="failed", returncode=7),
    )

    app = _build_app()
    with app.test_client() as client:
        response = client.post("/api/terminal/exec", json={"command": "false"})

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is False
    assert payload["returncode"] == 7
