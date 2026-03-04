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
        list_user_notices=lambda scope, include_read=False, limit=200: (
            [
                {"id": "n1", "task_id": "task_a", "status": "completed", "message": "Task finished", "source_session_id": "s1"},
                {"id": "n2", "task_id": "task_b", "status": "failed", "message": "Task failed", "source_session_id": "s1"},
            ] if include_read else [
                {"id": "n1", "task_id": "task_a", "status": "completed", "message": "Task finished", "source_session_id": "s1"}
            ]
        ),
        get_identity_pointer_summary=lambda: {"total": 3, "by_platform": {"telegram": 2, "web": 1}},
        list_identity_pointers=lambda limit=200: [
            {"identity_key": "telegram:user:1:chat:1", "session_id": "s1", "session_exists": True},
            {"identity_key": "telegram:user:2:chat:1", "session_id": "s2", "session_exists": False},
        ],
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
    assert len(payload["snapshot"]["work_inbox_preview"]) == 1
    assert payload["snapshot"]["work_inbox_preview"][0]["id"] == "n1"
    assert payload["snapshot"]["identity_session_pointers_total"] == 3
    assert payload["snapshot"]["identity_session_pointers_by_platform"]["telegram"] == 2
    assert payload["snapshot"]["identity_session_pointers_stale"] == 1
    assert payload["snapshot"]["identity_session_pointers_stale_preview"][0]["identity_key"] == "telegram:user:2:chat:1"
    assert captured["active_tasks_count"] == 2
    assert captured["active_tasks_len"] == 2




def test_status_snapshot_summary_only_omits_heavy_preview_fields(monkeypatch):
    app = _build_test_app()

    active_tasks = [SimpleNamespace(task_id="a")]
    fake_task_manager = SimpleNamespace(list_active_tasks=lambda: active_tasks)
    fake_orchestrator = SimpleNamespace(task_manager=fake_task_manager)

    monkeypatch.setattr(app_globals, "orchestrator", fake_orchestrator)
    monkeypatch.setattr(app_globals, "chat_manager", SimpleNamespace(
        list_user_notices=lambda scope, include_read=False, limit=200: [
            {"id": "n1", "task_id": "task_a", "status": "failed", "message": "Task failed", "source_session_id": "s1"}
        ],
        get_identity_pointer_summary=lambda: {"total": 2, "by_platform": {"telegram": 1, "web": 1}},
        list_identity_pointers=lambda limit=200: [
            {"identity_key": "telegram:user:2:chat:1", "session_id": "s2", "session_exists": False},
        ],
    ))
    monkeypatch.setattr(
        approvals,
        "get_status_snapshot",
        lambda active_tasks_count, active_tasks=None: {
            "background_tasks": active_tasks_count,
            "delegation_topology": [{"task_id": "task_1"}],
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/snapshot?summary_only=1')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["summary_only"] is True
    assert payload["snapshot"]["work_inbox_preview"] == []
    assert payload["snapshot"]["delegation_topology"] == []
    assert payload["snapshot"]["identity_session_pointers_stale_preview"] == []
    assert payload["snapshot"]["work_inbox_total"] == 1
    assert payload["snapshot"]["identity_session_pointers_stale"] == 1

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
    assert payload["snapshot"]["work_inbox_preview"] == []
    assert payload["snapshot"]["identity_session_pointers_total"] == 0
    assert payload["snapshot"]["identity_session_pointers_by_platform"] == {}
    assert payload["snapshot"]["identity_session_pointers_stale"] == 0
    assert payload["snapshot"]["identity_session_pointers_stale_preview"] == []


def test_delegation_topology_endpoint_returns_503_when_orchestrator_missing(monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(app_globals, "orchestrator", None)

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology')

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["success"] is False


def test_delegation_topology_endpoint_supports_filters_and_paging(monkeypatch):
    app = _build_test_app()

    active_tasks = [SimpleNamespace(task_id="t1"), SimpleNamespace(task_id="t2")]
    monkeypatch.setattr(
        app_globals,
        "orchestrator",
        SimpleNamespace(task_manager=SimpleNamespace(list_active_tasks=lambda: active_tasks)),
    )

    monkeypatch.setattr(
        approvals,
        "get_status_snapshot",
        lambda active_tasks_count, active_tasks=None: {
            "delegation_topology": [
                {"task_id": "task_a", "worker_profile": "coder_worker", "scope_type": "session", "state": "RUNNING", "source": "chat_delegate"},
                {"task_id": "task_b", "worker_profile": "coder_worker", "scope_type": "user", "state": "WAITING_FOR_REVIEW", "source": "chat_delegate"},
                {"task_id": "task_c", "worker_profile": "critic_worker", "scope_type": "session", "state": "RUNNING", "source": "planner"},
            ]
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology?worker_profile=coder_worker&source=chat_delegate&limit=1&offset=1')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["schema_version"] == 1
    assert payload["total"] == 2
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert payload["filters"]["worker_profile"] == "coder_worker"
    assert payload["filters"]["source"] == "chat_delegate"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["task_id"] == "task_b"


def test_delegation_topology_endpoint_handles_invalid_limit(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "orchestrator",
        SimpleNamespace(task_manager=SimpleNamespace(list_active_tasks=lambda: [])),
    )
    monkeypatch.setattr(approvals, "get_status_snapshot", lambda active_tasks_count, active_tasks=None: {"delegation_topology": []})

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology?limit=not-a-number&offset=-4')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["limit"] == 50
    assert payload["offset"] == 0
    assert payload["items"] == []



def test_delegation_topology_endpoint_supports_multi_state_and_task_prefix(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "orchestrator",
        SimpleNamespace(task_manager=SimpleNamespace(list_active_tasks=lambda: [SimpleNamespace(task_id="t1")])),
    )

    monkeypatch.setattr(
        approvals,
        "get_status_snapshot",
        lambda active_tasks_count, active_tasks=None: {
            "delegation_topology": [
                {"task_id": "task_alpha_1", "worker_profile": "coder_worker", "scope_type": "session", "state": "RUNNING", "source": "chat_delegate"},
                {"task_id": "task_alpha_2", "worker_profile": "critic_worker", "scope_type": "session", "state": "WAITING_FOR_REVIEW", "source": "chat_delegate"},
                {"task_id": "task_beta_1", "worker_profile": "coder_worker", "scope_type": "session", "state": "FAILED", "source": "planner"},
            ]
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology?state=RUNNING,WAITING_FOR_REVIEW&task_id_prefix=task_alpha')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["total"] == 2
    assert payload["filters"]["state"] == ["RUNNING", "WAITING_FOR_REVIEW"]
    assert payload["filters"]["task_id_prefix"] == "task_alpha"
    assert {item["task_id"] for item in payload["items"]} == {"task_alpha_1", "task_alpha_2"}



def test_delegation_topology_endpoint_supports_sorting(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "orchestrator",
        SimpleNamespace(task_manager=SimpleNamespace(list_active_tasks=lambda: [SimpleNamespace(task_id="t1")])),
    )

    monkeypatch.setattr(
        approvals,
        "get_status_snapshot",
        lambda active_tasks_count, active_tasks=None: {
            "delegation_topology": [
                {"task_id": "task_z", "worker_profile": "coder_worker", "scope_type": "session", "state": "RUNNING", "source": "chat_delegate"},
                {"task_id": "task_a", "worker_profile": "critic_worker", "scope_type": "session", "state": "WAITING_FOR_REVIEW", "source": "planner"},
                {"task_id": "task_m", "worker_profile": "coder_worker", "scope_type": "user", "state": "FAILED", "source": "chat_delegate"},
            ]
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology?sort_by=worker_profile&order=desc')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["filters"]["sort_by"] == "worker_profile"
    assert payload["filters"]["order"] == "desc"
    assert [item["worker_profile"] for item in payload["items"]] == ["critic_worker", "coder_worker", "coder_worker"]
    assert payload["breakdowns"]["by_worker"]["coder_worker"] == 2
    assert payload["breakdowns"]["by_state"]["FAILED"] == 1


def test_delegation_topology_endpoint_normalizes_invalid_sorting(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "orchestrator",
        SimpleNamespace(task_manager=SimpleNamespace(list_active_tasks=lambda: [SimpleNamespace(task_id="t1")])),
    )

    monkeypatch.setattr(
        approvals,
        "get_status_snapshot",
        lambda active_tasks_count, active_tasks=None: {
            "delegation_topology": [
                {"task_id": "task_b", "worker_profile": "coder_worker", "scope_type": "session", "state": "RUNNING", "source": "chat_delegate"},
                {"task_id": "task_a", "worker_profile": "critic_worker", "scope_type": "session", "state": "WAITING_FOR_REVIEW", "source": "planner"},
            ]
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology?sort_by=bad_field&order=sideways')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["filters"]["sort_by"] == "task_id"
    assert payload["filters"]["order"] == "asc"
    assert [item["task_id"] for item in payload["items"]] == ["task_a", "task_b"]



def test_delegation_topology_endpoint_includes_empty_breakdowns(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "orchestrator",
        SimpleNamespace(task_manager=SimpleNamespace(list_active_tasks=lambda: [])),
    )
    monkeypatch.setattr(approvals, "get_status_snapshot", lambda active_tasks_count, active_tasks=None: {"delegation_topology": []})

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["breakdowns"] == {
        "by_worker": {},
        "by_state": {},
        "by_scope": {},
        "by_source": {},
    }



def test_delegation_topology_endpoint_breakdown_scope_filtered_uses_all_filtered_items(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "orchestrator",
        SimpleNamespace(task_manager=SimpleNamespace(list_active_tasks=lambda: [SimpleNamespace(task_id="t1")]))
    )

    monkeypatch.setattr(
        approvals,
        "get_status_snapshot",
        lambda active_tasks_count, active_tasks=None: {
            "delegation_topology": [
                {"task_id": "task_a", "worker_profile": "coder_worker", "scope_type": "session", "state": "RUNNING", "source": "chat_delegate"},
                {"task_id": "task_b", "worker_profile": "critic_worker", "scope_type": "user", "state": "WAITING_FOR_REVIEW", "source": "planner"},
                {"task_id": "task_c", "worker_profile": "coder_worker", "scope_type": "session", "state": "RUNNING", "source": "chat_delegate"},
            ]
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology?limit=1&offset=0&breakdown_scope=filtered')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["breakdown_scope"] == "filtered"
    assert len(payload["items"]) == 1
    assert payload["breakdowns"]["by_worker"]["coder_worker"] == 2
    assert payload["breakdowns"]["by_scope"]["user"] == 1


def test_delegation_topology_endpoint_breakdown_scope_defaults_to_page(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "orchestrator",
        SimpleNamespace(task_manager=SimpleNamespace(list_active_tasks=lambda: [SimpleNamespace(task_id="t1")]))
    )

    monkeypatch.setattr(
        approvals,
        "get_status_snapshot",
        lambda active_tasks_count, active_tasks=None: {
            "delegation_topology": [
                {"task_id": "task_a", "worker_profile": "coder_worker", "scope_type": "session", "state": "RUNNING", "source": "chat_delegate"},
                {"task_id": "task_b", "worker_profile": "critic_worker", "scope_type": "user", "state": "WAITING_FOR_REVIEW", "source": "planner"},
            ]
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology?limit=1&offset=0&breakdown_scope=invalid')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["breakdown_scope"] == "page"
    assert payload["breakdowns"]["by_worker"] in ({"coder_worker": 1}, {"critic_worker": 1})
