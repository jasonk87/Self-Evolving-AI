import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import os
import sys
from typing import List

# Add project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path: # pragma: no cover
    sys.path.insert(0, project_root)

from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner, LLM_HP_OUTLINE_GENERATION_PROMPT_TEMPLATE
from ai_assistant.llm_interface.ollama_client import OllamaProvider # For spec in mock

class TestHierarchicalPlannerOutline(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_llm_provider = AsyncMock(spec=OllamaProvider)
        # Mock the specific method that will be called by the planner
        # If HierarchicalPlanner directly calls invoke_ollama_model_async, mock that.
        # If it calls a more generic method like 'get_completion', mock that.
        # Based on HierarchicalPlanner code, it calls invoke_ollama_model_async.
        self.mock_llm_provider.invoke_ollama_model_async = AsyncMock()

        self.planner = HierarchicalPlanner(llm_provider=self.mock_llm_provider)

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_successful_outline_generation(self, mock_get_model):
        mock_get_model.return_value = "mock_outline_model"
        user_goal = "Create a web application for task management."
        llm_response = """
        - User Authentication
        - Task Creation and Management
        - List and Filter Tasks
        - Real-time Notifications
        - API Endpoints
        """
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response

        expected_outline = [
            "User Authentication",
            "Task Creation and Management",
            "List and Filter Tasks",
            "Real-time Notifications",
            "API Endpoints"
        ]

        outline = await self.planner.generate_high_level_outline(user_goal)
        self.assertEqual(outline, expected_outline)

        expected_prompt = LLM_HP_OUTLINE_GENERATION_PROMPT_TEMPLATE.format(user_goal=user_goal)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once_with(
            expected_prompt,
            model_name="mock_outline_model",
            temperature=0.6,
            max_tokens=500
        )
        mock_get_model.assert_called_once_with("hierarchical_planning_outline")

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_outline_generation_with_project_context(self, mock_get_model):
        mock_get_model.return_value = "mock_outline_model_ctx"
        user_goal = "Refactor the user profile page."
        project_context = "The project is a Flask application using SQLAlchemy. User model exists."
        llm_response = "- Update Profile Form\n- Update Profile View Logic\n- Database Schema Migration (if any)"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response

        expected_outline = [
            "Update Profile Form",
            "Update Profile View Logic",
            "Database Schema Migration (if any)"
        ]

        outline = await self.planner.generate_high_level_outline(user_goal, project_context=project_context)
        self.assertEqual(outline, expected_outline)

        expected_prompt = LLM_HP_OUTLINE_GENERATION_PROMPT_TEMPLATE.format(user_goal=user_goal)
        expected_prompt += f"\n\nExisting project context to consider:\n{project_context}"

        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once_with(
            expected_prompt,
            model_name="mock_outline_model_ctx",
            temperature=0.6,
            max_tokens=500
        )

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_parsing_various_llm_outputs(self, mock_get_model):
        mock_get_model.return_value = "mock_parser_model"
        user_goal = "Parse test"

        test_cases = {
            "hyphen_list": "- Item 1\n- Item 2\n -  Item 3 (with spaces)",
            "asterisk_list": "* Item A\n* Item B",
            "numbered_list": "1. First\n2. Second\n3.Third",
            "mixed_markers": "- Mix 1\n* Mix 2\n1. Mix 3",
            "no_markers_just_newlines": "Line One\nLine Two\nLine Three",
            "extra_whitespace_and_blank_lines": "\n  - Whitespace Item 1  \n\n* Whitespace Item 2\n\n",
        }
        expected_results = {
            "hyphen_list": ["Item 1", "Item 2", "Item 3 (with spaces)"],
            "asterisk_list": ["Item A", "Item B"],
            "numbered_list": ["First", "Second", "Third"], # Note: "3.Third" becomes "Third"
            "mixed_markers": ["Mix 1", "Mix 2", "Mix 3"],
            "no_markers_just_newlines": ["Line One", "Line Two", "Line Three"],
            "extra_whitespace_and_blank_lines": ["Whitespace Item 1", "Whitespace Item 2"],
        }

        for name, llm_response_str in test_cases.items():
            with self.subTest(format=name):
                self.mock_llm_provider.invoke_ollama_model_async.reset_mock() # Reset for each subtest
                self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response_str

                outline = await self.planner.generate_high_level_outline(user_goal)
                self.assertEqual(outline, expected_results[name])

    async def test_empty_goal_returns_empty_list(self):
        outline = await self.planner.generate_high_level_outline("")
        self.assertEqual(outline, [])
        self.mock_llm_provider.invoke_ollama_model_async.assert_not_called()

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_llm_empty_response_returns_empty_list(self, mock_get_model):
        mock_get_model.return_value = "mock_empty_model"
        user_goal = "A valid goal"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "" # Empty string
        outline = await self.planner.generate_high_level_outline(user_goal)
        self.assertEqual(outline, [])

        self.mock_llm_provider.invoke_ollama_model_async.return_value = None # None response
        outline_none = await self.planner.generate_high_level_outline(user_goal)
        self.assertEqual(outline_none, [])

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_llm_error_returns_empty_list(self, mock_get_model):
        mock_get_model.return_value = "mock_error_model"
        user_goal = "Goal that causes LLM error"
        self.mock_llm_provider.invoke_ollama_model_async.side_effect = Exception("LLM API is down")

        # Capture print output to check for error logging (if implemented with print)
        with patch('builtins.print') as mock_print:
            outline = await self.planner.generate_high_level_outline(user_goal)

        self.assertEqual(outline, [])
        # Check if the error was "logged" (printed)
        error_logged = False
        for call_arg in mock_print.call_args_list:
            if "Error during LLM call" in str(call_arg):
                error_logged = True
                break
        self.assertTrue(error_logged, "Error message from LLM exception was not printed.")


if __name__ == '__main__': # pragma: no cover
    unittest.main()
