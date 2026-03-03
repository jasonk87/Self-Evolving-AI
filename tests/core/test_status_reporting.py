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

    snapshot = status_reporting.get_status_snapshot(active_tasks_count=4)

    assert snapshot["tools_total"] == 3
    assert snapshot["tools_by_type"] == {"dynamic": 2, "system": 1}
    assert snapshot["projects_summary"] == "projects-ok"
    assert snapshot["suggestions_summary"] == "suggestions-ok"
    assert snapshot["background_tasks"] == 4
    assert snapshot["debug_mode_enabled"] is True
    assert snapshot["autonomous_learning_enabled"] is True


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
