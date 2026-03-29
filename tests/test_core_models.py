import pytest
from pydantic import ValidationError
from ai_assistant.core.models.state import ExecutionState, ExecutionStatus
from ai_assistant.core.models.base_tool import BaseActionRequest, BaseActionResponse


def test_execution_state_valid():
    state = ExecutionState(original_user_prompt="Hello world")
    assert state.original_user_prompt == "Hello world"
    assert state.current_status == ExecutionStatus.INITIALIZED
    assert state.errors == []
    assert state.tool_results == []
    assert state.context_limits == {}

def test_execution_state_missing_prompt():
    with pytest.raises(ValidationError):
        ExecutionState()

def test_base_action_request_valid():
    request = BaseActionRequest(action_name="test_action")
    assert request.action_name == "test_action"
    assert request.parameters == {}

def test_base_action_request_missing_action_name():
    with pytest.raises(ValidationError):
        BaseActionRequest()

def test_base_action_response_valid():
    response = BaseActionResponse(success=True)
    assert response.success is True
    assert response.result is None
    assert response.error_message is None

def test_base_action_response_missing_success():
    with pytest.raises(ValidationError):
        BaseActionResponse()
