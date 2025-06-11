import unittest
from unittest.mock import AsyncMock, MagicMock
import json
from typing import List, Dict, Any, Optional
import asyncio # Required for running async tests if not using IsolatedAsyncioTestCase in some environments

try:
    from ai_assistant.utils.conversational_helpers import summarize_tool_result_conversationally, LLM_CONVERSATIONAL_SUMMARY_PROMPT_TEMPLATE
    # Use the actual OllamaProvider for type hinting if possible, or a generic mock type
    from ai_assistant.llm_interface.ollama_client import OllamaProvider
    from ai_assistant.config import get_model_for_task # To ensure it's mockable if needed
except ImportError: # pragma: no cover
    import sys
    import os
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.utils.conversational_helpers import summarize_tool_result_conversationally, LLM_CONVERSATIONAL_SUMMARY_PROMPT_TEMPLATE
    from ai_assistant.llm_interface.ollama_client import OllamaProvider
    from ai_assistant.config import get_model_for_task


class TestConversationalHelpers(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_llm_provider = AsyncMock(spec=OllamaProvider)
        # Reset any captured prompts from previous tests if this mock were shared (it's not here)
        self.captured_prompt = None
        self.captured_model_name = None
        self.captured_temperature = None

        async def mock_invoke_side_effect(prompt, model_name, temperature):
            self.captured_prompt = prompt
            self.captured_model_name = model_name
            self.captured_temperature = temperature
            # Default mock response, can be overridden in each test
            return "Default conversational summary from mock LLM."

        self.mock_llm_provider.invoke_ollama_model_async.side_effect = mock_invoke_side_effect

    async def test_summarize_success_simple_result(self):
        plan = [{"tool_name": "get_weather", "args": ("London",), "kwargs": {}}]
        results = ["The weather in London is sunny."]
        query = "Weather in London?"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = "It's sunny in London today!"

        summary = await summarize_tool_result_conversationally(
            query, plan, results, True, self.mock_llm_provider
        )
        self.assertEqual(summary, "It's sunny in London today!")
        self.assertIn(query, self.captured_prompt)
        self.assertIn("Step 1: Ran tool 'get_weather' with args ('London',) and kwargs {}. Result: The weather in London is sunny.", self.captured_prompt)
        self.assertIn("Overall outcome of the attempt: Succeeded", self.captured_prompt)
        self.assertEqual(self.captured_temperature, 0.6) # Default temp

    async def test_summarize_failure_with_exception(self):
        plan = [{"tool_name": "divide_numbers", "args": (10, 0), "kwargs": {}}]
        results = [ZeroDivisionError("division by zero")]
        query = "10/0"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = "It looks like there was an attempt to divide by zero, which isn't possible."

        summary = await summarize_tool_result_conversationally(
            query, plan, results, False, self.mock_llm_provider
        )
        self.assertEqual(summary, "It looks like there was an attempt to divide by zero, which isn't possible.")
        self.assertIn("Result: Error: ZeroDivisionError: division by zero", self.captured_prompt)
        self.assertIn("Overall outcome of the attempt: Failed", self.captured_prompt)

    async def test_summarize_complex_dict_result_no_summary_str(self):
        plan = [{"tool_name": "get_user_details", "args": ("user123",), "kwargs": {}}]
        results = [{"id": "user123", "name": "John Doe", "email": "john@example.com", "prefs": {"theme": "dark", "notifications": "daily"}}]
        query = "User details for user123"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = "I found details for John Doe, including their email and preferences."

        summary = await summarize_tool_result_conversationally(
            query, plan, results, True, self.mock_llm_provider
        )
        self.assertEqual(summary, "I found details for John Doe, including their email and preferences.")
        self.assertIn("Output data (dict with 4 keys: ['id', 'name', 'email']...)", self.captured_prompt)

    async def test_summarize_complex_dict_result_with_summary_str(self):
        plan = [{"tool_name": "get_user_details_v2", "args": ("user456",), "kwargs": {}}]
        results = [{"id": "user456", "name": "Jane Doe", "summary_str": "User Jane Doe, premium member since 2022."}]
        query = "User details for user456"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = "User Jane Doe is a premium member since 2022."

        summary = await summarize_tool_result_conversationally(
            query, plan, results, True, self.mock_llm_provider
        )
        self.assertEqual(summary, "User Jane Doe is a premium member since 2022.")
        self.assertIn("Result: User Jane Doe, premium member since 2022.", self.captured_prompt)


    async def test_summarize_llm_call_fails_returns_none(self):
        plan = [{"tool_name": "get_weather", "args": ("Paris",), "kwargs": {}}]
        results = ["Cloudy"]
        query = "Weather in Paris?"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = None # Simulate LLM returning None

        summary = await summarize_tool_result_conversationally(
            query, plan, results, True, self.mock_llm_provider
        )
        self.assertEqual(summary, "I've processed your request.")

    async def test_summarize_llm_call_raises_exception(self):
        plan = [{"tool_name": "get_weather", "args": ("Berlin",), "kwargs": {}}]
        results = ["Rainy"]
        query = "Weather in Berlin?"

        self.mock_llm_provider.invoke_ollama_model_async.side_effect = Exception("LLM network error")

        summary = await summarize_tool_result_conversationally(
            query, plan, results, True, self.mock_llm_provider
        )
        self.assertEqual(summary, "I have processed your request. The detailed technical summary is available if needed.")

    async def test_actions_results_formatting_long_strings_and_lists(self):
        plan = [
            {"tool_name": "read_long_file", "args": ("big.txt",), "kwargs": {}},
            {"tool_name": "get_list_items", "args": (), "kwargs": {}}
        ]
        long_string = "abcdefghijklmnopqrstuvwxyz" * 10 # 260 chars
        list_data = [f"item_{i}" for i in range(10)]
        results = [long_string, list_data]
        query = "Process long data"

        await summarize_tool_result_conversationally(
            query, plan, results, True, self.mock_llm_provider
        )

        self.assertIn(f"Result: {long_string[:147]}...", self.captured_prompt)
        self.assertIn(f"Result: Output data (list with 10 items: {str(list_data[:3])[:100]}...)", self.captured_prompt)

    async def test_no_actions_taken(self):
        plan = []
        results = []
        query = "Do I exist?"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = "It seems no actions were taken for your request."

        summary = await summarize_tool_result_conversationally(
            query, plan, results, False, self.mock_llm_provider # Success False if no plan can be made
        )
        self.assertEqual(summary, "It seems no actions were taken for your request.")
        self.assertIn("Actions and Results:\nNo actions were taken.", self.captured_prompt)
        self.assertIn("Overall outcome of the attempt: Failed", self.captured_prompt)


if __name__ == '__main__': # pragma: no cover
    unittest.main()
```
