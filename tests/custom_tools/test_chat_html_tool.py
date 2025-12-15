import pytest
from unittest.mock import MagicMock
from ai_assistant.custom_tools.generated.chat_html_tool import chat_dynamic_html
import json

@pytest.mark.asyncio
async def test_chat_dynamic_html_happy_path():
    """Test with a valid idea and a mock action_executor that returns a valid JSON response."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_code.return_value = json.dumps({
        "html": "<p>Hello, world!</p>",
        "css": "p { color: blue; }",
        "js": "console.log('Hello from JS');"
    })

    idea = "A simple greeting"
    result = await chat_dynamic_html(mock_action_executor, idea)

    assert "<style>\np { color: blue; }\n</style>" in result
    assert "<p>Hello, world!</p>" in result
    assert "<script>\nconsole.log('Hello from JS');\n</script>" in result
    mock_action_executor.run_code.assert_called_once()


@pytest.mark.asyncio
async def test_chat_dynamic_html_no_css():
    """Test when the LLM returns no CSS."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_code.return_value = json.dumps({
        "html": "<p>Hello, world!</p>",
        "css": None,
        "js": "console.log('Hello from JS');"
    })

    idea = "A simple greeting without CSS"
    result = await chat_dynamic_html(mock_action_executor, idea)

    assert "<style>" not in result
    assert "<p>Hello, world!</p>" in result
    assert "<script>\nconsole.log('Hello from JS');\n</script>" in result
    mock_action_executor.run_code.assert_called_once()


@pytest.mark.asyncio
async def test_chat_dynamic_html_no_js():
    """Test when the LLM returns no JavaScript."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_code.return_value = json.dumps({
        "html": "<p>Hello, world!</p>",
        "css": "p { color: blue; }",
        "js": None
    })

    idea = "A simple greeting without JS"
    result = await chat_dynamic_html(mock_action_executor, idea)

    assert "<style>\np { color: blue; }\n</style>" in result
    assert "<p>Hello, world!</p>" in result
    assert "<script>" not in result
    mock_action_executor.run_code.assert_called_once()


@pytest.mark.asyncio
async def test_chat_dynamic_html_empty_idea():
    """Test with an empty idea."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_code.return_value = json.dumps({
        "html": "<p>Empty idea result</p>",
        "css": None,
        "js": None
    })

    idea = ""
    result = await chat_dynamic_html(mock_action_executor, idea)

    assert "<p>Empty idea result</p>" in result
    mock_action_executor.run_code.assert_called_once()


@pytest.mark.asyncio
async def test_chat_dynamic_html_invalid_json():
    """Test when the LLM returns invalid JSON."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_code.return_value = "invalid json"

    idea = "Some idea"
    result = await chat_dynamic_html(mock_action_executor, idea)

    assert "Error decoding LLM response" in result
    mock_action_executor.run_code.assert_called_once()


@pytest.mark.asyncio
async def test_chat_dynamic_html_llm_error():
    """Test when the LLM call raises an exception."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_code.side_effect = Exception("LLM failed")

    idea = "Some idea"
    result = await chat_dynamic_html(mock_action_executor, idea)

    assert "Error generating HTML" in result
    mock_action_executor.run_code.assert_called_once()


@pytest.mark.asyncio
async def test_chat_dynamic_html_empty_html_css_js():
    """Test when the LLM returns empty HTML, CSS, and JS."""
    mock_action_executor = MagicMock()
    mock_action_executor.run_code.return_value = json.dumps({
        "html": "",
        "css": None,
        "js": None
    })

    idea = "An empty element"
    result = await chat_dynamic_html(mock_action_executor, idea)

    assert result == "\n\n"
    mock_action_executor.run_code.assert_called_once()