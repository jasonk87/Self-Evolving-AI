import sys
import types
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta

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
    assert payload["includes"]["delegation_topology"] is True
    assert payload["includes"]["work_inbox_preview"] is True
    assert payload["includes"]["identity_session_pointers_stale_preview"] is True
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
    assert payload["includes"]["delegation_topology"] is False
    assert payload["includes"]["work_inbox_preview"] is False
    assert payload["includes"]["identity_session_pointers_stale_preview"] is False
    assert payload["snapshot"]["work_inbox_preview"] == []
    assert payload["snapshot"]["delegation_topology"] == []
    assert payload["snapshot"]["identity_session_pointers_stale_preview"] == []
    assert payload["snapshot"]["work_inbox_total"] == 1
    assert payload["snapshot"]["identity_session_pointers_stale"] == 1



def test_status_snapshot_include_flags_can_disable_previews_without_summary_only(monkeypatch):
    app = _build_test_app()

    active_tasks = [SimpleNamespace(task_id="a")]
    fake_task_manager = SimpleNamespace(list_active_tasks=lambda: active_tasks)
    fake_orchestrator = SimpleNamespace(task_manager=fake_task_manager)

    monkeypatch.setattr(app_globals, "orchestrator", fake_orchestrator)
    monkeypatch.setattr(app_globals, "chat_manager", SimpleNamespace(
        list_user_notices=lambda scope, include_read=False, limit=200: [
            {"id": "n1", "task_id": "task_a", "status": "failed", "message": "Task failed", "source_session_id": "s1"}
        ],
        get_identity_pointer_summary=lambda: {"total": 1, "by_platform": {"telegram": 1}},
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
        response = client.get(
            '/api/status/snapshot?include_work_inbox_preview=false&include_identity_stale_preview=false&include_delegation_topology=false'
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["summary_only"] is False
    assert payload["includes"]["delegation_topology"] is False
    assert payload["includes"]["work_inbox_preview"] is False
    assert payload["includes"]["identity_session_pointers_stale_preview"] is False
    assert payload["snapshot"]["delegation_topology"] == []
    assert payload["snapshot"]["work_inbox_preview"] == []
    assert payload["snapshot"]["identity_session_pointers_stale_preview"] == []
    assert payload["snapshot"]["work_inbox_total"] == 1

def test_task_assistant_action_endpoint(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(approvals, "execute_alert_action", lambda task_id, action: {"success": True, "message": f"ok:{task_id}:{action}"})

    with app.test_client() as client:
        response = client.post('/api/tasks/task_abc/assistant-action', json={"action": "summarize"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["message"] == "ok:task_abc:summarize"


def test_background_cadence_includes_operator_policy(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "config_manager",
        SimpleNamespace(
            get_all_settings=lambda: {
                "ENABLE_DREAM_MODE": True,
                "DREAM_INTERVAL_SECONDS": 30,
                "REMINDER_CHECK_INTERVAL_SECONDS": 15,
                "AUTO_WEB_PIP": False,
            }
        ),
    )
    monkeypatch.setattr(
        approvals,
        "get_service_status",
        lambda: {
            "last_dream_timestamp": 1,
            "last_visual_audit_timestamp": 2,
            "last_self_healing_timestamp": 3,
        },
    )
    monkeypatch.setattr(
        approvals,
        "_load_operator_policy_settings",
        lambda: {
            "dream_mode_enabled": False,
            "host_automation_enabled": True,
            "host_automation_kill_switch": False,
            "require_provenance": True,
            "allowlisted_roots": ["/workspace/Self-Evolving-AI"],
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/background-cadence')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["operator_policy"]["host_automation_enabled"] is True
    assert payload["operator_policy"]["host_automation_kill_switch"] is False


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


def test_next_sprint_endpoint_returns_prioritized_plan():
    app = _build_test_app()

    with app.test_client() as client:
        response = client.get('/api/status/next-sprint')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["schema_version"] == 1
    sprint = payload["next_sprint"]
    assert sprint["sprint_id"] == "MILESTONE-D-TOKEN-GOVERNANCE-01"
    assert len(sprint["deliverables"]) >= 3
    assert "acceptance_checks" in sprint


def test_token_dashboard_endpoint_includes_budget_and_category_split(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        approvals.telemetry_tracker,
        "get_history",
        lambda limit=500: [
            {
                "timestamp": datetime.now(timezone.utc).timestamp(),
                "model": "gemini-2.5-flash",
                "task": "web_research",
                "input_tokens": 1000,
                "output_tokens": 500,
                "total_tokens": 1500,
            },
            {
                "timestamp": datetime.now(timezone.utc).timestamp(),
                "model": "gemini-2.5-flash",
                "task": "code_review",
                "input_tokens": 600,
                "output_tokens": 200,
                "total_tokens": 800,
            },
        ],
    )
    monkeypatch.setattr(
        approvals,
        "_load_token_budget_settings",
        lambda: {
            "default_daily_budget_usd": 2.0,
            "hard_stop_enabled": False,
            "category_budgets": {
                "research": 1.0,
                "coding": 3.0,
                "autonomous": 1.0,
                "general": 1.0,
            },
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/token-dashboard?window_hours=24&history_limit=20')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    dashboard = payload["token_dashboard"]
    assert dashboard["totals"]["calls"] == 2
    assert dashboard["by_category"]["research"]["calls"] == 1
    assert dashboard["by_category"]["coding"]["calls"] == 1
    assert dashboard["budget"]["default_daily_budget_usd"] == 2.0
    assert len(dashboard["budget"]["category_status"]) == 4
    assert "enforcement" in dashboard["budget"]


def test_operator_policy_update_and_preflight_guardrails(monkeypatch):
    app = _build_test_app()

    policy = {
        "schema_version": 1,
        "dream_mode_enabled": True,
        "host_automation_enabled": False,
        "host_automation_kill_switch": True,
        "require_provenance": True,
        "allowlisted_roots": ["/workspace/Self-Evolving-AI"],
        "updated_at": "now",
    }
    state = {"settings": dict(policy)}
    monkeypatch.setattr(approvals, "_load_operator_policy_settings", lambda: dict(state["settings"]))
    captured = {}

    def _save(data):
        captured["settings"] = data
        state["settings"] = dict(data)

    monkeypatch.setattr(approvals, "_save_operator_policy_settings", _save)

    with app.test_client() as client:
        update_response = client.post(
            '/api/status/operator-policy',
            json={
                "host_automation_enabled": True,
                "host_automation_kill_switch": False,
                "allowlisted_roots": ["~/", "/workspace/Self-Evolving-AI"],
            },
        )
        preflight_response = client.post(
            '/api/status/host-automation/preflight',
            json={
                "target_path": "/workspace/Self-Evolving-AI/tests",
                "actor": "operator",
                "rationale": "Review project",
                "requested_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    assert update_response.status_code == 200
    update_payload = update_response.get_json()
    assert update_payload["success"] is True
    assert update_payload["settings"]["host_automation_enabled"] is True
    assert update_payload["settings"]["host_automation_kill_switch"] is False
    assert isinstance(update_payload["settings"]["allowlisted_roots"], list)
    assert captured["settings"]["host_automation_enabled"] is True

    assert preflight_response.status_code == 200
    preflight_payload = preflight_response.get_json()
    assert preflight_payload["success"] is True
    assert preflight_payload["evaluation"]["allowed"] is True
    assert preflight_payload["evaluation"]["reasons"] == []


def test_host_automation_preflight_blocks_missing_provenance(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        approvals,
        "_load_operator_policy_settings",
        lambda: {
            "dream_mode_enabled": True,
            "host_automation_enabled": True,
            "host_automation_kill_switch": False,
            "require_provenance": True,
            "allowlisted_roots": ["/workspace/Self-Evolving-AI"],
        },
    )

    with app.test_client() as client:
        response = client.post('/api/status/host-automation/preflight', json={"target_path": "/workspace/Self-Evolving-AI"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["evaluation"]["allowed"] is False
    assert any(reason.startswith("missing_provenance:") for reason in payload["evaluation"]["reasons"])


def test_token_budget_preflight_blocks_when_hard_stop_exceeded(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        approvals,
        "_build_token_dashboard_payload",
        lambda window_hours=24, history_limit=2000: {
            "budget": {
                "default_daily_budget_usd": 1.0,
                "hard_stop_enabled": True,
                "category_status": [
                    {
                        "category": "research",
                        "daily_budget_usd": 1.0,
                        "spent_usd": 0.95,
                    }
                ],
            }
        },
    )

    with app.test_client() as client:
        response = client.post(
            '/api/status/token-budget/preflight',
            json={"category": "research", "projected_cost_usd": 0.2, "window_hours": 24},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["evaluation"]["blocked"] is True
    assert payload["evaluation"]["allowed"] is False
    assert "hard_stop_block" in payload["evaluation"]["reasons"]


def test_token_budget_preflight_allows_when_hard_stop_disabled(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        approvals,
        "_build_token_dashboard_payload",
        lambda window_hours=24, history_limit=2000: {
            "budget": {
                "default_daily_budget_usd": 1.0,
                "hard_stop_enabled": False,
                "category_status": [
                    {
                        "category": "research",
                        "daily_budget_usd": 1.0,
                        "spent_usd": 0.95,
                    }
                ],
            }
        },
    )

    with app.test_client() as client:
        response = client.post(
            '/api/status/token-budget/preflight',
            json={"category": "research", "projected_cost_usd": 0.2, "window_hours": 24},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["evaluation"]["blocked"] is False
    assert payload["evaluation"]["allowed"] is True
    assert payload["evaluation"]["would_exceed_category_budget"] is True


def test_execution_preflight_blocks_when_host_and_token_policies_fail(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        approvals,
        "_evaluate_token_budget_preflight",
        lambda category, projected_cost_usd, window_hours=24: {
            "blocked": True,
            "reasons": ["hard_stop_block"],
            "allowed": False,
        },
    )
    monkeypatch.setattr(
        approvals,
        "_load_operator_policy_settings",
        lambda: {
            "host_automation_enabled": False,
            "host_automation_kill_switch": True,
            "require_provenance": True,
            "allowlisted_roots": ["/workspace/Self-Evolving-AI"],
        },
    )

    with app.test_client() as client:
        response = client.post(
            '/api/status/execution-preflight',
            json={
                "action_type": "host_automation",
                "token_category": "research",
                "projected_cost_usd": 0.9,
                "target_path": "/workspace/Self-Evolving-AI/tests",
            },
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["evaluation"]["blocked"] is True
    assert any(reason.startswith("token:") for reason in payload["evaluation"]["reasons"])
    assert any(reason.startswith("host:") for reason in payload["evaluation"]["reasons"])


def test_execution_preflight_allows_non_host_action_when_token_allows(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        approvals,
        "_evaluate_token_budget_preflight",
        lambda category, projected_cost_usd, window_hours=24: {
            "blocked": False,
            "reasons": [],
            "allowed": True,
        },
    )

    with app.test_client() as client:
        response = client.post(
            '/api/status/execution-preflight',
            json={
                "action_type": "research",
                "token_category": "research",
                "projected_cost_usd": 0.1,
            },
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["evaluation"]["allowed"] is True
    assert payload["evaluation"]["host_automation"] is None


def test_token_budget_update_endpoint_normalizes_values(monkeypatch):
    app = _build_test_app()

    persisted = {
        "schema_version": 1,
        "default_daily_budget_usd": 1.0,
        "hard_stop_enabled": False,
        "category_budgets": {
            "research": 1.0,
            "coding": 2.0,
            "autonomous": 1.0,
            "general": 1.0,
        },
        "updated_at": "now",
    }

    monkeypatch.setattr(approvals, "_load_token_budget_settings", lambda: dict(persisted))
    captured = {}
    monkeypatch.setattr(approvals, "_save_token_budget_settings", lambda data: captured.setdefault("settings", data))

    with app.test_client() as client:
        response = client.post(
            '/api/status/token-budget',
            json={
                "default_daily_budget_usd": -5,
                "hard_stop_enabled": True,
                "category_budgets": {
                    "research": 0.25,
                    "coding": 4.5,
                    "invalid": 12,
                },
            },
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    settings = payload["settings"]
    assert settings["default_daily_budget_usd"] == 0.0
    assert settings["hard_stop_enabled"] is True
    assert settings["category_budgets"]["research"] == 0.25
    assert settings["category_budgets"]["coding"] == 4.5
    assert "invalid" not in settings["category_budgets"]
    assert captured["settings"]["hard_stop_enabled"] is True


def test_reflection_suggestions_endpoint_returns_all_items(monkeypatch):
    app = _build_test_app()

    insights = [
        SimpleNamespace(
            insight_id="i_old",
            type=SimpleNamespace(name="IMPROVEMENT"),
            status="NEW",
            description="Older suggestion",
            creation_timestamp=100,
        ),
        SimpleNamespace(
            insight_id="i_new",
            type=SimpleNamespace(name="SELF_HEALING"),
            status="SELF_HEALING_PROPOSED",
            description="Newest suggestion",
            creation_timestamp=200,
        ),
        SimpleNamespace(
            insight_id="i_done",
            type=SimpleNamespace(name="IMPROVEMENT"),
            status="APPROVED",
            description="Should be included",
            creation_timestamp=300,
        ),
    ]

    fake_orchestrator = SimpleNamespace(learning_agent=SimpleNamespace(insights=insights))
    monkeypatch.setattr(app_globals, "orchestrator", fake_orchestrator)

    import ai_assistant.core.suggestion_manager as sm
    monkeypatch.setattr(sm, "list_suggestions", lambda create_dummy=True: [])

    with app.test_client() as client:
        response = client.get('/api/status/reflection-suggestions?limit=2')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["limit"] == 2
    assert payload["count"] == 2
    assert payload["items"][0]["insight_id"] == "i_done"
    assert payload["items"][1]["insight_id"] == "i_new"


def test_reflection_suggestions_endpoint_returns_empty_when_unavailable(monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(app_globals, "orchestrator", None)

    import ai_assistant.core.suggestion_manager as sm
    monkeypatch.setattr(sm, "list_suggestions", lambda create_dummy=True: [])

    with app.test_client() as client:
        response = client.get('/api/status/reflection-suggestions')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["count"] == 0
    assert payload["items"] == []


def test_reflection_suggestions_returns_stable_counts_when_no_suggestions_exist(monkeypatch):
    app = _build_test_app()
    # Provide an orchestrator but with empty insights
    fake_orchestrator = SimpleNamespace(learning_agent=SimpleNamespace(insights=[]))
    monkeypatch.setattr(app_globals, "orchestrator", fake_orchestrator)

    import ai_assistant.core.suggestion_manager as sm
    monkeypatch.setattr(sm, "list_suggestions", lambda create_dummy=True: [])

    with app.test_client() as client:
        response = client.get('/api/status/reflection-suggestions')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["count"] == 0
    assert payload["items"] == []






def test_reflection_suggestions_include_spawn_action_and_recommendations(monkeypatch):
    app = _build_test_app()

    insights = [
        SimpleNamespace(
            insight_id="i_spawn",
            type=SimpleNamespace(name="TOOL_BUG_SUSPECTED"),
            status="NEW",
            description="Bug candidate",
            creation_timestamp=100,
            metadata={},
        ),
    ]
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(learning_agent=SimpleNamespace(insights=insights)))

    import ai_assistant.core.suggestion_manager as sm
    monkeypatch.setattr(sm, "list_suggestions", lambda create_dummy=True: [])

    with app.test_client() as client:
        response = client.get('/api/status/reflection-suggestions')

    assert response.status_code == 200
    payload = response.get_json()
    item = payload["items"][0]
    assert item["actions"]["spawn_specialist"] == "/api/status/reflection-suggestions/i_spawn/spawn-specialist"
    assert "code_reviewer_v1" in item["recommended_specialist_templates"]


def test_reflection_spawn_specialist_endpoint_creates_task_and_updates_insight(monkeypatch):
    app = _build_test_app()

    insight = SimpleNamespace(
        insight_id="i_spawn",
        type=SimpleNamespace(name="TOOL_BUG_SUSPECTED"),
        status="NEW",
        description="Investigate potential bug in parser.",
        creation_timestamp=100,
        metadata={},
    )

    captured = {}

    def fake_add_task(description, task_type, details=None, session_id=None, related_item_id=None):
        captured["description"] = description
        captured["details"] = details
        captured["session_id"] = session_id
        return SimpleNamespace(task_id="task_from_reflection")

    learning_agent = SimpleNamespace(insights=[insight], _save_insights=lambda: None)
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(learning_agent=learning_agent))
    monkeypatch.setattr(app_globals, "task_manager", SimpleNamespace(add_task=fake_add_task), raising=False)

    with app.test_client() as client:
        response = client.post(
            '/api/status/reflection-suggestions/i_spawn/spawn-specialist',
            json={"metadata": {"operator": "yes"}, "session_id": "s_reflect"},
        )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["insight_status"] == "SPECIALIST_SPAWNED"
    assert payload["task_id"] == "task_from_reflection"
    assert captured["session_id"] == "s_reflect"
    assert captured["details"]["source_insight_id"] == "i_spawn"
    assert insight.metadata["specialist_spawn_task_id"] == "task_from_reflection"


def test_reflection_spawn_specialist_endpoint_rejects_non_spawnable_status(monkeypatch):
    app = _build_test_app()

    insight = SimpleNamespace(
        insight_id="i_done",
        type=SimpleNamespace(name="TOOL_BUG_SUSPECTED"),
        status="REJECTED_BY_USER",
        description="Already handled",
        creation_timestamp=100,
        metadata={},
    )
    learning_agent = SimpleNamespace(insights=[insight], _save_insights=lambda: None)
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(learning_agent=learning_agent))
    monkeypatch.setattr(app_globals, "task_manager", SimpleNamespace(add_task=lambda **kwargs: None), raising=False)

    with app.test_client() as client:
        response = client.post('/api/status/reflection-suggestions/i_done/spawn-specialist', json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False

def test_reflection_suggestions_include_action_links(monkeypatch):
    app = _build_test_app()

    insights = [
        SimpleNamespace(
            insight_id="i_action",
            type=SimpleNamespace(name="IMPROVEMENT"),
            status="NEW",
            description="Actionable suggestion",
            creation_timestamp=100,
        ),
    ]
    fake_orchestrator = SimpleNamespace(
        learning_agent=SimpleNamespace(insights=insights)
    )
    monkeypatch.setattr(app_globals, "orchestrator", fake_orchestrator)

    import ai_assistant.core.suggestion_manager as sm
    monkeypatch.setattr(sm, "list_suggestions", lambda create_dummy=True: [])

    with app.test_client() as client:
        response = client.get('/api/status/reflection-suggestions')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["items"][0]["actions"]["approve"] == "/api/status/reflection-suggestions/i_action/approve"
    assert payload["items"][0]["actions"]["reject"] == "/api/status/reflection-suggestions/i_action/reject"


def test_reflection_suggestion_approve_endpoint_updates_status(monkeypatch):
    app = _build_test_app()

    insight = SimpleNamespace(
        insight_id="i_approve",
        type=SimpleNamespace(name="IMPROVEMENT"),
        status="NEW",
        description="Approve me",
        creation_timestamp=100,
        metadata={},
    )
    learning_agent = SimpleNamespace(
        insights=[insight],
        _save_insights=lambda: None,
    )
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(learning_agent=learning_agent))

    with app.test_client() as client:
        response = client.post('/api/status/reflection-suggestions/i_approve/approve', json={"feedback": "looks good"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["status"] == "APPROVED_BY_USER"
    assert insight.status == "APPROVED_BY_USER"
    assert insight.metadata["operator_feedback"] == "looks good"


def test_reflection_suggestion_reject_endpoint_updates_status(monkeypatch):
    app = _build_test_app()

    insight = SimpleNamespace(
        insight_id="i_reject",
        type=SimpleNamespace(name="IMPROVEMENT"),
        status="SELF_HEALING_PROPOSED",
        description="Reject me",
        creation_timestamp=100,
        metadata={},
    )
    learning_agent = SimpleNamespace(
        insights=[insight],
        _save_insights=lambda: None,
    )
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(learning_agent=learning_agent))

    with app.test_client() as client:
        response = client.post('/api/status/reflection-suggestions/i_reject/reject', json={"feedback": "not now"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["status"] == "REJECTED_BY_USER"
    assert insight.status == "REJECTED_BY_USER"
    assert insight.metadata["operator_rejection_reason"] == "not now"


def test_reflection_suggestion_triage_endpoint_rejects_non_pending_status(monkeypatch):
    app = _build_test_app()

    insight = SimpleNamespace(
        insight_id="i_done",
        type=SimpleNamespace(name="IMPROVEMENT"),
        status="REJECTED_BY_USER",
        description="Already handled",
        creation_timestamp=100,
        metadata={},
    )
    learning_agent = SimpleNamespace(
        insights=[insight],
        _save_insights=lambda: None,
    )
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(learning_agent=learning_agent))

    with app.test_client() as client:
        response = client.post('/api/status/reflection-suggestions/i_done/approve')

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False


def test_reflection_suggestion_triage_endpoint_returns_404_for_unknown_id(monkeypatch):
    app = _build_test_app()

    learning_agent = SimpleNamespace(insights=[], _save_insights=lambda: None)
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(learning_agent=learning_agent))

    with app.test_client() as client:
        response = client.post('/api/status/reflection-suggestions/missing/reject')

    assert response.status_code == 404
    payload = response.get_json()
    assert payload["success"] is False

def test_background_cadence_endpoint_returns_runtime_values(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "config_manager",
        SimpleNamespace(
            get_all_settings=lambda: {
                "ENABLE_DREAM_MODE": True,
                "ALLOW_DREAMER": True,
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
        "dreamer_allowed": True,
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
    assert payload["returned_count"] == 1
    assert payload["has_more"] is False
    assert payload["next_offset"] is None
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
    assert payload["returned_count"] == 0
    assert payload["has_more"] is False
    assert payload["next_offset"] is None
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
    assert payload["distincts"]["worker_profile"] == ["coder_worker", "critic_worker"]
    assert payload["distincts"]["state"] == ["FAILED", "RUNNING", "WAITING_FOR_REVIEW"]


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
    assert payload["distincts"] == {
        "worker_profile": [],
        "state": [],
        "scope_type": [],
        "source": [],
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



def test_delegation_topology_endpoint_can_omit_items_payload(monkeypatch):
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
        response = client.get('/api/status/delegation-topology?include_items=false&breakdown_scope=filtered')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["include_items"] is False
    assert payload["items"] == []
    assert payload["breakdowns"]["by_worker"]["coder_worker"] == 1
    assert payload["breakdowns"]["by_worker"]["critic_worker"] == 1


def test_delegation_topology_endpoint_include_items_defaults_true(monkeypatch):
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
            ]
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["include_items"] is True
    assert len(payload["items"]) == 1



def test_delegation_topology_endpoint_can_disable_breakdowns_and_distincts(monkeypatch):
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
        response = client.get('/api/status/delegation-topology?include_breakdowns=false&include_distincts=false')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["include_breakdowns"] is False
    assert payload["include_distincts"] is False
    assert payload["breakdowns"] == {}
    assert payload["distincts"] == {}


def test_delegation_topology_endpoint_pagination_metadata_when_no_more(monkeypatch):
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
            ]
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/delegation-topology?limit=5&offset=0')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["returned_count"] == 1
    assert payload["has_more"] is False
    assert payload["next_offset"] is None



def test_agent_scope_audit_endpoint_returns_503_when_orchestrator_missing(monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(app_globals, "orchestrator", None)

    with app.test_client() as client:
        response = client.get('/api/status/agent-scope-audit')

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["success"] is False


def test_agent_scope_audit_endpoint_reports_compliance_and_violations(monkeypatch):
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
                {
                    "task_id": "task_ok",
                    "worker_profile": "coder_worker",
                    "scope_type": "session",
                    "capability_profile": "workspace_code_generation",
                    "retention_policy": "drop_task_memory_on_completion_keep_artifacts",
                },
                {
                    "task_id": "task_bad_scope",
                    "worker_profile": "critic_worker",
                    "scope_type": "org",
                    "capability_profile": "review_only",
                    "retention_policy": "keep_summary_only",
                },
                {
                    "task_id": "task_missing_fields",
                    "worker_profile": "ops_worker",
                    "scope_type": "user",
                    "capability_profile": "",
                    "retention_policy": "",
                },
            ]
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/agent-scope-audit')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["schema_version"] == 1
    audit = payload["audit"]
    assert audit["total_edges"] == 3
    assert audit["compliant_edges"] == 1
    assert audit["violation_count"] == 2
    assert abs(audit["compliance_rate"] - (1 / 3)) < 0.0001
    assert audit["distribution"]["by_scope"]["session"] == 1
    assert audit["distribution"]["by_scope"]["user"] == 1
    assert audit["distribution"]["by_scope"]["other"] == 1
    assert audit["contract_version"] == "2026-03-r3-v1"
    assert "review_only" in audit["allowed_capability_profiles"]
    assert "keep_summary_only" in audit["allowed_retention_policies"]
    assert any(v["task_id"] == "task_bad_scope" and v["invalid_scope_type"] == "org" for v in audit["violations_preview"])
    assert any(v["task_id"] == "task_missing_fields" and "capability_profile" in v["missing_fields"] for v in audit["violations_preview"])



def test_agent_policy_matrix_endpoint_returns_503_when_orchestrator_missing(monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(app_globals, "orchestrator", None)

    with app.test_client() as client:
        response = client.get('/api/status/agent-policy-matrix')

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["success"] is False


def test_agent_policy_matrix_endpoint_reports_worker_policy_consistency(monkeypatch):
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
                {
                    "task_id": "task_a",
                    "worker_profile": "coder_worker",
                    "scope_type": "session",
                    "capability_profile": "workspace_code_generation",
                    "retention_policy": "drop_task_memory_on_completion_keep_artifacts",
                    "source": "chat_delegate",
                },
                {
                    "task_id": "task_b",
                    "worker_profile": "coder_worker",
                    "scope_type": "session",
                    "capability_profile": "workspace_code_generation",
                    "retention_policy": "drop_task_memory_on_completion_keep_artifacts",
                    "source": "planner",
                },
                {
                    "task_id": "task_c",
                    "worker_profile": "critic_worker",
                    "scope_type": "user",
                    "capability_profile": "review_only",
                    "retention_policy": "keep_summary_only",
                    "source": "chat_delegate",
                },
                {
                    "task_id": "task_d",
                    "worker_profile": "critic_worker",
                    "scope_type": "session",
                    "capability_profile": "review_only",
                    "retention_policy": "keep_summary_only",
                    "source": "chat_delegate",
                },
            ]
        },
    )

    with app.test_client() as client:
        response = client.get('/api/status/agent-policy-matrix')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["schema_version"] == 1

    matrix = payload["matrix"]
    assert matrix["workers_total"] == 2
    assert matrix["contract_version"] == "2026-03-r3-v1"
    assert "worker_profile" in matrix["required_fields"]

    coder = next(row for row in matrix["rows"] if row["worker_profile"] == "coder_worker")
    assert coder["count"] == 2
    assert coder["policy_consistency"]["single_scope_type"] is True
    assert coder["policy_consistency"]["single_capability_profile"] is True
    assert coder["policy_consistency"]["single_retention_policy"] is True

    critic = next(row for row in matrix["rows"] if row["worker_profile"] == "critic_worker")
    assert critic["count"] == 2
    assert critic["policy_consistency"]["single_scope_type"] is False
    assert critic["policy_consistency"]["single_capability_profile"] is True
    assert critic["policy_consistency"]["single_retention_policy"] is True





def test_specialist_templates_endpoint_lists_approved_templates(monkeypatch):
    app = _build_test_app()

    with app.test_client() as client:
        response = client.get('/api/status/specialist-templates')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["count"] >= 1
    first = payload["items"][0]
    assert "template_id" in first
    assert "scope_type" in first
    assert "capability_profile" in first
    assert "retention_policy" in first


def test_specialist_spawns_endpoint_creates_ephemeral_task_from_template(monkeypatch):
    app = _build_test_app()

    captured = {}

    def fake_add_task(description, task_type, details=None, session_id=None, related_item_id=None):
        captured["description"] = description
        captured["task_type"] = task_type
        captured["details"] = details
        captured["session_id"] = session_id
        return SimpleNamespace(task_id="task_specialist_1")

    monkeypatch.setattr(app_globals, "task_manager", SimpleNamespace(add_task=fake_add_task), raising=False)

    with app.test_client() as client:
        response = client.post(
            '/api/status/specialist-spawns',
            json={
                "template_id": "code_reviewer_v1",
                "task_description": "Review the latest parser changes for edge cases.",
                "session_id": "s123",
                "source_insight_id": "i123",
                "metadata": {"requested_by": "operator"},
            },
        )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["task_id"] == "task_specialist_1"
    assert payload["template"]["template_id"] == "code_reviewer_v1"
    assert captured["session_id"] == "s123"
    assert captured["details"]["source"] == "specialist_template"
    assert captured["details"]["template_id"] == "code_reviewer_v1"
    assert captured["details"]["scope_type"] == "session"


def test_specialist_spawns_endpoint_rejects_unknown_template(monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(app_globals, "task_manager", SimpleNamespace(add_task=lambda **kwargs: None), raising=False)

    with app.test_client() as client:
        response = client.post('/api/status/specialist-spawns', json={"template_id": "unknown", "task_description": "x"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False


def test_specialist_spawns_endpoint_returns_503_when_task_manager_unavailable(monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(app_globals, "task_manager", None, raising=False)
    monkeypatch.setattr(app_globals, "orchestrator", None)

    with app.test_client() as client:
        response = client.post('/api/status/specialist-spawns', json={"template_id": "code_reviewer_v1", "task_description": "x"})

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["success"] is False

def test_work_inbox_endpoint_lists_state_filtered_items(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "chat_manager",
        SimpleNamespace(
            list_user_notices=lambda scope, include_read=False, limit=20, state=None: [
                {"id": "n2", "notice_state": state or "open", "read": False}
            ],
        ),
    )

    with app.test_client() as client:
        response = client.get('/api/work-inbox?state=open&limit=10')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["state"] == "open"
    assert payload["count"] == 1
    assert payload["items"][0]["id"] == "n2"




def test_work_inbox_endpoint_resolves_identity_scope(monkeypatch):
    app = _build_test_app()

    captured = {}

    def fake_list(scope, include_read=False, limit=20, state=None):
        captured["scope"] = scope
        return []

    monkeypatch.setattr(
        app_globals,
        "chat_manager",
        SimpleNamespace(list_user_notices=fake_list),
    )

    with app.test_client() as client:
        response = client.get('/api/work-inbox?platform=telegram&user_id=u7&chat_id=c7')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["scope"] == "telegram:u7:c7"
    assert captured["scope"] == "telegram:u7:c7"


def test_work_inbox_endpoint_rejects_partial_identity_payload(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "chat_manager",
        SimpleNamespace(list_user_notices=lambda scope, include_read=False, limit=20, state=None: []),
    )

    with app.test_client() as client:
        response = client.get('/api/work-inbox?platform=telegram&user_id=u7')

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False


def test_work_inbox_set_state_endpoint_uses_identity_scope_from_query(monkeypatch):
    app = _build_test_app()

    captured = {}

    def fake_update(scope, notice_id, action, snooze_seconds=None):
        captured["scope"] = scope
        return {"id": notice_id, "notice_state": "acknowledged"}

    monkeypatch.setattr(
        app_globals,
        "chat_manager",
        SimpleNamespace(update_user_notice_state=fake_update),
    )

    with app.test_client() as client:
        response = client.post('/api/work-inbox/n1/state?identity_key=telegram:u7:c7', json={"action": "ack"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["scope"] == "telegram:u7:c7"
    assert captured["scope"] == "telegram:u7:c7"

def test_work_inbox_set_state_endpoint_updates_notice(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "chat_manager",
        SimpleNamespace(
            update_user_notice_state=lambda scope, notice_id, action, snooze_seconds=None: {
                "id": notice_id,
                "read": True,
                "notice_state": "resolved",
                "updated_at": 123,
            }
        ),
    )

    with app.test_client() as client:
        response = client.post('/api/work-inbox/n1/state', json={"action": "resolve"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["notice"]["id"] == "n1"
    assert payload["notice"]["notice_state"] == "resolved"


def test_work_inbox_set_state_endpoint_validates_action(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "chat_manager",
        SimpleNamespace(update_user_notice_state=lambda scope, notice_id, action, snooze_seconds=None: None),
    )

    with app.test_client() as client:
        response = client.post('/api/work-inbox/n1/state', json={"action": "noop"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False


def test_work_inbox_set_state_endpoint_returns_404_when_missing(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        app_globals,
        "chat_manager",
        SimpleNamespace(update_user_notice_state=lambda scope, notice_id, action, snooze_seconds=None: None),
    )

    with app.test_client() as client:
        response = client.post('/api/work-inbox/n404/state', json={"action": "ack"})

    assert response.status_code == 404
    payload = response.get_json()
    assert payload["success"] is False


def test_reflection_suggestions_skips_items_without_insight_id(monkeypatch):
    app = _build_test_app()

    insights = [
        SimpleNamespace(
            insight_id="",
            type=SimpleNamespace(name="IMPROVEMENT"),
            status="NEW",
            description="missing id should be ignored",
            creation_timestamp=100,
        ),
        SimpleNamespace(
            insight_id="i_valid",
            type=SimpleNamespace(name="IMPROVEMENT"),
            status="NEW",
            description="valid",
            creation_timestamp=200,
        ),
    ]

    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(learning_agent=SimpleNamespace(insights=insights)))

    import ai_assistant.core.suggestion_manager as sm
    monkeypatch.setattr(sm, "list_suggestions", lambda create_dummy=True: [])

    with app.test_client() as client:
        response = client.get('/api/status/reflection-suggestions')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    assert payload["items"][0]["insight_id"] == "i_valid"



def test_specialist_spawns_endpoint_accepts_non_json_request_body(monkeypatch):
    app = _build_test_app()

    captured = {}

    def fake_add_task(description, task_type, details=None, session_id=None, related_item_id=None):
        captured["description"] = description
        captured["task_type"] = task_type
        return SimpleNamespace(task_id="task_specialist_text_body")

    monkeypatch.setattr(app_globals, "task_manager", SimpleNamespace(add_task=fake_add_task), raising=False)

    with app.test_client() as client:
        response = client.post(
            '/api/status/specialist-spawns',
            data='not-json',
            content_type='text/plain',
        )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "template_id is required" in payload["error"]


def test_dynamic_specialist_proposal_create_requires_provenance(monkeypatch, tmp_path):
    app = _build_test_app()
    monkeypatch.setattr(approvals, "_DYNAMIC_SPECIALIST_STORE_PATH", str(tmp_path / "dynamic_specialist_proposals.json"))

    payload = {
        "profile": {
            "label": "Dynamic Reviewer",
            "worker_profile": "task_reviewer_worker",
            "scope_type": "session",
            "capability_profile": "review_only",
            "retention_policy": "keep_summary_only",
            "description": "Dynamic specialist profile",
        },
        "provenance": {
            "requested_by": "operator",
            "rationale": "Need specialized review",
            "rollback_plan": "Disable profile and archive tasks",
        },
    }

    with app.test_client() as client:
        response = client.post('/api/status/dynamic-specialist-proposals', json=payload)

    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert "provenance.retirement_policy is required" in body["error"]



def test_dynamic_specialist_proposal_lifecycle_create_and_approve(monkeypatch, tmp_path):
    app = _build_test_app()
    store_path = tmp_path / "dynamic_specialist_proposals.json"
    monkeypatch.setattr(approvals, "_DYNAMIC_SPECIALIST_STORE_PATH", str(store_path))

    payload = {
        "profile": {
            "label": "Dynamic Reviewer",
            "worker_profile": "task_reviewer_worker",
            "scope_type": "session",
            "capability_profile": "review_only",
            "retention_policy": "keep_summary_only",
            "description": "Dynamic specialist profile",
        },
        "provenance": {
            "requested_by": "operator:alice",
            "rationale": "Need domain-specific review",
            "rollback_plan": "Revoke profile and stop new tasks",
            "retirement_policy": "Retire after 7 days of inactivity",
        },
    }

    with app.test_client() as client:
        create_response = client.post('/api/status/dynamic-specialist-proposals', json=payload)
        assert create_response.status_code == 202
        proposal = create_response.get_json()["proposal"]
        assert proposal["status"] == "PENDING_REVIEW"

        approve_response = client.post(
            f"/api/status/dynamic-specialist-proposals/{proposal['proposal_id']}/approve",
            json={"reviewed_by": "reviewer:bob", "review_notes": "Looks safe for controlled rollout"},
        )

        assert approve_response.status_code == 200
        approved = approve_response.get_json()
        assert approved["success"] is True
        assert approved["auto_spawned"] is False
        assert approved["proposal"]["status"] == "APPROVED"

        list_response = client.get('/api/status/dynamic-specialist-proposals?status=APPROVED')
        assert list_response.status_code == 200
        listed = list_response.get_json()
        assert listed["count"] == 1
        assert listed["items"][0]["proposal_id"] == proposal["proposal_id"]
        assert listed["audit_trail_count"] >= 2



def test_dynamic_specialist_proposal_reject_requires_reason(monkeypatch, tmp_path):
    app = _build_test_app()
    monkeypatch.setattr(approvals, "_DYNAMIC_SPECIALIST_STORE_PATH", str(tmp_path / "dynamic_specialist_proposals.json"))

    payload = {
        "profile": {
            "label": "Dynamic Ops Assistant",
            "worker_profile": "ops_assistant_worker",
            "scope_type": "user",
            "capability_profile": "ops_diagnostics",
            "retention_policy": "retain_user_profile_with_provenance",
            "description": "Dynamic ops specialist",
        },
        "provenance": {
            "requested_by": "operator",
            "rationale": "Need incident triage",
            "rollback_plan": "Disable profile immediately if unsafe",
            "retirement_policy": "Retire after incident resolution",
        },
    }

    with app.test_client() as client:
        create_response = client.post('/api/status/dynamic-specialist-proposals', json=payload)
        proposal_id = create_response.get_json()["proposal"]["proposal_id"]

        reject_response = client.post(
            f"/api/status/dynamic-specialist-proposals/{proposal_id}/reject",
            json={"reviewed_by": "reviewer"},
        )

    assert reject_response.status_code == 400
    body = reject_response.get_json()
    assert body["success"] is False
    assert "rejection_reason is required" in body["error"]



def test_dynamic_specialist_proposal_approve_reject_non_pending_fails(monkeypatch, tmp_path):
    app = _build_test_app()
    monkeypatch.setattr(approvals, "_DYNAMIC_SPECIALIST_STORE_PATH", str(tmp_path / "dynamic_specialist_proposals.json"))

    payload = {
        "profile": {
            "label": "Dynamic Reviewer",
            "worker_profile": "task_reviewer_worker",
            "scope_type": "session",
            "capability_profile": "review_only",
            "retention_policy": "keep_summary_only",
            "description": "Dynamic specialist profile",
        },
        "provenance": {
            "requested_by": "operator",
            "rationale": "Need review",
            "rollback_plan": "Disable profile",
            "retirement_policy": "retire quickly",
        },
    }

    with app.test_client() as client:
        create_response = client.post('/api/status/dynamic-specialist-proposals', json=payload)
        proposal_id = create_response.get_json()["proposal"]["proposal_id"]

        first_reject = client.post(
            f"/api/status/dynamic-specialist-proposals/{proposal_id}/reject",
            json={"reviewed_by": "reviewer", "rejection_reason": "Insufficient constraints"},
        )
        assert first_reject.status_code == 200

        second_approve = client.post(
            f"/api/status/dynamic-specialist-proposals/{proposal_id}/approve",
            json={"reviewed_by": "reviewer2"},
        )

    assert second_approve.status_code == 400
    body = second_approve.get_json()
    assert body["success"] is False
    assert "not pending review" in body["error"]



def test_dynamic_specialist_proposal_create_rejects_invalid_contract_fields(monkeypatch, tmp_path):
    app = _build_test_app()
    monkeypatch.setattr(approvals, "_DYNAMIC_SPECIALIST_STORE_PATH", str(tmp_path / "dynamic_specialist_proposals.json"))

    payload = {
        "profile": {
            "label": "Dynamic Reviewer",
            "worker_profile": "task_reviewer_worker",
            "scope_type": "org",
            "capability_profile": "review_only",
            "retention_policy": "keep_summary_only",
            "description": "Dynamic specialist profile",
        },
        "provenance": {
            "requested_by": "operator",
            "rationale": "Need specialized review",
            "rollback_plan": "Disable profile and archive tasks",
            "retirement_policy": "Retire after issue fixed",
        },
    }

    with app.test_client() as client:
        response = client.post('/api/status/dynamic-specialist-proposals', json=payload)

    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert "scope_type" in body["error"]


def test_reflection_spawn_specialist_endpoint_returns_503_when_task_manager_unavailable(monkeypatch):
    app = _build_test_app()

    insight = SimpleNamespace(
        insight_id="i_spawn_tm_missing",
        type=SimpleNamespace(name="TOOL_BUG_SUSPECTED"),
        status="NEW",
        description="Need specialist",
        creation_timestamp=100,
        metadata={},
    )
    monkeypatch.setattr(app_globals, "task_manager", None, raising=False)
    monkeypatch.setattr(
        app_globals,
        "orchestrator",
        SimpleNamespace(learning_agent=SimpleNamespace(insights=[insight], _save_insights=lambda: None), task_manager=None),
    )

    with app.test_client() as client:
        response = client.post('/api/status/reflection-suggestions/i_spawn_tm_missing/spawn-specialist', json={})

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["success"] is False
    assert "Task manager unavailable" in payload["error"]



def test_health_audit_reports_partial_optional_dependency_availability(monkeypatch):
    app = _build_test_app()

    monkeypatch.setattr(
        approvals,
        "_is_optional_dependency_available",
        lambda name: {"playwright": False, "chromadb": True, "pyaudio": False}.get(name, False),
    )
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(task_manager=SimpleNamespace()))
    monkeypatch.setattr(app_globals, "chat_manager", SimpleNamespace())

    with app.test_client() as client:
        response = client.get('/api/status/health-audit')

    assert response.status_code == 200
    payload = response.get_json()
    checks = {item["key"]: item for item in payload["health"]["checks"]}
    assert checks["playwright"]["ok"] is False
    assert checks["chromadb"]["ok"] is True
    assert checks["pyaudio"]["ok"] is False
    assert payload["health"]["failing_count"] >= 2


def test_dynamic_specialist_list_includes_operator_lifecycle_view(monkeypatch, tmp_path):
    app = _build_test_app()
    monkeypatch.setattr(approvals, "_DYNAMIC_SPECIALIST_STORE_PATH", str(tmp_path / "dynamic_specialist_proposals.json"))

    payload = {
        "profile": {
            "label": "Dynamic Reviewer",
            "worker_profile": "task_reviewer_worker",
            "scope_type": "session",
            "capability_profile": "review_only",
            "retention_policy": "keep_summary_only",
            "description": "Dynamic specialist profile",
        },
        "provenance": {
            "requested_by": "operator:carol",
            "rationale": "Need specialist for flaky regression triage",
            "rollback_plan": "Disable profile and stop assignment",
            "retirement_policy": "Retire after 14 days idle",
        },
    }

    with app.test_client() as client:
        create_response = client.post('/api/status/dynamic-specialist-proposals', json=payload)
        assert create_response.status_code == 202
        proposal_id = create_response.get_json()["proposal"]["proposal_id"]

        approve_response = client.post(
            f"/api/status/dynamic-specialist-proposals/{proposal_id}/approve",
            json={"reviewed_by": "reviewer:dan", "review_notes": "approved for controlled rollout"},
        )
        assert approve_response.status_code == 200

        list_response = client.get('/api/status/dynamic-specialist-proposals?status=APPROVED')

    assert list_response.status_code == 200
    body = list_response.get_json()
    assert body["count"] == 1
    lifecycle = body["items"][0]["operator_lifecycle"]
    assert lifecycle["why_this_specialist_exists"] == "Need specialist for flaky regression triage"
    assert lifecycle["retirement_policy"] == "Retire after 14 days idle"
    assert lifecycle["rollback_plan"] == "Disable profile and stop assignment"
    assert lifecycle["review_status"] == "APPROVED"
    assert lifecycle["reviewed_by"] == "reviewer:dan"
    assert lifecycle["audit_event_count"] >= 2
    assert lifecycle["last_audit_event_type"] == "PROPOSAL_APPROVED"


def test_slo_dashboard_returns_503_when_orchestrator_missing(monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(app_globals, "orchestrator", None)

    with app.test_client() as client:
        response = client.get('/api/status/slo-dashboard')

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["success"] is False


def test_slo_dashboard_reports_measured_and_gap_metrics(monkeypatch):
    app = _build_test_app()

    active_tasks = [SimpleNamespace(task_id="t1"), SimpleNamespace(task_id="t2"), SimpleNamespace(task_id="t3"), SimpleNamespace(task_id="t4")]
    fake_task_manager = SimpleNamespace(list_active_tasks=lambda: active_tasks)
    fake_orchestrator = SimpleNamespace(task_manager=fake_task_manager)

    monkeypatch.setattr(app_globals, "orchestrator", fake_orchestrator)
    monkeypatch.setattr(app_globals, "chat_manager", SimpleNamespace(
        list_user_notices=lambda scope, include_read=False, limit=200: (
            [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}] if not include_read else [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}, {"id": "n4"}]
        )
    ))

    topology = [
        {"task_id": "t1", "state": "FAILED"},
        {"task_id": "t2", "state": "WAITING_FOR_REVIEW"},
        {"task_id": "t3", "state": "RUNNING"},
        {"task_id": "t4", "state": "RUNNING"},
    ]
    monkeypatch.setattr(approvals, "get_status_snapshot", lambda active_tasks_count, active_tasks=None: {"delegation_topology": topology})
    monkeypatch.setattr(approvals, "_is_optional_dependency_available", lambda name: {"playwright": True, "chromadb": False, "pyaudio": False}.get(name, False))

    with app.test_client() as client:
        response = client.get('/api/status/slo-dashboard')

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    slo = body["slo"]
    assert slo["snapshot"]["active_delegated_tasks"] == 4
    assert slo["snapshot"]["by_state"]["FAILED"] == 1
    assert slo["snapshot"]["work_inbox_unread"] == 3

    metrics = {item["name"]: item for item in slo["metrics"]}
    assert metrics["delegation_failed_active_rate_pct"]["value"] == 25.0
    assert metrics["delegation_failed_active_rate_pct"]["meeting_target"] is False
    assert metrics["delegation_review_backlog_rate_pct"]["value"] == 25.0
    assert metrics["delegation_review_backlog_rate_pct"]["meeting_target"] is True
    assert metrics["optional_dependency_availability_pct"]["value"] == 33.33
    assert metrics["optional_dependency_availability_pct"]["meeting_target"] is False
    assert metrics["delegated_completion_latency_p50_ms"]["data_status"] == "not_instrumented"
    assert metrics["delegated_completion_latency_p50_ms"]["meeting_target"] is None
    assert metrics["failed_delegation_recovery_rate_pct"]["data_status"] == "not_instrumented"
    assert slo["coverage"]["measured_metrics"] == 4
    assert slo["coverage"]["not_instrumented_metrics"] == 4



def test_slo_dashboard_uses_archived_tasks_for_latency_and_recovery(monkeypatch):
    app = _build_test_app()

    class _Status:
        def __init__(self, name):
            self.name = name

    class _Task:
        def __init__(self, created_at, terminal_at, recovered=False, retry_attempts=0, diagnosis_minutes=0):
            self.status = _Status("COMPLETED_SUCCESSFULLY")
            self.created_at = created_at
            self.last_updated_at = terminal_at
            failed_at = created_at + timedelta(minutes=1)
            diagnosed_at = failed_at + timedelta(minutes=diagnosis_minutes)
            self.details = {"lifecycle": {
                "terminal_at": terminal_at.isoformat(),
                "recovered_after_failure": recovered,
                "retry_attempts": retry_attempts,
                "first_failed_at": failed_at.isoformat(),
                "diagnosed_at": diagnosed_at.isoformat(),
            }}

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    archived = [
        _Task(now - timedelta(seconds=30), now - timedelta(seconds=10), recovered=True, retry_attempts=1, diagnosis_minutes=4),
        _Task(now - timedelta(seconds=20), now - timedelta(seconds=5), recovered=False, retry_attempts=0, diagnosis_minutes=2),
        _Task(now - timedelta(seconds=10), now, recovered=True, retry_attempts=2, diagnosis_minutes=6),
    ]

    active_tasks = [SimpleNamespace(task_id="a")]
    fake_task_manager = SimpleNamespace(
        list_active_tasks=lambda: active_tasks,
        list_archived_tasks=lambda limit=500: archived,
    )
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(task_manager=fake_task_manager))
    monkeypatch.setattr(app_globals, "chat_manager", SimpleNamespace(list_user_notices=lambda scope, include_read=False, limit=200: []))
    monkeypatch.setattr(approvals, "get_status_snapshot", lambda active_tasks_count, active_tasks=None: {"delegation_topology": []})
    monkeypatch.setattr(approvals, "_is_optional_dependency_available", lambda name: True)

    with app.test_client() as client:
        response = client.get('/api/status/slo-dashboard')

    assert response.status_code == 200
    payload = response.get_json()["slo"]
    metrics = {item["name"]: item for item in payload["metrics"]}
    assert metrics["delegated_completion_latency_p50_ms"]["data_status"] == "measured"
    assert metrics["delegated_completion_latency_p50_ms"]["value"] == 15000
    assert metrics["failed_delegation_recovery_rate_pct"]["data_status"] == "measured"
    assert metrics["failed_delegation_recovery_rate_pct"]["value"] == 66.67
    assert metrics["manual_retry_rate_pct"]["data_status"] == "measured"
    assert metrics["manual_retry_rate_pct"]["value"] == 66.67
    assert metrics["mean_time_to_diagnose_minutes"]["data_status"] == "measured"
    assert metrics["mean_time_to_diagnose_minutes"]["value"] == 4.0



def test_slo_trends_returns_503_when_orchestrator_missing(monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(app_globals, "orchestrator", None)

    with app.test_client() as client:
        response = client.get('/api/status/slo-trends')

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["success"] is False


def test_slo_trends_reports_primary_and_compare_windows(monkeypatch):
    app = _build_test_app()

    class _Status:
        def __init__(self, name):
            self.name = name

    class _Task:
        def __init__(self, created_at, terminal_at, retry_attempts=0, recovered=False, diagnosis_minutes=0):
            self.status = _Status("COMPLETED_SUCCESSFULLY")
            self.created_at = created_at
            self.last_updated_at = terminal_at
            failed_at = created_at + timedelta(minutes=1)
            diagnosed_at = failed_at + timedelta(minutes=diagnosis_minutes)
            self.details = {
                "worker_profile": "coder_worker" if retry_attempts else "critic_worker",
                "scope_type": "session" if recovered else "user",
                "source": "chat_delegate" if retry_attempts else "planner",
                "lifecycle": {
                    "terminal_at": terminal_at.isoformat(),
                    "retry_attempts": retry_attempts,
                    "retry_events": ([{"at": terminal_at.isoformat(), "source": "manual"}] if retry_attempts else []),
                    "recovered_after_failure": recovered,
                    "first_failed_at": failed_at.isoformat(),
                    "diagnosed_at": diagnosed_at.isoformat(),
                }
            }

    now = datetime.now(timezone.utc)
    archived = [
        _Task(now - timedelta(hours=2, minutes=20), now - timedelta(hours=2), retry_attempts=1, recovered=True, diagnosis_minutes=4),
        _Task(now - timedelta(hours=1, minutes=30), now - timedelta(hours=1), retry_attempts=0, recovered=False, diagnosis_minutes=6),
        _Task(now - timedelta(days=3, minutes=40), now - timedelta(days=3), retry_attempts=1, recovered=True, diagnosis_minutes=2),
    ]

    fake_task_manager = SimpleNamespace(list_archived_tasks=lambda limit=5000: archived)
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(task_manager=fake_task_manager))

    with app.test_client() as client:
        response = client.get('/api/status/slo-trends?window_hours=24&compare_window_hours=168')

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True

    primary = body["windows"]["primary"]
    compare = body["windows"]["compare"]

    assert primary["window_hours"] == 24
    assert compare["window_hours"] == 168
    assert primary["completed_tasks_considered"] == 2
    assert compare["completed_tasks_considered"] == 3

    p_metrics = primary["metrics"]
    assert p_metrics["delegated_completion_latency_p50_ms"] == 1800000
    assert p_metrics["manual_retry_rate_pct"] == 50.0
    assert p_metrics["manual_retry_source_rate_pct"] == 50.0
    assert p_metrics["automatic_retry_source_rate_pct"] == 0.0
    assert p_metrics["failed_delegation_recovery_rate_pct"] == 50.0
    assert p_metrics["mean_time_to_diagnose_minutes"] == 6.0

    # Trend analysis (primary vs compare)
    assert body["analysis"]["deltas"]["manual_retry_rate_pct"] == -16.67
    assert body["analysis"]["breaches"]["manual_retry_rate_pct"]["primary_breached"] is True
    assert body["analysis"]["breaches"]["manual_retry_rate_pct"]["compare_breached"] is True
    assert body["analysis"]["breaches"]["manual_retry_rate_pct"]["breach_streak_windows"] == 2
    assert body["analysis"]["breaches"]["manual_retry_rate_pct"]["severity"] == "critical"
    assert body["analysis"]["overall_severity"] == "critical"

    # Contributor slices
    failed_by_worker = body["windows"]["primary"]["contributors"]["failed_tasks"]["by_worker"]
    retries_by_source = body["windows"]["primary"]["contributors"]["retries"]["by_source"]
    assert failed_by_worker[0]["key"] in {"coder_worker", "critic_worker"}
    assert retries_by_source[0]["key"] == "chat_delegate"
