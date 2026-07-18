from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_assistant.core.models.state import ExecutionState
from ai_assistant.custom_tools.awareness_tools import get_self_awareness_info_and_converse
from ai_assistant.custom_tools import system_tools
from ai_assistant.core.orchestrator import DynamicOrchestrator
from ai_assistant.core.task_manager import ActiveTaskStatus, ActiveTaskType


class _FakeToolSystem:
    def list_tools_with_sources(self):
        return {
            "alpha_tool": {"type": "custom", "module_path": "x", "description": "Alpha."},
            "beta_tool": {"type": "system", "module_path": "y", "description": "Beta."},
        }


def _make_isolated_orchestrator():
    orch = DynamicOrchestrator(
        planner=SimpleNamespace(),
        executor=SimpleNamespace(),
        learning_agent=SimpleNamespace(),
        action_executor=SimpleNamespace(),
    )
    orch.blocked_tools = {}
    orch.failure_counts = {}
    orch._save_quarantine_state = lambda: None
    return orch


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
    orch = _make_isolated_orchestrator()

    details = orch._build_ui_feedback_task_details()

    assert details["worker_profile"] == "ops_assistant_worker"
    assert details["scope_type"] == "session"
    assert details["retention_policy"] == "keep_summary_only"
    assert details["source"] == "ui_feedback"
    assert details["execution_surface"] == "chat_tool_cycle"


@pytest.mark.asyncio
async def test_orchestrator_chat_tool_cycle_uses_non_code_status_and_image_provenance(monkeypatch):
    class FakeTaskManager:
        def __init__(self):
            self.added = []
            self.updates = []

        def add_task(self, description, task_type, details, session_id):
            task = SimpleNamespace(task_id="task_ui_feedback")
            self.added.append({
                "description": description,
                "task_type": task_type,
                "details": details,
                "session_id": session_id,
            })
            return task

        def update_task_status(self, task_id, status, **kwargs):
            self.updates.append({"task_id": task_id, "status": status, **kwargs})

    class FakeToolSystem:
        def get_tools_description(self):
            return "take_screenshot: captures current screen"

        async def execute_tool(self, tool_name, **kwargs):
            assert tool_name == "take_screenshot"
            return {"success": True, "result": {"images": ["abc123"], "status": "ok"}}

    responses = iter([
        '{"type":"tool_call","name":"take_screenshot","thought":"Need visual state.","params":{}}',
        '{"type":"final_answer","name":null,"params":{"message":"Checked it."}}',
    ])

    async def fake_invoke(*args, **kwargs):
        return next(responses)

    orch = _make_isolated_orchestrator()
    orch.task_manager = FakeTaskManager()
    orch.episodic_manager = SimpleNamespace(
        recall_failures=AsyncMock(return_value=""),
        record_experience=AsyncMock(),
    )
    monkeypatch.setattr("ai_assistant.core.orchestrator.tool_system_instance", FakeToolSystem())
    monkeypatch.setattr("ai_assistant.core.orchestrator.model_router.generate_response", fake_invoke)

    state = ExecutionState(original_user_prompt="How is the agent doing?")

    await orch._execute_universal_cycle_internal(
        state,
        prompt=state.original_user_prompt,
        context="",
        history=[],
        session_id="session-1",
        context_source="USER",
        initial_images=["user-upload-image"],
    )

    assert orch.task_manager.added[0]["task_type"] == ActiveTaskType.AGENT_TOOL_EXECUTION
    assert not any(update["status"] == ActiveTaskStatus.GENERATING_CODE for update in orch.task_manager.updates)
    assert any(update["status"] == ActiveTaskStatus.RUNNING for update in orch.task_manager.updates)

    final_result = state.tool_results[-1]
    assert final_result["action_name"] == "orchestrator_final_answer"
    assert final_result["collected_images"] == [{
        "src": "abc123",
        "source": "take_screenshot",
        "label": "Visual Capture",
    }]


def test_orchestrator_register_tool_failure_activates_circuit_breaker_at_threshold(monkeypatch):
    quarantined = []
    monkeypatch.setattr(
        "ai_assistant.core.orchestrator.mark_tool_quarantined",
        lambda tool_name, **kwargs: quarantined.append((tool_name, kwargs)),
    )
    orch = _make_isolated_orchestrator()

    err = RuntimeError("boom")
    first = orch._register_tool_failure("search_duckduckgo", err, threshold=2)
    second = orch._register_tool_failure("search_duckduckgo", err, threshold=2)

    assert first["activated"] is False
    assert second["activated"] is True
    assert "search_duckduckgo" in orch.blocked_tools
    assert orch.blocked_tools["search_duckduckgo"]["count"] == 2
    assert quarantined == [("search_duckduckgo", {
        "reason": "Repeated identical failure (2x): search_duckduckgo|RuntimeError|boom",
        "error_signature": "search_duckduckgo|RuntimeError|boom",
        "metadata": {"count": 2, "context_data": {}},
    })]


def test_orchestrator_register_tool_failure_separates_distinct_errors():
    orch = _make_isolated_orchestrator()

    orch._register_tool_failure("search_duckduckgo", RuntimeError("first"), threshold=3)
    orch._register_tool_failure("search_duckduckgo", ValueError("second"), threshold=3)

    assert len(orch.failure_counts) == 2
    assert orch.blocked_tools == {}
