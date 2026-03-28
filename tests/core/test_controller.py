import pytest
from unittest.mock import AsyncMock, MagicMock
from ai_assistant.core.controller import SystemController
from ai_assistant.core.models.state import ExecutionState

@pytest.fixture
def mock_orchestrator():
    orchestrator = MagicMock()

    async def mock_process(state, **kwargs):
        state.current_status = "completed"
        state.tool_results.append({
            "action_name": "orchestrator_final_answer",
            "success": True,
            "result": "Success message",
            "collected_images": ["img1"]
        })
        return state

    orchestrator.process_prompt = AsyncMock(side_effect=mock_process)
    return orchestrator

@pytest.mark.asyncio
async def test_controller_handles_successful_request(mock_orchestrator):
    controller = SystemController(orchestrator=mock_orchestrator)
    state = await controller.handle_user_request(prompt="Test prompt")

    assert isinstance(state, ExecutionState)
    assert state.original_user_prompt == "Test prompt"
    assert state.current_status == "completed"
    assert len(state.errors) == 0
    assert len(state.tool_results) == 1

    final_result = state.tool_results[0]
    assert final_result["action_name"] == "orchestrator_final_answer"
    assert final_result["success"] is True
    assert final_result["result"] == "Success message"

@pytest.mark.asyncio
async def test_controller_handles_failed_request(mock_orchestrator):
    async def mock_process_fail(state, **kwargs):
        state.current_status = "failed"
        state.errors.append("Maximum cycles reached.")
        state.tool_results.append({
            "action_name": "orchestrator_final_answer",
            "success": False,
            "result": "Maximum cycles reached."
        })
        return state

    mock_orchestrator.process_prompt = AsyncMock(side_effect=mock_process_fail)
    controller = SystemController(orchestrator=mock_orchestrator)

    state = await controller.handle_user_request(prompt="Fail prompt")

    assert state.current_status == "failed"
    assert "Maximum cycles reached." in state.errors
    assert state.tool_results[0]["success"] is False

@pytest.mark.asyncio
async def test_controller_handles_exceptions(mock_orchestrator):
    mock_orchestrator.process_prompt = AsyncMock(side_effect=Exception("Critical meltdown"))
    controller = SystemController(orchestrator=mock_orchestrator)

    state = await controller.handle_user_request(prompt="Error prompt")

    assert state.current_status == "failed"
    assert "Critical System Error: Critical meltdown" in state.errors
