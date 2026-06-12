from typing import List, Dict, Any, Optional
import json
import logging
import asyncio


try:
    from ai_assistant.llm_interface.ollama_client import OllamaProvider
    from ai_assistant.config import get_model_for_task, DEFAULT_MODEL
except ImportError: # pragma: no cover
    print("Warning: Could not import OllamaProvider or get_model_for_task from standard paths. Using placeholder for direct script execution if applicable.")
    OllamaProvider = type('OllamaProvider', (object,), {})
    def get_model_for_task(task_type: str) -> str: return "mock_model"


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
- **CRITICAL:** You MUST include the ACTUAL SPECIFIC VALUES returned by the tool (e.g., the exact temperature number, the specific file names).
- **DO NOT** use placeholders like "[temperature]", "[filename]", or "[insert value here]". You must use the REAL data provided in the "Actions and Results" section.
- If the tool result says "temperature: 24", your response MUST say "24", not "[temperature]".
- If partially successful, explain what worked and what didn't.
- If failed, explain the issue clearly but try to be helpful if possible (e.g., suggesting what they might try differently if it was a user error, or stating you've logged the internal error).
- Avoid overly technical jargon unless the user's query was highly technical.
- Synthesize the information natively into your sentence.
- Do not start with phrases like "Based on this..." or "Here's a summary...". Just give the direct conversational response.
- Focus on conciseness and relevance to the user's goal.
- **DYNAMIC HTML:** You can output dynamic HTML to visually assist the user.
    - If the user asks for a visualization, table, list, or if the data is better presented visually (e.g. status reports, simple charts), enclose the HTML in a block starting with ```html-dynamic
    - Available CSS classes: `.ai-card` (container), `.ai-table` (clean tables), `.ai-metric` (badges/pills), `.ai-status-good` (green), `.ai-status-bad` (red), `.ai-status-warning` (yellow).
    - Example:
      ```html-dynamic
      <div class="ai-card">
        <h3>Project Status</h3>
        <table class="ai-table">
          <tr><th>Service</th><th>Status</th></tr>
          <tr><td>Database</td><td><span class="ai-metric ai-status-good">Online</span></td></tr>
        </table>
      </div>
      ```
    - The HTML will be rendered safely in the chat using DOMPurify.
    - **ENCOURAGEMENT:** You are highly encouraged to use this for:
        - **Tables:** Comparison of data, lists of files, status reports.
        - **Cards:** Summarizing items (e.g. "Project A: Active", "Project B: Inactive").
        - **Badges:** highlighting status (e.g. `<span class="ai-metric ai-status-good">Active</span>`).
    - Use standard HTML5 tags (div, table, tr, td, th, span, ul, li, b, i, button).  
    - **Do NOT** use `<html>`, `<head>`, or `<body>` tags. Just the content snippet.

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
                    # Create a concise string representation of the dictionary
                    try:
                        result_summary = json.dumps(res, indent=2)
                        # Truncate if excessively long (e.g. > 2000 chars) to prevent context flooding
                        if len(result_summary) > 3000:
                             result_summary = result_summary[:3000] + "... (truncated)"
                    except (TypeError, OverflowError):
                        result_summary = str(res)
            elif isinstance(res, list):
                # IMPROVED TRUNCATION:
                # 1. Increase limit significantly (4000 chars) to allow context window to work.
                # 2. If list of dicts, try to inspect them for 'text' or 'summary' fields for cleaner output, 
                #    but fallback to raw dump if needed.
                list_sample = res[:20] # Inspect first 20 items (upped from 3)
                
                # Check directly if it looks like a list of learned facts (contain 'text' and 'fact_id')
                if list_sample and isinstance(list_sample[0], dict) and "text" in list_sample[0]:
                    # Format nicely for the LLM
                     formatted_items = [f"- {item.get('text', '')}" for item in list_sample]
                     list_str = "\n".join(formatted_items)
                     if len(res) > 20:
                         list_str += f"\n... ({len(res) - 20} more items)"
                     result_summary = f"List of Facts:\n{list_str}"
                else:
                    # Generic handling
                    list_str = str(list_sample)
                    if len(list_str) > 4000:
                        list_str = list_str[:4000] + "... (truncated)"
                    result_summary = f"Output data (list with {len(res)} items): {list_str}"

            elif isinstance(res, (str, int, float, bool)):
                result_summary = str(res)
            else:
                result_summary = f"Output of type {type(res).__name__}."

            # Relax generic char limit for the FINAL result_summary string too, 
            # to prevent the outer check from nuking our work above.
            if len(result_summary) > 5000:
                result_summary = result_summary[:4900] + "..."

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
        # Ensure the call is awaited
        summary_response_coro = llm_provider.invoke_ollama_model_async(
            prompt,
            model_name=target_model,
            temperature=0.6
        )
        summary_response = await summary_response_coro # Await the coroutine
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
    if not technical_error_message:
        return "An unexpected issue occurred, but no specific error message was available."

    query_for_prompt = original_user_query if original_user_query else "an unspecified task"

    prompt = LLM_REPHRASE_ERROR_PROMPT_TEMPLATE.format(
        original_user_query=query_for_prompt,
        technical_error_message=technical_error_message
    )

    try:
        target_model = (
            model_name or
            get_model_for_task("error_rephrasing") or
            get_model_for_task("conversational_response") or
            DEFAULT_MODEL
        )
        if target_model == DEFAULT_MODEL:
            logger.warning(f"No specific model for 'error_rephrasing' or 'conversational_response'. Using system DEFAULT_MODEL: {target_model}")

        logger.info(f"Rephrasing error with model {target_model}. Original error: {technical_error_message[:100]}...")

        llm_response_coro = llm_provider.invoke_ollama_model_async(
            prompt,
            model_name=target_model,
            temperature=0.5
        )
        llm_response = await llm_response_coro # Await the coroutine

        if llm_response and llm_response.strip():
            return llm_response.strip()
        else:
            logger.warning(f"LLM returned empty response for error rephrasing. Technical error: {technical_error_message}")
            truncated_error = technical_error_message[:500] + "..." if len(technical_error_message) > 500 else technical_error_message
            return f"I encountered an issue processing your request for '{query_for_prompt}'. The technical details are: {truncated_error}"

    except Exception as e: # pragma: no cover
        logger.error(f"Error during LLM call for error rephrasing: {e}. Technical error: {technical_error_message}", exc_info=True)
        truncated_error = technical_error_message[:500] + "..." if len(technical_error_message) > 500 else technical_error_message
        return f"I ran into a problem with your request for '{query_for_prompt}'. The specific technical error was: {truncated_error}"


if __name__ == '__main__': # pragma: no cover
    _mock_captured_code_gen_prompt_main: Optional[str] = None

    class MockOllamaProviderMain:
        async def invoke_ollama_model_async(self, prompt: str, model_name: str, temperature: float) -> Optional[str]:
            global _mock_captured_code_gen_prompt_main
            logger.info(f"\n--- Mock LLM Prompt (Model: {model_name}, Temp: {temperature}) ---")
            logger.info(prompt)
            logger.info(f"--- End Mock LLM Prompt ---")

            if "Actions and Results:" in prompt:
                _mock_captured_code_gen_prompt_main = prompt
                if "list_files" in prompt and "found 3 files" in prompt:
                    return "Okay, I looked in the main directory and found 3 files for you: file1.txt, file2.py, and notes.md."
                elif "failed" in prompt.lower() and ("calculate_sum" in prompt or "ValueError" in prompt) :
                    return "I tried to do that, but unfortunately, I ran into an issue with the 'calculate_sum' tool. It seems it received invalid input."
                elif "complex_data" in prompt:
                    return "I've processed the complex data you provided."
                return "I've completed the steps you asked for."

            elif "User-friendly explanation:" in prompt:
                if "ValueError" in prompt and "my_notes.txt" in prompt:
                    return "It looks like there was a small mix-up. I tried to use 'my_notes.txt' as a number, which didn't quite work. If you were trying to work with a file, maybe a different command would help?"
                return "I'm sorry, something went wrong. I've noted the technical details."

            return "Generic mock response."


    async def test_conversational_summary():
        global _mock_captured_code_gen_prompt_main
        mock_llm_provider_instance = MockOllamaProviderMain()

        logger.info("\n--- Testing summarize_tool_result_conversationally ---")
        plan1 = [{"tool_name": "list_files", "args": ("/test",), "kwargs": {}}]
        results1 = [{"files": ["file1.txt", "file2.py", "notes.md"], "count": 3, "summary_str": "found 3 files in /test"}]
        summary1 = await summarize_tool_result_conversationally(
            "What files are in /test?", plan1, results1, True, mock_llm_provider_instance
        )
        logger.info(f"\nUser Query: What files are in /test?\nAI Summary 1: {summary1}")
        assert "found 3 files" in summary1.lower()

        plan2 = [{"tool_name": "calculate_sum", "args": ("a", "5"), "kwargs": {}}]
        results2 = [ValueError("Invalid input for sum: 'a' is not a number.")]
        summary2 = await summarize_tool_result_conversationally(
            "Add a and 5", plan2, results2, False, mock_llm_provider_instance
        )
        logger.info(f"\nUser Query: Add a and 5\nAI Summary 2: {summary2}")
        assert "invalid input" in summary2.lower()
        assert "calculate_sum" in summary2.lower()

        plan3 = [{"tool_name": "get_item_details", "args": ("item123",), "kwargs": {}}]
        results3 = [{"item_id": "item123", "name": "Test Item", "details": {"color": "red", "size": "large"}, "metadata": {"source": "db"}}]
        summary3 = await summarize_tool_result_conversationally(
            "Get details for item123 (complex_data)", plan3, results3, True, mock_llm_provider_instance
        )
        logger.info(f"\nUser Query: Get details for item123 (complex_data)\nAI Summary 3: {summary3}")
        assert "processed the complex data" in summary3.lower()

        _mock_captured_code_gen_prompt_main = None
        plan5 = []
        results5 = []
        summary5 = await summarize_tool_result_conversationally(
            "Do nothing specific.", plan5, results5, True, mock_llm_provider_instance
        )
        logger.info(f"\nUser Query: Do nothing specific.\nAI Summary 5: {summary5}")
        assert _mock_captured_code_gen_prompt_main is not None, "Prompt should have been captured"
        if _mock_captured_code_gen_prompt_main:
          assert "No actions were taken." in _mock_captured_code_gen_prompt_main

    async def test_rephrase_error():
        mock_llm_provider_instance = MockOllamaProviderMain()
        logger.info("\n--- Testing rephrase_error_message_conversationally ---")

        error1 = "Tool 'add_numbers' failed. Error: ValueError: 'a' and 'b' must be integers. Got 'my_notes.txt'."
        query1 = "Add my notes file."
        rephrased1 = await rephrase_error_message_conversationally(error1, query1, mock_llm_provider_instance)
        logger.info(f"Original Error 1: {error1}\nRephrased 1: {rephrased1}\n")
        assert "mix-up" in rephrased1.lower()
        assert "my_notes.txt" in rephrased1
        assert "add_numbers" in rephrased1

        error2 = "Some obscure internal error: NullPointerException at Java.Lang.System.InternalError"
        query2 = "Do complex task"
        
        # Override the instance method directly for this test case
        async def mock_return_none(*args, **kwargs):
            return None
        
        original_method = mock_llm_provider_instance.invoke_ollama_model_async
        mock_llm_provider_instance.invoke_ollama_model_async = mock_return_none

        rephrased2 = await rephrase_error_message_conversationally(error2, query2, mock_llm_provider_instance)
        logger.info(f"Original Error 2: {error2}\nRephrased 2 (LLM fail/None): {rephrased2}\n")
        assert error2 in rephrased2
        assert query2 in rephrased2

        # Test Exception
        async def mock_raise_exception(*args, **kwargs):
            raise Exception("Network connection to LLM failed")
        
        try:
            mock_llm_provider_instance.invoke_ollama_model_async = mock_raise_exception

            error3 = "Database timeout"
            query3 = "Fetch all user records"
            rephrased3 = await rephrase_error_message_conversationally(error3, query3, mock_llm_provider_instance)
            logger.info(f"Original Error 3: {error3}\nRephrased 3 (LLM exception): {rephrased3}\n")
            assert error3 in rephrased3
            assert query3 in rephrased3
        finally:
            # Restore
            mock_llm_provider_instance.invoke_ollama_model_async = original_method


    async def main_tests():
        await test_conversational_summary()
        await test_rephrase_error()

    asyncio.run(main_tests())
