from types import SimpleNamespace

from ai_assistant.custom_tools.awareness_tools import get_self_awareness_info_and_converse
from ai_assistant.custom_tools import system_tools
from ai_assistant.core.orchestrator import DynamicOrchestrator


class _FakeToolSystem:
    def list_tools_with_sources(self):
        return {
            "alpha_tool": {"type": "custom", "module_path": "x", "description": "Alpha."},
            "beta_tool": {"type": "system", "module_path": "y", "description": "Beta."},
        }


def test_self_awareness_tool_accepts_query_alias_without_typeerror(monkeypatch):
    monkeypatch.setattr(
        "ai_assistant.custom_tools.awareness_tools.get_system_status_summary",
        lambda **kwargs: "System Status Summary:\nAll good.",
    )
    monkeypatch.setattr("ai_assistant.custom_tools.awareness_tools.load_learned_facts", lambda: [])

    response = get_self_awareness_info_and_converse(query="Summarize capabilities")

    assert "Self-Awareness Report" in response
    assert "Summarize capabilities" in response


def test_list_available_tools_uses_singleton_tool_system(monkeypatch):
    monkeypatch.setattr("ai_assistant.tools.tool_system.tool_system_instance", _FakeToolSystem())

    output = system_tools.list_available_tools()

    assert "Available Tools:" in output
    assert "alpha_tool" in output
    assert "beta_tool" in output


def test_orchestrator_ui_feedback_task_details_include_worker_profile():
    orch = DynamicOrchestrator(
        planner=SimpleNamespace(),
        executor=SimpleNamespace(),
        learning_agent=SimpleNamespace(),
        action_executor=SimpleNamespace(),
    )

    details = orch._build_ui_feedback_task_details()

    assert details["worker_profile"] == "ops_assistant_worker"
    assert details["scope_type"] == "session"
    assert details["retention_policy"] == "keep_summary_only"
