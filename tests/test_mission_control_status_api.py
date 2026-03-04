import sys
import types
from types import SimpleNamespace

from flask import Flask

import app_globals

# Prevent optional live-audio imports from breaking route-module loading in tests.
sys.modules.setdefault("pyaudio", types.SimpleNamespace())
sys.modules.setdefault(
    "ai_live_link",
    types.SimpleNamespace(
        toggle_live_mode=lambda: None,
        get_status=lambda: "inactive",
        stop_live_mode=lambda: None,
    ),
)

from routes import api_bp
from routes import approvals


def _build_test_app():
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    return app


def test_status_snapshot_returns_503_when_orchestrator_missing(monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(app_globals, "orchestrator", None)

    with app.test_client() as client:
        response = client.get('/api/status/snapshot')

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["success"] is False


def test_status_snapshot_returns_structured_data(monkeypatch):
    app = _build_test_app()

    active_tasks = [SimpleNamespace(task_id="a"), SimpleNamespace(task_id="b")]
    fake_task_manager = SimpleNamespace(list_active_tasks=lambda: active_tasks)
    fake_orchestrator = SimpleNamespace(task_manager=fake_task_manager)

    captured = {}

    def fake_snapshot(active_tasks_count, active_tasks=None):
        captured["active_tasks_count"] = active_tasks_count
        captured["active_tasks_len"] = len(active_tasks or [])
        return {
            "tools_total": 3,
            "tools_by_type": {"system": 2, "dynamic": 1},
            "projects_summary": "ok",
            "suggestions_summary": "ok",
            "background_tasks": active_tasks_count,
            "debug_mode_enabled": False,
            "autonomous_learning_enabled": True,
            "delegation_active_tasks": 0,
            "delegation_chat_delegate_active_tasks": 0,
            "delegation_by_worker": {},
            "delegation_by_scope": {},
            "delegation_by_state": {},
            "delegation_topology": [],
        }

    monkeypatch.setattr(app_globals, "orchestrator", fake_orchestrator)
    monkeypatch.setattr(app_globals, "chat_manager", SimpleNamespace(
        list_user_notices=lambda scope, include_read=False, limit=200: ([{"id": "n1"}, {"id": "n2"}] if include_read else [{"id": "n1"}])
    ))
    monkeypatch.setattr(approvals, "get_status_snapshot", fake_snapshot)

    with app.test_client() as client:
        response = client.get('/api/status/snapshot')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["schema_version"] == 1
    assert isinstance(payload["generated_at"], str)
    assert isinstance(payload["generated_at_ms"], int)
    assert payload["snapshot"]["background_tasks"] == 2
    assert payload["snapshot"]["tools_total"] == 3
    assert payload["snapshot"]["work_inbox_unread"] == 1
    assert payload["snapshot"]["work_inbox_total"] == 2
    assert captured["active_tasks_count"] == 2
    assert captured["active_tasks_len"] == 2


def test_task_assistant_action_endpoint(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(approvals, "execute_alert_action", lambda task_id, action: {"success": True, "message": f"ok:{task_id}:{action}"})

    with app.test_client() as client:
        response = client.post('/api/tasks/task_abc/assistant-action', json={"action": "summarize"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["message"] == "ok:task_abc:summarize"


def test_health_audit_endpoint(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(task_manager=SimpleNamespace()))

    with app.test_client() as client:
        response = client.get('/api/status/health-audit')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "health" in payload
    assert isinstance(payload["health"]["checks"], list)
    assert "healthy" in payload["health"]
    assert "failing_count" in payload["health"]


def test_background_cadence_endpoint_returns_runtime_values(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "config_manager",
        SimpleNamespace(
            get_all_settings=lambda: {
                "ENABLE_DREAM_MODE": True,
                "DREAM_INTERVAL_SECONDS": 900,
                "REMINDER_CHECK_INTERVAL_SECONDS": 15,
                "AUTO_WEB_PIP": False,
            }
        ),
    )
    monkeypatch.setattr(
        approvals,
        "get_service_status",
        lambda: {
            "last_dream_timestamp": 100,
            "last_visual_audit_timestamp": 200,
            "last_self_healing_timestamp": 300,
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/background-cadence')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["schema_version"] == 1
    assert payload["cadence"] == {
        "dream_mode_enabled": True,
        "dream_interval_seconds": 900,
        "reminder_check_interval_seconds": 15,
        "auto_web_pip": False,
    }
    assert payload["recent"] == {
        "last_dream_timestamp": 100,
        "last_visual_audit_timestamp": 200,
        "last_self_healing_timestamp": 300,
    }


def test_status_snapshot_work_inbox_summary_fallback(monkeypatch):
    app = _build_test_app()

    fake_task_manager = SimpleNamespace(list_active_tasks=lambda: [])
    fake_orchestrator = SimpleNamespace(task_manager=fake_task_manager)

    monkeypatch.setattr(app_globals, "orchestrator", fake_orchestrator)
    monkeypatch.setattr(app_globals, "chat_manager", None)
    monkeypatch.setattr(approvals, "get_status_snapshot", lambda active_tasks_count, active_tasks=None: {"background_tasks": active_tasks_count})

    with app.test_client() as client:
        response = client.get('/api/status/snapshot')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["snapshot"]["work_inbox_unread"] == 0
    assert payload["snapshot"]["work_inbox_total"] == 0
