import pytest
from unittest.mock import MagicMock
from ai_assistant.custom_tools.generated.ai_response_generator import generate_two_responses


@pytest.mark.asyncio
async def test_generate_two_responses_happy_path():
    """Test the normal operation of the tool."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_prompt.side_effect = ["Response 1", "Response 2"]
    prompt = "Test prompt"
    expected_response = "Response 1\n---RESPONSE_SEPARATOR---\nResponse 2"
    actual_response = await generate_two_responses(mock_action_executor, prompt)
    assert actual_response == expected_response


@pytest.mark.asyncio
async def test_generate_two_responses_empty_prompt():
    """Test with an empty prompt."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_prompt.side_effect = ["Response 1", "Response 2"]
    prompt = ""
    expected_response = "Response 1\n---RESPONSE_SEPARATOR---\nResponse 2"
    actual_response = await generate_two_responses(mock_action_executor, prompt)
    assert actual_response == expected_response


@pytest.mark.asyncio
async def test_generate_two_responses_none_action_executor():
    """Test with a None action_executor.  Expect an error."""
    prompt = "Test prompt"
    with pytest.raises(AttributeError):
        await generate_two_responses(None, prompt)


@pytest.mark.asyncio
async def test_generate_two_responses_action_executor_returns_none():
    """Test when the action_executor returns None for one or both responses."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_prompt.side_effect = [None, "Response 2"]
    prompt = "Test prompt"
    expected_response = "Error: Could not generate both responses."
    actual_response = await generate_two_responses(mock_action_executor, prompt)
    assert actual_response == expected_response

    mock_action_executor = MagicMock()
    mock_action_executor.run_prompt.side_effect = ["Response 1", None]
    prompt = "Test prompt"
    expected_response = "Error: Could not generate both responses."
    actual_response = await generate_two_responses(mock_action_executor, prompt)
    assert actual_response == expected_response

    mock_action_executor = MagicMock()
    mock_action_executor.run_prompt.side_effect = [None, None]
    prompt = "Test prompt"
    expected_response = "Error: Could not generate both responses."
    actual_response = await generate_two_responses(mock_action_executor, prompt)
    assert actual_response == expected_response


@pytest.mark.asyncio
async def test_generate_two_responses_action_executor_raises_exception():
    """Test when the action_executor raises an exception."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_prompt.side_effect = Exception("Test exception")
    prompt = "Test prompt"
    expected_response_start = "Error: Test exception"
    actual_response = await generate_two_responses(mock_action_executor, prompt)
    assert actual_response.startswith(expected_response_start)


@pytest.mark.asyncio
async def test_generate_two_responses_long_prompt():
    """Test with a very long prompt."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_prompt.side_effect = ["Response 1", "Response 2"]
    prompt = "This is a very long prompt. " * 100
    expected_response = "Response 1\n---RESPONSE_SEPARATOR---\nResponse 2"
    actual_response = await generate_two_responses(mock_action_executor, prompt)
    assert actual_response == expected_response