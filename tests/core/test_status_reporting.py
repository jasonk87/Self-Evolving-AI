from types import SimpleNamespace

from unittest.mock import MagicMock

from ai_assistant.core import status_reporting


def test_get_status_snapshot_structure(monkeypatch):
    monkeypatch.setattr(status_reporting, "get_projects_status", lambda: "projects-ok")
    monkeypatch.setattr(status_reporting, "get_suggestions_status", lambda: "suggestions-ok")
    monkeypatch.setattr(status_reporting, "is_debug_mode", lambda: True)
    monkeypatch.setattr(status_reporting, "AUTONOMOUS_LEARNING_ENABLED", True)

    tool_system = MagicMock()
    tool_system.list_tools_with_details.return_value = {
        "t1": {"type": "system"},
        "t2": {"type": "dynamic"},
        "t3": {"type": "dynamic"},
    }
    monkeypatch.setattr(status_reporting, "tool_system_instance", tool_system)

    snapshot = status_reporting.get_status_snapshot(active_tasks_count=4, active_tasks=[
        SimpleNamespace(task_type=SimpleNamespace(name="EPHEMERAL_AGENT_TASK"), details={"source": "chat_delegate"}),
        SimpleNamespace(task_type=SimpleNamespace(name="EPHEMERAL_AGENT_TASK"), details={"source": "other"}),
    ])

    assert snapshot["tools_total"] == 3
    assert snapshot["tools_by_type"] == {"dynamic": 2, "system": 1}
    assert snapshot["projects_summary"] == "projects-ok"
    assert snapshot["suggestions_summary"] == "suggestions-ok"
    assert snapshot["background_tasks"] == 4
    assert snapshot["debug_mode_enabled"] is True
    assert snapshot["autonomous_learning_enabled"] is True

    assert snapshot["delegation_active_tasks"] == 2
    assert snapshot["delegation_chat_delegate_active_tasks"] == 1
    assert snapshot["delegation_by_worker"] == {"coder_worker": 2}
    assert snapshot["delegation_by_scope"] == {"session": 2}
    assert snapshot["delegation_by_state"] == {"UNKNOWN": 2}
    assert len(snapshot["delegation_topology"]) == 2
    assert snapshot["delegation_topology"][0]["worker_profile"] == "coder_worker"
    assert snapshot["delegation_topology"][0]["capability_profile"] == "unknown"
    assert snapshot["delegation_topology"][0]["retention_policy"] == "unknown"



def test_get_tools_status_fallback_to_registry(monkeypatch):
    tool_system = MagicMock()
    if hasattr(tool_system, "list_tools_with_details"):
        del tool_system.list_tools_with_details
    tool_system._tool_registry = {
        "a": {"type": "system"},
        "b": {"type": "system"},
    }
    monkeypatch.setattr(status_reporting, "tool_system_instance", tool_system)

    text = status_reporting.get_tools_status()
    assert "Total Tools Registered: 2" in text
    assert "Type 'system': 2" in text


def test_get_status_snapshot_supports_dict_tasks(monkeypatch):
    monkeypatch.setattr(status_reporting, "get_projects_status", lambda: "projects-ok")
    monkeypatch.setattr(status_reporting, "get_suggestions_status", lambda: "suggestions-ok")
    monkeypatch.setattr(status_reporting, "is_debug_mode", lambda: False)
    monkeypatch.setattr(status_reporting, "AUTONOMOUS_LEARNING_ENABLED", False)

    tool_system = MagicMock()
    tool_system.list_tools_with_details.return_value = {}
    monkeypatch.setattr(status_reporting, "tool_system_instance", tool_system)

    snapshot = status_reporting.get_status_snapshot(
        active_tasks_count=2,
        active_tasks=[
            {"task_type": {"name": "EPHEMERAL_AGENT_TASK"}, "status": "GENERATING_CODE", "details": {"source": "chat_delegate", "worker_profile": "coder_worker", "scope_type": "session", "capability_profile": "workspace_code_generation", "retention_policy": "drop_task_memory_on_completion_keep_artifacts"}},
            {"task_type": "EPHEMERAL_AGENT_TASK", "status": "WAITING_FOR_REVIEW", "details": {"source": "other", "worker_profile": "task_reviewer_worker", "scope_type": "user", "capability_profile": "review_only", "retention_policy": "keep_summary_only"}},
        ],
    )

    assert snapshot["delegation_active_tasks"] == 2
    assert snapshot["delegation_chat_delegate_active_tasks"] == 1
    assert snapshot["delegation_by_worker"] == {"coder_worker": 1, "task_reviewer_worker": 1}
    assert snapshot["delegation_by_scope"] == {"session": 1, "user": 1}
    assert snapshot["delegation_by_state"] == {"GENERATING_CODE": 1, "WAITING_FOR_REVIEW": 1}
    assert len(snapshot["delegation_topology"]) == 2
    assert snapshot["delegation_topology"][1]["scope_type"] == "user"
    assert snapshot["delegation_topology"][1]["state"] == "WAITING_FOR_REVIEW"
    assert snapshot["delegation_topology"][0]["capability_profile"] == "workspace_code_generation"
    assert snapshot["delegation_topology"][1]["retention_policy"] == "keep_summary_only"
