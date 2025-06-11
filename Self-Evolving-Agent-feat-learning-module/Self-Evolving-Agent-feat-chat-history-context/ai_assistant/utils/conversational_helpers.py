from typing import List, Dict, Any, Optional
import json
import logging

# Assuming OllamaProvider is the concrete class or a suitable ABC/protocol
# Adjust if a different LLM provider interface is used throughout the project.
try:
    from ai_assistant.llm_interface.ollama_client import OllamaProvider
    from ai_assistant.config import get_model_for_task
except ImportError: # pragma: no cover
    # Fallback for potential local execution or different project structure
    # This might occur if the utils directory is not correctly recognized as part of the package
    # when running this file directly in some environments.
    # For the agent's runtime, the primary import path should work.
    print("Warning: Could not import OllamaProvider or get_model_for_task from standard paths. Using placeholder for direct script execution if applicable.")
    OllamaProvider = type('OllamaProvider', (object,), {}) # Placeholder
    def get_model_for_task(task_type: str) -> str: return "mock_model" # Placeholder


logger = logging.getLogger(__name__)

LLM_CONVERSATIONAL_SUMMARY_PROMPT_TEMPLATE = """
User's original request: "{original_user_query}"

I attempted to address this by performing the following actions and getting these results:
Actions and Results:
{actions_and_results_summary}

Overall outcome of the attempt: {overall_success_str}

Based on this, provide a concise, natural language summary to the user, as if you are a helpful AI assistant.
Your summary should be brief and directly relate to the user's original request.

Key Instructions:
- If successful, explain what you found or did in relation to their query.
- If partially successful, explain what worked and what didn't.
- If failed, explain the issue clearly but try to be helpful if possible (e.g., suggesting what they might try differently if it was a user error, or stating you've logged the internal error).
- Avoid overly technical jargon unless the user's query was highly technical.
- Do not just repeat the raw results. Synthesize the information.
- Do not start with phrases like "Based on this..." or "Here's a summary...". Just give the direct conversational response.
- If the actions involved showing data (like a list of files or a project status), briefly mention what was shown without excessive detail.
- Focus on conciseness and relevance to the user's goal.

Example:
User's original request: "What are the first 2 files in my 'WebApp' project and what's its status?"

I attempted to address this by performing the following actions and getting these results:
Actions and Results:
Step 1: Ran tool 'find_project_by_name' with args ('WebApp',). Result: {{'project_id': 'proj_webapp_123', 'name': 'WebApp', 'root_path': '/projects/webapp'}}
Step 2: Ran tool 'list_project_files' with args ('proj_webapp_123',). Result: {{'status': 'success', 'path_listed': '/projects/webapp', 'files': ['main.py', 'utils.py', 'config.json', 'README.md'], 'directories': ['static', 'templates']}}
Step 3: Ran tool 'get_project_status' with args ('proj_webapp_123',). Result: {{'status': 'active_development'}}

Overall outcome of the attempt: Succeeded

Conversational Summary:
Okay, for your 'WebApp' project, the first two files I see are main.py and utils.py. The project is currently in 'active_development'.

---
Now, provide your conversational summary for the input above.
Conversational Summary:
"""

async def summarize_tool_result_conversationally(
    original_user_query: str,
    executed_plan_steps: List[Dict[str, Any]],
    tool_results: List[Any],
    overall_success: bool,
    llm_provider: OllamaProvider,
    model_name: Optional[str] = None
) -> str:
    """
    Generates a conversational summary of tool execution results using an LLM.

    Args:
        original_user_query: The user's initial query.
        executed_plan_steps: The list of plan steps that were executed.
        tool_results: The results corresponding to each executed step.
        overall_success: Boolean indicating if the overall execution was successful.
        llm_provider: An instance of an LLM provider (e.g., OllamaProvider).
        model_name: Optional. Specific model name to use. If None, uses default for "conversational_response".

    Returns:
        A string containing the conversational summary.
    """
    actions_summary_parts = []
    for i, step in enumerate(executed_plan_steps):
        tool_name = step.get("tool_name", "Unknown Tool")
        args = step.get("args", ())
        kwargs = step.get("kwargs", {})

        result_summary = "No result captured."
        if i < len(tool_results):
            res = tool_results[i]
            if isinstance(res, Exception):
                result_summary = f"Error: {type(res).__name__}: {str(res)}"
            elif isinstance(res, dict):
                # If the tool provides its own summary string, use that.
                if "summary_str" in res and isinstance(res["summary_str"], str):
                    result_summary = res["summary_str"]
                elif "status" in res and res["status"] == "error" and "message" in res:
                    result_summary = f"Tool Error: {res['message']}"
                else:
                    # Basic summary for dicts to avoid excessive length
                    result_summary = f"Output data (dict with {len(res)} keys: {list(res.keys())[:3]}{'...' if len(res.keys()) > 3 else ''})"
            elif isinstance(res, list):
                result_summary = f"Output data (list with {len(res)} items: {str(res[:3])[:100]}{'...' if len(res) > 3 or len(str(res[:3])) > 100 else ''})"
            elif isinstance(res, (str, int, float, bool)):
                result_summary = str(res)
            else:
                result_summary = f"Output of type {type(res).__name__}."

            if len(result_summary) > 150: # Truncate very long simple results
                result_summary = result_summary[:147] + "..."

        actions_summary_parts.append(
            f"Step {i+1}: Ran tool '{tool_name}' with args {args} and kwargs {kwargs}. Result: {result_summary}"
        )

    actions_and_results_summary = "\n".join(actions_summary_parts) if actions_summary_parts else "No actions were taken."

    if overall_success:
        overall_success_str = "Succeeded"
    # TODO: Add "Partially Succeeded" if possible to determine
    else:
        overall_success_str = "Failed"

    prompt = LLM_CONVERSATIONAL_SUMMARY_PROMPT_TEMPLATE.format(
        original_user_query=original_user_query,
        actions_and_results_summary=actions_and_results_summary,
        overall_success_str=overall_success_str
    )

    try:
        target_model = model_name or get_model_for_task("conversational_response")
        # Temperature might be slightly higher for more natural language
        summary_response = await llm_provider.invoke_ollama_model_async(
            prompt,
            model_name=target_model,
            temperature=0.6
        )
        return summary_response.strip() if summary_response else "I've processed your request."
    except Exception as e:
        logger.error(f"Error invoking LLM for conversational summary: {e}", exc_info=True)
        return "I have processed your request. The detailed technical summary is available if needed."


if __name__ == '__main__': # pragma: no cover
    import asyncio
    from unittest.mock import MagicMock

    # Mock OllamaProvider for testing
    class MockOllamaProvider:
        async def invoke_ollama_model_async(self, prompt: str, model_name: str, temperature: float) -> str:
            print(f"\n--- LLM Prompt for Conversational Summary ---")
            print(prompt)
            print(f"--- End LLM Prompt (Model: {model_name}, Temp: {temperature}) ---")
            if "list_files" in prompt and "found 3 files" in prompt:
                return "Okay, I looked in the main directory and found 3 files for you: file1.txt, file2.py, and notes.md."
            elif "failed" in prompt.lower() and ("calculate_sum" in prompt or "ValueError" in prompt) :
                return "I tried to do that, but unfortunately, I ran into an issue with the 'calculate_sum' tool. It seems it received invalid input."
            elif "complex_data" in prompt:
                return "I've processed the complex data you provided."
            return "I've completed the steps you asked for."

    async def test_conversational_summary():
        mock_llm_provider_instance = MockOllamaProvider()

        # Test Case 1: Success with simple list result
        plan1 = [{"tool_name": "list_files", "args": ("/test",), "kwargs": {}}]
        # Tool result often provides a summary string for complex data
        results1 = [{"files": ["file1.txt", "file2.py", "notes.md"], "count": 3, "summary_str": "found 3 files in /test"}]
        summary1 = await summarize_tool_result_conversationally(
            "What files are in /test?", plan1, results1, True, mock_llm_provider_instance
        )
        print(f"\nUser Query: What files are in /test?\nAI Summary 1: {summary1}")
        assert "found 3 files" in summary1.lower()

        # Test Case 2: Failure with Exception
        plan2 = [{"tool_name": "calculate_sum", "args": ("a", "5"), "kwargs": {}}]
        results2 = [ValueError("Invalid input for sum: 'a' is not a number.")]
        summary2 = await summarize_tool_result_conversationally(
            "Add a and 5", plan2, results2, False, mock_llm_provider_instance
        )
        print(f"\nUser Query: Add a and 5\nAI Summary 2: {summary2}")
        assert "invalid input" in summary2.lower()
        assert "calculate_sum" in summary2.lower()

        # Test Case 3: Success with complex dictionary result (no summary_str)
        plan3 = [{"tool_name": "get_item_details", "args": ("item123",), "kwargs": {}}]
        results3 = [{"item_id": "item123", "name": "Test Item", "details": {"color": "red", "size": "large"}, "metadata": {"source": "db"}}]
        summary3 = await summarize_tool_result_conversationally(
            "Get details for item123 (complex_data)", plan3, results3, True, mock_llm_provider_instance
        )
        print(f"\nUser Query: Get details for item123 (complex_data)\nAI Summary 3: {summary3}")
        assert "processed the complex data" in summary3.lower() # Checks if LLM got a summary of the dict

        # Test Case 4: Success with a very long string result
        plan4 = [{"tool_name": "read_file", "args": ("long_file.txt",), "kwargs": {}}]
        results4 = ["This is a very long string result that definitely exceeds one hundred and fifty characters in total length, so it should be truncated by the summarizer if it's not handled by a specific tool summary_str field which in this mock case it is not." * 2]
        summary4 = await summarize_tool_result_conversationally(
            "Read long_file.txt", plan4, results4, True, mock_llm_provider_instance
        )
        print(f"\nUser Query: Read long_file.txt\nAI Summary 4: {summary4}")
        # The assertion here depends on how the LLM summarizes it.
        # For this test, we're mostly interested in the prompt sent to the LLM.
        # The mock LLM just returns "I've completed the steps you asked for." if not specific.

        # Test Case 5: No actions taken
        plan5 = []
        results5 = []
        summary5 = await summarize_tool_result_conversationally(
            "Do nothing specific.", plan5, results5, True, mock_llm_provider_instance
        )
        print(f"\nUser Query: Do nothing specific.\nAI Summary 5: {summary5}")
        assert "No actions were taken." in _mock_captured_code_gen_prompt # Check the prompt to LLM for this

    asyncio.run(test_conversational_summary())
```
