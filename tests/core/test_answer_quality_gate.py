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
    def __init__(self):
        self.calls = []

    def get_tools_description(self):
        return "status_tool: checks live status"

    async def execute_tool(self, tool_name, **kwargs):
        self.calls.append({"tool_name": tool_name, **kwargs})
        assert tool_name == "status_tool"
        return {"success": True, "result": "all systems nominal"}


class FakeSearchToolSystem:
    def __init__(self):
        self.calls = []

    def get_tools_description(self):
        return "google_search: searches the web"

    async def execute_tool(self, tool_name, **kwargs):
        self.calls.append({"tool_name": tool_name, **kwargs})
        assert tool_name == "google_search"
        query = kwargs.get("kwargs", {}).get("query", "")
        if "Claire" in query or "Clair" in query:
            return {
                "success": True,
                "result": (
                    "Search result: voco The Clair Cincinnati Downtown appears to be the likely hotel. "
                    "Aronoff Center is in downtown Cincinnati. Exact walking distance not verified."
                ),
            }
        return {"success": True, "result": "Search result: Aronoff Center address in downtown Cincinnati."}


class FakeAgentToolSystem:
    def __init__(self):
        self.calls = []

    def get_tools_description(self):
        return "spawn_ephemeral_agent: spawns and queues an agent"

    async def execute_tool(self, tool_name, **kwargs):
        self.calls.append({"tool_name": tool_name, **kwargs})
        assert tool_name == "spawn_ephemeral_agent"
        return {
            "success": True,
            "result": {
                "agent_id": "agent-1",
                "goal_id": "goal-1",
                "goal_status": "pending",
                "queued": "true",
                "message": "Agent agent-1 was created and assigned a real background task.",
            },
        }


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


@pytest.mark.asyncio
async def test_repeated_search_stagnation_injects_control_observation(monkeypatch):
    prompts_seen = []
    responses = iter([
        '{"type":"tool_call","thought":"Find the venue.","name":"google_search","params":{"query":"Aronoff Center address"}}',
        '{"type":"tool_call","thought":"Find the likely Clair hotel.","name":"google_search","params":{"query":"Claire hotel near Aronoff Center Cincinnati"}}',
        '{"type":"tool_call","thought":"Search again for the Clair hotel.","name":"google_search","params":{"query":"Claire hotel Cincinnati near Aronoff Center"}}',
        '{"type":"tool_call","thought":"Search exact distance.","name":"google_search","params":{"query":"voco The Clair Cincinnati distance to Aronoff Center"}}',
        '{"type":"final_answer","thought":"Use gathered evidence with uncertainty.","params":{"message":"The likely hotel is voco The Clair Cincinnati Downtown. I found enough to identify the likely hotel near the Aronoff Center, but I do not have a verified exact walking distance from the search results, so I would treat the distance as still needing confirmation."}}',
    ])

    async def fake_invoke(prompt, **kwargs):
        prompts_seen.append(prompt)
        return next(responses)

    orch = _make_isolated_orchestrator()
    fake_tools = FakeSearchToolSystem()
    monkeypatch.setattr("ai_assistant.core.orchestrator.invoke_gemini_model_async", fake_invoke)
    monkeypatch.setattr("ai_assistant.core.orchestrator.tool_system_instance", fake_tools)

    state = ExecutionState(original_user_prompt="Can you see how far away the Claire hotel is from the Aronoff Center?")
    await orch._execute_universal_cycle_internal(state, state.original_user_prompt, "", [], None, "USER", None)

    final_result = state.tool_results[-1]
    metadata = final_result["react_cycle_metadata"]

    assert state.current_status == "completed"
    assert "voco The Clair" in final_result["result"]
    assert "not have a verified exact walking distance" in final_result["result"]
    assert any(record.get("retry_reason") is None and record.get("tool_name") == "google_search" for record in metadata)
    assert any("ReactLoopControl: You are repeating" in prompt for prompt in prompts_seen)
    assert len(fake_tools.calls) == 4


@pytest.mark.asyncio
async def test_near_max_cycles_with_observations_adds_finalization_pressure(monkeypatch):
    prompts_seen = []
    responses = iter([
        '{"type":"tool_call","thought":"Initial lookup.","name":"google_search","params":{"query":"Aronoff Center address"}}',
        '{"type":"tool_call","thought":"Different lookup one.","name":"google_search","params":{"query":"voco The Clair Cincinnati official address"}}',
        '{"type":"tool_call","thought":"Different lookup two.","name":"google_search","params":{"query":"Cincinnati Arts Association hotels"}}',
        '{"type":"tool_call","thought":"Different lookup three.","name":"google_search","params":{"query":"downtown Cincinnati hotel distance map"}}',
        '{"type":"tool_call","thought":"Different lookup four.","name":"google_search","params":{"query":"Aronoff Center nearby lodging"}}',
        '{"type":"tool_call","thought":"Different lookup five.","name":"google_search","params":{"query":"voco hotel Cincinnati downtown location"}}',
        '{"type":"final_answer","thought":"Answer near limit.","params":{"message":"I found partial evidence about the likely hotel and venue, but not enough for a verified exact distance. I would confirm in maps before relying on it."}}',
    ])

    async def fake_invoke(prompt, **kwargs):
        prompts_seen.append(prompt)
        return next(responses)

    orch = _make_isolated_orchestrator()
    monkeypatch.setattr("ai_assistant.core.orchestrator.invoke_gemini_model_async", fake_invoke)
    monkeypatch.setattr("ai_assistant.core.orchestrator.tool_system_instance", FakeSearchToolSystem())

    state = ExecutionState(original_user_prompt="Can you see how far away the Claire hotel is from the Aronoff Center?")
    await orch._execute_universal_cycle_internal(state, state.original_user_prompt, "", [], None, "USER", None)

    assert state.current_status == "completed"
    assert any("ReactLoopControl: You are near the ReAct cycle limit" in prompt for prompt in prompts_seen)


@pytest.mark.asyncio
async def test_duplicate_successful_tool_call_short_circuits_to_final_answer(monkeypatch):
    responses = iter([
        '{"type":"tool_call","thought":"Check branches.","name":"status_tool","params":{"command":"git branch -v","cwd":"C:\\\\Users\\\\Owner\\\\Desktop\\\\Projects\\\\Self Evolving AI"}}',
        '{"type":"tool_call","thought":"Check branches again.","name":"status_tool","params":{"command":"git branch -v","cwd":"C:\\\\Users\\\\Owner\\\\Desktop\\\\Projects\\\\Self Evolving AI"}}',
    ])

    async def fake_invoke(*args, **kwargs):
        return next(responses)

    orch = _make_isolated_orchestrator()
    fake_tools = FakeToolSystem()
    monkeypatch.setattr("ai_assistant.core.orchestrator.invoke_gemini_model_async", fake_invoke)
    monkeypatch.setattr("ai_assistant.core.orchestrator.tool_system_instance", fake_tools)

    state = ExecutionState(original_user_prompt="Look up the latest updated branch for Self Evolving AI")
    await orch._execute_universal_cycle_internal(state, state.original_user_prompt, "", [], None, "USER", None)

    final_result = state.tool_results[-1]
    metadata = final_result["react_cycle_metadata"]

    assert state.current_status == "completed"
    assert len(fake_tools.calls) == 1
    assert "all systems nominal" in final_result["result"]
    assert metadata[-1]["retry_reason"] == "duplicate_successful_tool_call_short_circuit"


@pytest.mark.asyncio
async def test_spawn_ephemeral_agent_gets_session_and_finishes_as_queued_background_task(monkeypatch):
    async def fake_invoke(*args, **kwargs):
        return (
            '{"type":"tool_call","thought":"Queue a real agent.","name":"spawn_ephemeral_agent",'
            '"params":{"task_description":"Investigate the LLM Call project","scope_type":"persistent"}}'
        )

    orch = _make_isolated_orchestrator()
    fake_tools = FakeAgentToolSystem()
    monkeypatch.setattr("ai_assistant.core.orchestrator.invoke_gemini_model_async", fake_invoke)
    monkeypatch.setattr("ai_assistant.core.orchestrator.tool_system_instance", fake_tools)

    state = ExecutionState(original_user_prompt="What is the LLM Call project about?")
    await orch._execute_universal_cycle_internal(state, state.original_user_prompt, "", [], "chat-42", "USER", None)

    final_result = state.tool_results[-1]
    tool_kwargs = fake_tools.calls[0]["kwargs"]

    assert state.current_status == "completed"
    assert tool_kwargs["session_id"] == "chat-42"
    assert "assigned a real background task" in final_result["result"]
