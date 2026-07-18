import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ai_assistant.custom_tools import meta_programming_tools as meta


class _ActionExecutor:
    def __init__(self, llm):
        self.llm_interface = llm


@pytest.mark.asyncio
async def test_generation_never_uses_dunder_method_or_init_filename(tmp_path):
    class FakeLLM:
        async def invoke_ollama_model_async(self, prompt, **kwargs):
            if "AI governance system" in prompt:
                return "NO"
            return """```python
class Renderer:
    def __init__(self):
        self.ready = True

async def render_custom_html(value: str) -> str:
    return value
```
Suggested Filename: __init__.py"""

    class FakeReviewer:
        def __init__(self, model_name):
            self.model_name = model_name

    class FakeCoordinator:
        init_arg_counts = []

        def __init__(self, *args):
            self.init_arg_counts.append(len(args))

        async def execute_council_debate(self, **kwargs):
            return True, "approved"

    fake_tool_system = SimpleNamespace(
        list_tools=lambda: {"existing_tool": "An existing description."},
        refresh_custom_tools=lambda: "refreshed",
    )

    with patch.object(meta, "get_generated_tools_path", return_value=str(tmp_path)), \
         patch("ai_assistant.tools.tool_system.tool_system_instance", fake_tool_system), \
         patch("ai_assistant.core.reviewer.ReviewerAgent", FakeReviewer), \
         patch("ai_assistant.core.critical_reviewer.CriticalReviewCoordinator", FakeCoordinator), \
         patch.object(meta, "_generate_test_for_tool", new=AsyncMock(return_value="Skipped for unit test")):
        result = await meta.generate_new_tool_from_description(
            "Render safe custom HTML in chat.",
            action_executor=_ActionExecutor(FakeLLM()),
        )

    assert "Successfully generated" in result
    assert (tmp_path / "render_custom_html.py").exists()
    assert not (tmp_path / "__init__.py").read_text(encoding="utf-8").strip().endswith(
        "from .__init__ import __init__"
    )
    assert FakeCoordinator.init_arg_counts == [1]


@pytest.mark.asyncio
async def test_redundancy_guard_accepts_string_descriptions_and_blocks_duplicate(tmp_path):
    class FakeLLM:
        async def invoke_ollama_model_async(self, prompt, **kwargs):
            assert "chat_dynamic_html" in prompt
            return "YES: chat_dynamic_html"

    fake_tool_system = SimpleNamespace(
        list_tools=lambda: {
            "chat_dynamic_html": "Generates safe HTML for inline chat presentation."
        }
    )

    with patch.object(meta, "get_generated_tools_path", return_value=str(tmp_path)), \
         patch("ai_assistant.tools.tool_system.tool_system_instance", fake_tool_system):
        result = await meta.generate_new_tool_from_description(
            "Create custom HTML for chat presentation.",
            action_executor=_ActionExecutor(FakeLLM()),
        )

    assert "ABORTED" in result
    assert "chat_dynamic_html" in result
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_test_generation_timeout_is_bounded(monkeypatch):
    class TimeoutLLM:
        async def invoke_ollama_model_async(self, *args, **kwargs):
            raise asyncio.TimeoutError

    result = await meta._generate_test_for_tool(
        tool_name="slow_tool",
        tool_filename="slow_tool.py",
        tool_code="def slow_tool():\n    return True",
        action_executor=_ActionExecutor(TimeoutLLM()),
    )

    assert result == "Skipped (test generation timed out after 30 seconds)."
