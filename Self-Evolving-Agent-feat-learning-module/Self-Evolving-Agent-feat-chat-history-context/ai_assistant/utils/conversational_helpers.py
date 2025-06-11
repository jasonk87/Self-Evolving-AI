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

LLM_REPHRASE_ERROR_PROMPT_TEMPLATE = """
You are an AI assistant translating a technical error message into a user-friendly, conversational explanation.
The user was trying to achieve the following:
User's original request: "{original_user_query}"

The system encountered the following technical error:
Technical error: "{technical_error_message}"

Please rephrase this error in a clear, concise, and helpful way for the user.
- Avoid overly technical jargon unless essential and explained.
- If possible, offer a very brief, general suggestion on what the user might check or try next, or assure them the issue has been logged if it seems like an internal problem.
- Do not start with phrases like "Based on the error..." or "It seems there was an error...". Directly provide the helpful explanation.
- Aim for a supportive and understanding tone.

Example 1 (User Input Error):
User's original request: "Add my notes file."
Technical error: "Tool 'add_numbers' failed. Error: ValueError: 'a' and 'b' must be integers. Got 'my_notes.txt'."
User-friendly explanation: "It looks like there was a small mix-up. I tried to use 'my_notes.txt' as a number for the 'add_numbers' tool, which didn't quite work as it expects numbers. If you were trying to do something with a file, perhaps a different command or specifying the file operation more clearly might help?"

Example 2 (System/Tool Error):
User's original request: "Calculate the trajectory for the lunar launch."
Technical error: "Tool 'lunar_trajectory_calculator' failed. Error: NullPointerException at com.space.Calculator.getGravity(Calculator.java:123). Associated task task_launch_001."
User-friendly explanation: "I encountered an unexpected issue while trying to calculate the lunar trajectory (task task_launch_001). I've logged the technical details, and the team will look into it. Sorry about that! Perhaps we could try a different approach or simplify the request for now?"

Example 3 (Generic Error):
User's original request: "Update my profile."
Technical error: "Connection timed out to database server."
User-friendly explanation: "I'm having trouble connecting to the database right now to update your profile. Please try again in a few moments. If the problem persists, I'll make sure to log it."

User-friendly explanation:
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
                if "summary_str" in res and isinstance(res["summary_str"], str):
                    result_summary = res["summary_str"]
                elif "status" in res and res["status"] == "error" and "message" in res:
                    result_summary = f"Tool Error: {res['message']}"
                else:
                    result_summary = f"Output data (dict with {len(res)} keys: {list(res.keys())[:3]}{'...' if len(res.keys()) > 3 else ''})"
            elif isinstance(res, list):
                result_summary = f"Output data (list with {len(res)} items: {str(res[:3])[:100]}{'...' if len(res) > 3 or len(str(res[:3])) > 100 else ''})"
            elif isinstance(res, (str, int, float, bool)):
                result_summary = str(res)
            else:
                result_summary = f"Output of type {type(res).__name__}."

            if len(result_summary) > 150:
                result_summary = result_summary[:147] + "..."

        actions_summary_parts.append(
            f"Step {i+1}: Ran tool '{tool_name}' with args {args} and kwargs {kwargs}. Result: {result_summary}"
        )

    actions_and_results_summary = "\n".join(actions_summary_parts) if actions_summary_parts else "No actions were taken."

    if overall_success:
        overall_success_str = "Succeeded"
    else:
        overall_success_str = "Failed"

    prompt = LLM_CONVERSATIONAL_SUMMARY_PROMPT_TEMPLATE.format(
        original_user_query=original_user_query,
        actions_and_results_summary=actions_and_results_summary,
        overall_success_str=overall_success_str
    )

    try:
        target_model = model_name or get_model_for_task("conversational_response")
        summary_response = await llm_provider.invoke_ollama_model_async(
            prompt,
            model_name=target_model,
            temperature=0.6
        )
        return summary_response.strip() if summary_response else "I've processed your request."
    except Exception as e:
        logger.error(f"Error invoking LLM for conversational summary: {e}", exc_info=True)
        return "I have processed your request. The detailed technical summary is available if needed."

async def rephrase_error_message_conversationally(
    technical_error_message: str,
    original_user_query: Optional[str],
    llm_provider: OllamaProvider,
    model_name: Optional[str] = None
) -> str:
    """
    Rephrases a technical error message into a user-friendly, conversational explanation using an LLM.
    """
    if not technical_error_message:
        return "An unexpected issue occurred, but no specific error message was available."

    query_for_prompt = original_user_query if original_user_query else "an unspecified task"

    prompt = LLM_REPHRASE_ERROR_PROMPT_TEMPLATE.format(
        original_user_query=query_for_prompt,
        technical_error_message=technical_error_message
    )

    try:
        target_model = model_name
        if not target_model:
            target_model = get_model_for_task("error_rephrasing")
        if not target_model: # Fallback if "error_rephrasing" is not in config
            target_model = get_model_for_task("conversational_response")
        if not target_model: # Further fallback
            target_model = "mistral" # A common default, replace if your default is different
            logger.warning(f"No specific model for 'error_rephrasing' or 'conversational_response'. Using hardcoded default: {target_model}")

        logger.info(f"Rephrasing error with model {target_model}. Original error: {technical_error_message[:100]}...")

        llm_response = await llm_provider.invoke_ollama_model_async(
            prompt,
            model_name=target_model,
            temperature=0.5
        )

        if llm_response and llm_response.strip():
            return llm_response.strip()
        else:
            logger.warning(f"LLM returned empty response for error rephrasing. Technical error: {technical_error_message}")
            return f"I encountered an issue processing your request for '{query_for_prompt}'. The technical details are: {technical_error_message}"

    except Exception as e: # pragma: no cover
        logger.error(f"Error during LLM call for error rephrasing: {e}. Technical error: {technical_error_message}", exc_info=True)
        return f"I ran into a problem with your request for '{query_for_prompt}'. The specific technical error was: {technical_error_message}"


if __name__ == '__main__': # pragma: no cover
    import asyncio
    from unittest.mock import MagicMock, AsyncMock

    # Temporary global variable for mock_invoke_side_effect in test_conversational_summary
    _mock_captured_code_gen_prompt = None


    # Mock OllamaProvider for testing
    class MockOllamaProvider:
        async def invoke_ollama_model_async(self, prompt: str, model_name: str, temperature: float) -> Optional[str]:
            global _mock_captured_code_gen_prompt # Allow modification for test_conversational_summary

            print(f"\n--- LLM Prompt (Model: {model_name}, Temp: {temperature}) ---")
            print(prompt)
            print(f"--- End LLM Prompt ---")

            # Specific mock responses for summarize_tool_result_conversationally
            if "Actions and Results:" in prompt: # Heuristic for summary prompt
                _mock_captured_code_gen_prompt = prompt # Capture for test_conversational_summary's assertion
                if "list_files" in prompt and "found 3 files" in prompt:
                    return "Okay, I looked in the main directory and found 3 files for you: file1.txt, file2.py, and notes.md."
                elif "failed" in prompt.lower() and ("calculate_sum" in prompt or "ValueError" in prompt) :
                    return "I tried to do that, but unfortunately, I ran into an issue with the 'calculate_sum' tool. It seems it received invalid input."
                elif "complex_data" in prompt:
                    return "I've processed the complex data you provided."
                return "I've completed the steps you asked for." # Default for summary

            # Specific mock responses for rephrase_error_message_conversationally
            elif "User-friendly explanation:" in prompt: # Heuristic for rephrase error prompt
                if "ValueError" in prompt and "my_notes.txt" in prompt:
                    return "It looks like there was a small mix-up. I tried to use 'my_notes.txt' as a number, which didn't quite work. If you were trying to work with a file, maybe a different command would help?"
                return "I'm sorry, something went wrong. I've noted the technical details." # Default for rephrase

            return "Generic mock response."


    async def test_conversational_summary():
        global _mock_captured_code_gen_prompt # Access global for assertion
        mock_llm_provider_instance = MockOllamaProvider()

        print("\n--- Testing summarize_tool_result_conversationally ---")
        # Test Case 1: Success with simple list result
        plan1 = [{"tool_name": "list_files", "args": ("/test",), "kwargs": {}}]
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
        assert "processed the complex data" in summary3.lower()

        # Test Case 5: No actions taken
        _mock_captured_code_gen_prompt = None # Reset for this specific assertion
        plan5 = []
        results5 = []
        summary5 = await summarize_tool_result_conversationally(
            "Do nothing specific.", plan5, results5, True, mock_llm_provider_instance
        )
        print(f"\nUser Query: Do nothing specific.\nAI Summary 5: {summary5}")
        assert _mock_captured_code_gen_prompt is not None, "Prompt should have been captured"
        if _mock_captured_code_gen_prompt: # Check to satisfy type checker
          assert "No actions were taken." in _mock_captured_code_gen_prompt

    async def test_rephrase_error():
        mock_llm_provider_instance = MockOllamaProvider()
        print("\n--- Testing rephrase_error_message_conversationally ---")

        # Test 1: User input type error
        error1 = "Tool 'add_numbers' failed. Error: ValueError: 'a' and 'b' must be integers. Got 'my_notes.txt'."
        query1 = "Add my notes file."
        rephrased1 = await rephrase_error_message_conversationally(error1, query1, mock_llm_provider_instance)
        print(f"Original Error 1: {error1}\nRephrased 1: {rephrased1}\n")
        assert "mix-up" in rephrased1.lower()
        assert "my_notes.txt" in rephrased1 # Check if context from error is in rephrased
        assert "add_numbers" in rephrased1

        # Test 2: LLM fails to rephrase (e.g., returns empty or default from mock)
        # For this, we need the mock to return its default "I'm sorry..."
        # We can achieve this by providing an error that doesn't match specific conditions in the mock.
        error2 = "Some obscure internal error: NullPointerException at Java.Lang.System.InternalError"
        query2 = "Do complex task"
        # Temporarily change side effect for this specific test if needed, or rely on default mock behavior
        original_side_effect = mock_llm_provider_instance.invoke_ollama_model_async
        mock_llm_provider_instance.invoke_ollama_model_async = AsyncMock(return_value=None) # Simulate LLM returning None

        rephrased2 = await rephrase_error_message_conversationally(error2, query2, mock_llm_provider_instance)
        print(f"Original Error 2: {error2}\nRephrased 2 (LLM fail/None): {rephrased2}\n")
        assert error2 in rephrased2 # Fallback should include original error if LLM returns None
        assert query2 in rephrased2

        # Test 3: LLM call itself raises an exception
        mock_llm_provider_instance.invoke_ollama_model_async.side_effect = Exception("Network connection to LLM failed")
        error3 = "Database timeout"
        query3 = "Fetch all user records"
        rephrased3 = await rephrase_error_message_conversationally(error3, query3, mock_llm_provider_instance)
        print(f"Original Error 3: {error3}\nRephrased 3 (LLM exception): {rephrased3}\n")
        assert error3 in rephrased3 # Fallback should include original error
        assert query3 in rephrased3

        # Restore general mock behavior if it was changed for a specific test
        mock_llm_provider_instance.invoke_ollama_model_async = original_side_effect


    async def main_tests():
        await test_conversational_summary()
        await test_rephrase_error()

    asyncio.run(main_tests())
```
