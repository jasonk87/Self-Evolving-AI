
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from ai_assistant.learning.learning import LearningAgent, InsightType
from ai_assistant.core.reflection import ReflectionLogEntry

@pytest.mark.asyncio
async def test_root_cause_analysis_flow(tmp_path):
    # Setup
    insights_file = tmp_path / "test_insights.json"
    agent = LearningAgent(insights_filepath=str(insights_file))
    
    # Mock data
    mock_entry = ReflectionLogEntry(
        entry_id="test_entry_123",
        goal_description="Calculate something",
        status="FAILURE",
        error_type="division by zero",
        error_message="float division by zero",
        plan=[{"tool_name": "divide_numbers", "args": (10, 0), "module_path": "math_tools", "function_name_in_module": "divide"}],
        execution_results=[{"error": "division by zero", "_is_error_representation_": True}]
    )

    mock_code = """
def divide(a, b):
    return a / b
"""
    mock_analysis = "The error occurs because variable `b` is 0, causing a division by zero exception at line 3."

    # Patches
    # Patch get_function_source_code
    with patch("ai_assistant.learning.learning.self_modification.get_function_source_code") as mock_get_code, \
         patch("ai_assistant.learning.learning.invoke_gemini_model_async", new_callable=AsyncMock) as mock_llm, \
         patch("ai_assistant.learning.learning.get_tool") as mock_get_tool:

        mock_get_code.return_value = mock_code
        mock_llm.return_value = mock_analysis
        mock_get_tool.return_value = {"module_path": "math_tools", "function_name": "divide"}

        # Execution
        insight = await agent.process_reflection_entry(mock_entry)

        # Verification
        assert insight is not None
        assert insight.type == InsightType.TOOL_BUG_SUSPECTED
        assert insight.related_tool_name == "divide_numbers"
        
        # Verify code was fetched
        mock_get_code.assert_called_once_with("math_tools", "divide")
        
        # Verify LLM was called with code
        mock_llm.assert_called_once()
        prompt = mock_llm.call_args.kwargs.get("prompt") or mock_llm.call_args[0][0]
        assert mock_code in prompt
        assert "division by zero" in prompt
        
        # Verify analysis is in the insight
        assert "ROOT CAUSE ANALYSIS" in insight.description
        assert mock_analysis in insight.description
        assert insight.metadata["root_cause_analysis"] == mock_analysis

        print("\nTest Passed: Root Cause Analysis correctly integrated!")
