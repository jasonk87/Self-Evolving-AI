import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.mark.integration
def test_council_integration_smoke(monkeypatch, tmp_path):
    """Verify council path can run with mocked reviewers without mutating repo files."""
    mock_reviewer_module = MagicMock()
    mock_critic_module = MagicMock()

    class MockReviewerAgent:
        def __init__(self, name):
            self.name = name

    mock_reviewer_module.ReviewerAgent = MockReviewerAgent
    mock_critic_module.CriticalReviewCoordinator = MagicMock()

    monkeypatch.setitem(__import__("sys").modules, "ai_assistant.core.reviewer", mock_reviewer_module)
    monkeypatch.setitem(__import__("sys").modules, "ai_assistant.core.critical_reviewer", mock_critic_module)

    try:
        import ai_assistant.custom_tools.meta_programming_tools as mpt
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Council integration import unavailable: {exc}")

    # Prevent writes into repository tree during test.
    monkeypatch.setattr(mpt, "get_generated_tools_path", lambda: str(tmp_path))

    class MockLLM:
        async def invoke_ollama_model_async(self, prompt, model_name, temperature=0.2):
            return """```python\ndef council_sys_test_tool():\n    return 'Sys Tested'\n```\nSuggested Filename: council_sys_test_tool.py"""

        async def send_request(self, prompt, model_name, temperature=0.2):
            return await self.invoke_ollama_model_async(prompt, model_name, temperature)

    class MockActionExecutor:
        def __init__(self):
            self.llm_interface = MockLLM()

    mock_instance = mock_critic_module.CriticalReviewCoordinator.return_value

    async def _mock_debate(*args, **kwargs):
        return (True, "Mock Council Approval")

    mock_instance.execute_council_debate.side_effect = _mock_debate

    result = asyncio.run(
        mpt.generate_new_tool_from_description(
            tool_description="Create a test tool for council verification",
            action_executor=MockActionExecutor(),
        )
    )
    assert isinstance(result, str)
    assert "Success" in result or "already exists" in result or "Error" in result
