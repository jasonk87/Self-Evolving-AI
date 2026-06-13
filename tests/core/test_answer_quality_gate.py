from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_assistant.core.models.state import ExecutionState
from ai_assistant.core.orchestrator import AnswerQualityGate, DynamicOrchestrator


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
    orch.episodic_manager = SimpleNamespace(
        recall_failures=AsyncMock(return_value=""),
        record_experience=AsyncMock(),
    )
    return orch


class FakeToolSystem:
    def get_tools_description(self):
        return "status_tool: checks live status"

    async def execute_tool(self, tool_name, **kwargs):
        assert tool_name == "status_tool"
        return {"success": True, "result": "all systems nominal"}


@pytest.mark.asyncio
async def test_final_answer_accepted_when_useful(monkeypatch):
    async def fake_invoke(*args, **kwargs):
        return '{"type":"final_answer","thought":"Simple arithmetic.","params":{"message":"2 + 2 is 4."}}'

    orch = _make_isolated_orchestrator()
    monkeypatch.setattr("ai_assistant.core.orchestrator.invoke_gemini_model_async", fake_invoke)
    monkeypatch.setattr("ai_assistant.core.orchestrator.tool_system_instance", FakeToolSystem())

    state = ExecutionState(original_user_prompt="What is 2 + 2?")
    await orch._execute_universal_cycle_internal(state, state.original_user_prompt, "", [], None, "USER", None)

    final_result = state.tool_results[-1]
    metadata = final_result["react_cycle_metadata"]

    assert state.current_status == "completed"
    assert final_result["result"] == "2 + 2 is 4."
    assert metadata[0]["quality_gate_result"]["accepted"] is True
    assert metadata[0]["retry_reason"] is None


def test_vague_final_answer_rejected_when_context_or_tool_needed():
    gate = AnswerQualityGate()

    result = gate.evaluate(
        user_prompt="Check the current project status",
        answer="Sure.",
        context="",
        execution_history="",
        remaining_cycles=2,
    )

    assert result.accepted is False
    assert result.reason == "context_or_tool_needed"
    assert result.should_retrieve_more_context is True
    assert result.retry_observation


def test_casual_conversational_answer_does_not_need_tool_context():
    gate = AnswerQualityGate()

    result = gate.evaluate(
        user_prompt="Thanks, that helped",
        answer="Anytime.",
        context="",
        execution_history="",
        remaining_cycles=2,
    )

    assert result.accepted is True
    assert result.reason == "accepted"
    assert result.should_retrieve_more_context is False


def test_generic_reply_to_substantive_non_tool_request_is_still_rejected():
    gate = AnswerQualityGate()

    result = gate.evaluate(
        user_prompt="Explain how the approval queue works",
        answer="Sure.",
        context="",
        execution_history="",
        remaining_cycles=2,
    )

    assert result.accepted is False
    assert result.reason == "too_generic"
    assert result.too_generic is True


@pytest.mark.asyncio
async def test_low_quality_answer_forces_another_react_cycle(monkeypatch):
    responses = iter([
        '{"type":"final_answer","thought":"Answering too early.","params":{"message":"Sure."}}',
        '{"type":"tool_call","thought":"Need live status first.","name":"status_tool","params":{}}',
        '{"type":"final_answer","thought":"Tool result answers it.","params":{"message":"The status tool reported all systems nominal."}}',
    ])

    async def fake_invoke(*args, **kwargs):
        return next(responses)

    orch = _make_isolated_orchestrator()
    monkeypatch.setattr("ai_assistant.core.orchestrator.invoke_gemini_model_async", fake_invoke)
    monkeypatch.setattr("ai_assistant.core.orchestrator.tool_system_instance", FakeToolSystem())

    state = ExecutionState(original_user_prompt="Check the current system status")
    await orch._execute_universal_cycle_internal(state, state.original_user_prompt, "", [], None, "USER", None)

    final_result = state.tool_results[-1]
    metadata = final_result["react_cycle_metadata"]

    assert state.current_status == "completed"
    assert final_result["result"] == "The status tool reported all systems nominal."
    assert metadata[0]["quality_gate_result"]["accepted"] is False
    assert metadata[0]["retry_reason"] == "context_or_tool_needed"
    assert metadata[1]["selected_type"] == "tool_call"
    assert metadata[1]["tool_name"] == "status_tool"
    assert metadata[2]["quality_gate_result"]["accepted"] is True


@pytest.mark.asyncio
async def test_quality_gate_max_cycles_exits_safely(monkeypatch):
    async def fake_invoke(*args, **kwargs):
        return '{"type":"final_answer","thought":"Answering too early.","params":{"message":"Sure."}}'

    orch = _make_isolated_orchestrator()
    monkeypatch.setattr("ai_assistant.core.orchestrator.MAX_REACT_STEPS", 1)
    monkeypatch.setattr("ai_assistant.core.orchestrator.invoke_gemini_model_async", fake_invoke)
    monkeypatch.setattr("ai_assistant.core.orchestrator.tool_system_instance", FakeToolSystem())

    state = ExecutionState(original_user_prompt="Check the current system status")
    await orch._execute_universal_cycle_internal(state, state.original_user_prompt, "", [], None, "USER", None)

    final_result = state.tool_results[-1]
    metadata = final_result["react_cycle_metadata"]

    assert state.current_status == "completed"
    assert final_result["result"] == "Sure."
    assert metadata[0]["quality_gate_result"]["accepted"] is False
    assert "max cycles were reached" in state.errors[-1]
