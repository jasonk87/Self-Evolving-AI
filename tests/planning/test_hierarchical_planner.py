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


class TestHierarchicalPlannerDetailedTasks(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_llm_provider = AsyncMock(spec=OllamaProvider)
        self.mock_llm_provider.invoke_ollama_model_async = AsyncMock() # Specific mock for the method
        self.planner = HierarchicalPlanner(llm_provider=self.mock_llm_provider)

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_successful_detailed_task_generation(self, mock_get_model):
        mock_get_model.return_value = "mock_detailed_task_model"
        user_goal = "Develop a snake game."
        outline_item = "Game Core Logic"
        llm_response = """
        - Define snake data structure
        - Implement movement
        - Implement food mechanism
        """
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response

        expected_tasks = [
            "Define snake data structure",
            "Implement movement",
            "Implement food mechanism"
        ]

        detailed_tasks = await self.planner.generate_detailed_tasks_for_outline_item(outline_item, user_goal)
        self.assertEqual(detailed_tasks, expected_tasks)

        from ai_assistant.planning.hierarchical_planner import LLM_HP_DETAILED_TASK_BREAKDOWN_PROMPT_TEMPLATE # Import for prompt
        expected_prompt = LLM_HP_DETAILED_TASK_BREAKDOWN_PROMPT_TEMPLATE.format(
            user_goal=user_goal,
            outline_item=outline_item,
            project_context_section="" # No project context in this test
        )
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once_with(
            expected_prompt,
            model_name="mock_detailed_task_model",
            temperature=0.5,
            max_tokens=700
        )
        mock_get_model.assert_called_once_with("hierarchical_planning_tasks")

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_detailed_task_generation_with_project_context(self, mock_get_model):
        mock_get_model.return_value = "mock_detailed_task_model_ctx"
        user_goal = "Refactor API endpoints."
        outline_item = "User Authentication API"
        project_context = "Current API uses Flask-RESTful. JWT for tokens."
        llm_response = "- Update /login endpoint for JWT\n- Update /register endpoint\n- Add /refresh_token endpoint"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response

        expected_tasks = [
            "Update /login endpoint for JWT",
            "Update /register endpoint",
            "Add /refresh_token endpoint"
        ]
        detailed_tasks = await self.planner.generate_detailed_tasks_for_outline_item(outline_item, user_goal, project_context)
        self.assertEqual(detailed_tasks, expected_tasks)

        from ai_assistant.planning.hierarchical_planner import LLM_HP_DETAILED_TASK_BREAKDOWN_PROMPT_TEMPLATE
        project_context_section = f"\nExisting project context to consider:\n{project_context}"
        expected_prompt = LLM_HP_DETAILED_TASK_BREAKDOWN_PROMPT_TEMPLATE.format(
            user_goal=user_goal,
            outline_item=outline_item,
            project_context_section=project_context_section
        )
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once_with(
            expected_prompt,
            model_name="mock_detailed_task_model_ctx",
            temperature=0.5,
            max_tokens=700
        )

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_parsing_various_llm_detailed_task_outputs(self, mock_get_model):
        mock_get_model.return_value = "mock_parser_model_detailed"
        user_goal = "Test parsing for detailed tasks"
        outline_item = "Any outline item"

        test_cases = {
            "hyphen_list": "- Task 1\n- Task 2\n -  Task 3 (with spaces)",
            "asterisk_list": "* Detail A\n* Detail B",
            "numbered_list": "1. First Detail\n2. Second Detail",
            "no_markers": "Subtask One\nSubtask Two\nSubtask Three",
        }
        expected_results = {
            "hyphen_list": ["Task 1", "Task 2", "Task 3 (with spaces)"],
            "asterisk_list": ["Detail A", "Detail B"],
            "numbered_list": ["First Detail", "Second Detail"],
            "no_markers": ["Subtask One", "Subtask Two", "Subtask Three"],
        }

        for name, llm_response_str in test_cases.items():
            with self.subTest(format=name):
                self.mock_llm_provider.invoke_ollama_model_async.reset_mock()
                self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response_str

                detailed_tasks = await self.planner.generate_detailed_tasks_for_outline_item(outline_item, user_goal)
                self.assertEqual(detailed_tasks, expected_results[name])

    async def test_empty_outline_item_returns_empty_list(self):
        detailed_tasks = await self.planner.generate_detailed_tasks_for_outline_item("", "Valid User Goal")
        self.assertEqual(detailed_tasks, [])
        self.mock_llm_provider.invoke_ollama_model_async.assert_not_called()

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_empty_user_goal_argument_still_calls_llm_if_outline_item_present(self, mock_get_model):
        # Current implementation calls LLM if outline_item is present, even if user_goal is empty.
        # This test verifies that behavior. Depending on desired strictness, this could be an error case.
        mock_get_model.return_value = "mock_model_empty_goal"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "- Task from empty goal context"

        detailed_tasks = await self.planner.generate_detailed_tasks_for_outline_item("Valid Outline Item", "")
        self.assertEqual(detailed_tasks, ["Task from empty goal context"])
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once() # LLM is called

    async def test_empty_user_goal_and_empty_outline_item_returns_empty_list(self):
        detailed_tasks = await self.planner.generate_detailed_tasks_for_outline_item("", "")
        self.assertEqual(detailed_tasks, [])
        self.mock_llm_provider.invoke_ollama_model_async.assert_not_called()


    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_llm_empty_response_for_detailed_tasks(self, mock_get_model):
        mock_get_model.return_value = "mock_empty_detailed_model"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "" # Empty string
        detailed_tasks = await self.planner.generate_detailed_tasks_for_outline_item("Item", "Goal")
        self.assertEqual(detailed_tasks, [])

        self.mock_llm_provider.invoke_ollama_model_async.return_value = None # None response
        detailed_tasks_none = await self.planner.generate_detailed_tasks_for_outline_item("Item", "Goal")
        self.assertEqual(detailed_tasks_none, [])

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_llm_error_for_detailed_tasks(self, mock_get_model):
        mock_get_model.return_value = "mock_error_detailed_model"
        self.mock_llm_provider.invoke_ollama_model_async.side_effect = Exception("LLM API Detailed Task Error")

        with patch('builtins.print') as mock_print: # To check if error is printed
            detailed_tasks = await self.planner.generate_detailed_tasks_for_outline_item("Item", "Goal")

        self.assertEqual(detailed_tasks, [])
        error_logged = any("Error during LLM call" in str(call_arg) for call_arg in mock_print.call_args_list)
        self.assertTrue(error_logged, "Error message from LLM exception was not printed for detailed tasks.")


class TestHierarchicalPlannerFullPlan(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # HierarchicalPlanner needs an llm_provider. We can pass a basic MagicMock
        # as the methods we are testing will have their LLM calls (via other planner methods) mocked.
        self.mock_llm_provider_for_init = MagicMock(spec=OllamaProvider)
        self.planner = HierarchicalPlanner(llm_provider=self.mock_llm_provider_for_init)

    @patch.object(HierarchicalPlanner, 'generate_project_plan_step_for_task', new_callable=AsyncMock)
    @patch.object(HierarchicalPlanner, 'generate_detailed_tasks_for_outline_item', new_callable=AsyncMock)
    @patch.object(HierarchicalPlanner, 'generate_high_level_outline', new_callable=AsyncMock)
    async def test_successful_full_plan_generation(
        self, mock_gen_outline, mock_gen_detailed_tasks, mock_gen_step_elaboration
    ):
        user_goal = "Create a complex application."
        project_context = "Existing backend available."

        # Configure mocks
        mock_gen_outline.return_value = ["Feature A", "Feature B"]

        def detailed_tasks_side_effect(outline_item, ug, pc):
            if outline_item == "Feature A":
                return ["Task A.1", "Task A.2"]
            elif outline_item == "Feature B":
                return ["Task B.1"]
            return []
        mock_gen_detailed_tasks.side_effect = detailed_tasks_side_effect

        def step_elaboration_side_effect(detailed_task, ug, pc):
            if detailed_task == "Task A.1":
                return {"type": "python_script", "details": {"script_content_prompt": "Prompt for A.1"}}
            elif detailed_task == "Task A.2":
                return {"type": "human_review_gate", "details": {"prompt_to_user": "Review A.2 output."}}
            elif detailed_task == "Task B.1":
                return {"type": "informational", "details": {"message": "Info for B.1"}}
            return None
        mock_gen_step_elaboration.side_effect = step_elaboration_side_effect

        full_plan = await self.planner.generate_full_project_plan(user_goal, project_context)

        # Assertions
        self.assertEqual(len(full_plan), 3)
        mock_gen_outline.assert_called_once_with(user_goal, project_context)

        self.assertEqual(mock_gen_detailed_tasks.call_count, 2)
        mock_gen_detailed_tasks.assert_any_call("Feature A", user_goal, project_context)
        mock_gen_detailed_tasks.assert_any_call("Feature B", user_goal, project_context)

        self.assertEqual(mock_gen_step_elaboration.call_count, 3)
        mock_gen_step_elaboration.assert_any_call("Task A.1", user_goal, project_context)
        mock_gen_step_elaboration.assert_any_call("Task A.2", user_goal, project_context)
        mock_gen_step_elaboration.assert_any_call("Task B.1", user_goal, project_context)

        # Check structure of the first step
        self.assertEqual(full_plan[0]["step_id"], "1.1")
        self.assertEqual(full_plan[0]["description"], "Task A.1")
        self.assertEqual(full_plan[0]["type"], "python_script")
        self.assertEqual(full_plan[0]["details"]["script_content_prompt"], "Prompt for A.1")
        self.assertEqual(full_plan[0]["outline_group"], "Feature A")

        self.assertEqual(full_plan[1]["step_id"], "1.2")
        self.assertEqual(full_plan[1]["outline_group"], "Feature A")

        self.assertEqual(full_plan[2]["step_id"], "2.1") # New outline group, counter resets for sub-step
        self.assertEqual(full_plan[2]["description"], "Task B.1")
        self.assertEqual(full_plan[2]["type"], "informational")
        self.assertEqual(full_plan[2]["outline_group"], "Feature B")


    @patch.object(HierarchicalPlanner, 'generate_project_plan_step_for_task', new_callable=AsyncMock)
    @patch.object(HierarchicalPlanner, 'generate_detailed_tasks_for_outline_item', new_callable=AsyncMock)
    @patch.object(HierarchicalPlanner, 'generate_high_level_outline', new_callable=AsyncMock)
    async def test_empty_outline_returns_empty_plan(
        self, mock_gen_outline, mock_gen_detailed_tasks, mock_gen_step_elaboration
    ):
        mock_gen_outline.return_value = [] # Empty outline

        full_plan = await self.planner.generate_full_project_plan("Goal", "Context")

        self.assertEqual(full_plan, [])
        mock_gen_outline.assert_called_once_with("Goal", "Context")
        mock_gen_detailed_tasks.assert_not_called()
        mock_gen_step_elaboration.assert_not_called()

    @patch.object(HierarchicalPlanner, 'generate_project_plan_step_for_task', new_callable=AsyncMock)
    @patch.object(HierarchicalPlanner, 'generate_detailed_tasks_for_outline_item', new_callable=AsyncMock)
    @patch.object(HierarchicalPlanner, 'generate_high_level_outline', new_callable=AsyncMock)
    async def test_outline_item_with_no_detailed_tasks(
        self, mock_gen_outline, mock_gen_detailed_tasks, mock_gen_step_elaboration
    ):
        user_goal = "Test goal"
        mock_gen_outline.return_value = ["Outline 1", "Outline 2"]

        def detailed_tasks_side_effect(outline_item, ug, pc):
            if outline_item == "Outline 1":
                return [] # No detailed tasks for Outline 1
            elif outline_item == "Outline 2":
                return ["Task 2.1"]
            return []
        mock_gen_detailed_tasks.side_effect = detailed_tasks_side_effect
        mock_gen_step_elaboration.return_value = {"type": "informational", "details": {"message": "Msg 2.1"}}

        full_plan = await self.planner.generate_full_project_plan(user_goal)

        self.assertEqual(len(full_plan), 1)
        self.assertEqual(full_plan[0]["step_id"], "2.1") # Belongs to the second outline item
        self.assertEqual(full_plan[0]["description"], "Task 2.1")
        self.assertEqual(full_plan[0]["outline_group"], "Outline 2")

        self.assertEqual(mock_gen_detailed_tasks.call_count, 2)
        mock_gen_step_elaboration.assert_called_once_with("Task 2.1", user_goal, None)


    @patch('builtins.print') # To check warning logs
    @patch.object(HierarchicalPlanner, 'generate_project_plan_step_for_task', new_callable=AsyncMock)
    @patch.object(HierarchicalPlanner, 'generate_detailed_tasks_for_outline_item', new_callable=AsyncMock)
    @patch.object(HierarchicalPlanner, 'generate_high_level_outline', new_callable=AsyncMock)
    async def test_detailed_task_fails_elaboration(
        self, mock_gen_outline, mock_gen_detailed_tasks, mock_gen_step_elaboration, mock_print
    ):
        user_goal = "Test fail elaboration"
        mock_gen_outline.return_value = ["Feature X"]
        mock_gen_detailed_tasks.return_value = ["Task X.1 (good)", "Task X.2 (fails)", "Task X.3 (good)"]

        def step_elaboration_side_effect(detailed_task, ug, pc):
            if detailed_task == "Task X.1 (good)":
                return {"type": "informational", "details": {"message": "Msg X.1"}}
            elif detailed_task == "Task X.2 (fails)":
                return None # Elaboration fails
            elif detailed_task == "Task X.3 (good)":
                return {"type": "python_script", "details": {"script_content_prompt": "Prompt X.3"}}
            return None
        mock_gen_step_elaboration.side_effect = step_elaboration_side_effect

        full_plan = await self.planner.generate_full_project_plan(user_goal)

        self.assertEqual(len(full_plan), 2)
        self.assertEqual(full_plan[0]["description"], "Task X.1 (good)")
        self.assertEqual(full_plan[0]["step_id"], "1.1")
        self.assertEqual(full_plan[1]["description"], "Task X.3 (good)")
        self.assertEqual(full_plan[1]["step_id"], "1.3") # Note: step_id for X.3 is 1.3 due to X.2 failing

        self.assertEqual(mock_gen_step_elaboration.call_count, 3)

        # Check if a warning was printed for the failed elaboration
        printed_warnings = [str(call_args) for call_args, _ in mock_print.call_args_list]
        self.assertTrue(any("Failed to elaborate step for detailed task: 'Task X.2 (fails)'" in s for s in printed_warnings))

    @patch.object(HierarchicalPlanner, 'generate_project_plan_step_for_task', new_callable=AsyncMock)
    @patch.object(HierarchicalPlanner, 'generate_detailed_tasks_for_outline_item', new_callable=AsyncMock)
    @patch.object(HierarchicalPlanner, 'generate_high_level_outline', new_callable=AsyncMock)
    async def test_all_detailed_tasks_fail_elaboration_for_an_outline_item(
        self, mock_gen_outline, mock_gen_detailed_tasks, mock_gen_step_elaboration
    ):
        user_goal = "Test all fail elaboration"
        mock_gen_outline.return_value = ["Feature Y"]
        mock_gen_detailed_tasks.return_value = ["Task Y.1", "Task Y.2"]
        mock_gen_step_elaboration.return_value = None # All elaborations fail

        full_plan = await self.planner.generate_full_project_plan(user_goal)
        self.assertEqual(len(full_plan), 0) # No steps should be added
        self.assertEqual(mock_gen_step_elaboration.call_count, 2) # Attempted for both


class TestHierarchicalPlannerStepElaboration(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_llm_provider = AsyncMock(spec=OllamaProvider)
        self.mock_llm_provider.invoke_ollama_model_async = AsyncMock()
        self.planner = HierarchicalPlanner(llm_provider=self.mock_llm_provider)

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_successful_elaboration_python_script(self, mock_get_model):
        mock_get_model.return_value = "mock_step_elab_model"
        detailed_task = "Implement user login functionality."
        user_goal = "Create a web app with user accounts."

        expected_details = {
            "script_content_prompt": "Write Python code for user login using Flask.",
            "input_files": ["models/user.py"],
            "output_files_to_capture": ["routes/auth.py"],
            "timeout_seconds": 60
        }
        llm_response_json = json.dumps({
            "type": "python_script",
            "details": expected_details
        })
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response_json

        result = await self.planner.generate_project_plan_step_for_task(detailed_task, user_goal)

        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "python_script")
        self.assertEqual(result["details"], expected_details)

        from ai_assistant.planning.hierarchical_planner import LLM_HP_STEP_ELABORATION_PROMPT_TEMPLATE
        expected_prompt = LLM_HP_STEP_ELABORATION_PROMPT_TEMPLATE.format(
            user_goal=user_goal,
            detailed_task=detailed_task,
            project_context_section=""
        )
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once_with(
            expected_prompt,
            model_name="mock_step_elab_model",
            temperature=0.3,
            max_tokens=1000
        )
        mock_get_model.assert_called_once_with("hierarchical_planning_step_elaboration")

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_successful_elaboration_human_review(self, mock_get_model):
        mock_get_model.return_value = "mock_step_elab_model"
        detailed_task = "Verify database schema."
        user_goal = "Setup database."
        expected_details = {"prompt_to_user": "Is the database schema correct?"}
        llm_response_json = json.dumps({"type": "human_review_gate", "details": expected_details})
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response_json

        result = await self.planner.generate_project_plan_step_for_task(detailed_task, user_goal)
        self.assertEqual(result["type"], "human_review_gate")
        self.assertEqual(result["details"], expected_details)

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_successful_elaboration_informational(self, mock_get_model):
        mock_get_model.return_value = "mock_step_elab_model"
        detailed_task = "Note down API version."
        user_goal = "Document API."
        expected_details = {"message": "API version is v1.2"}
        llm_response_json = json.dumps({"type": "informational", "details": expected_details})
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response_json

        result = await self.planner.generate_project_plan_step_for_task(detailed_task, user_goal)
        self.assertEqual(result["type"], "informational")
        self.assertEqual(result["details"], expected_details)

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_step_elaboration_with_project_context(self, mock_get_model):
        mock_get_model.return_value = "mock_step_elab_model_ctx"
        detailed_task = "Define API routes for products."
        user_goal = "Build e-commerce API."
        project_context = "Using FastAPI framework. Product model already defined."
        llm_response_json = json.dumps({"type": "python_script", "details": {"script_content_prompt": "Define FastAPI routes..."}})
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response_json

        await self.planner.generate_project_plan_step_for_task(detailed_task, user_goal, project_context)

        from ai_assistant.planning.hierarchical_planner import LLM_HP_STEP_ELABORATION_PROMPT_TEMPLATE
        project_context_section = f"\nExisting project context to consider:\n{project_context}"
        expected_prompt = LLM_HP_STEP_ELABORATION_PROMPT_TEMPLATE.format(
            user_goal=user_goal,
            detailed_task=detailed_task,
            project_context_section=project_context_section
        )
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once_with(
            expected_prompt,
            model_name="mock_step_elab_model_ctx",
            temperature=0.3,
            max_tokens=1000
        )

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_llm_returns_json_with_markdown_fences(self, mock_get_model):
        mock_get_model.return_value = "mock_step_elab_model_fences"
        llm_response_fenced = "```json\n{\"type\": \"informational\", \"details\": {\"message\": \"Fenced content\"}}\n```"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response_fenced

        result = await self.planner.generate_project_plan_step_for_task("Task", "Goal")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "informational")
        self.assertEqual(result["details"]["message"], "Fenced content")

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_llm_returns_invalid_json(self, mock_get_model):
        mock_get_model.return_value = "mock_step_elab_model_invalid"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "This is not JSON"
        with patch('builtins.print') as mock_print:
            result = await self.planner.generate_project_plan_step_for_task("Task", "Goal")
        self.assertIsNone(result)
        self.assertTrue(any("Failed to parse LLM JSON response" in str(c) for c in mock_print.call_args_list))


    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_llm_returns_json_missing_keys(self, mock_get_model):
        mock_get_model.return_value = "mock_step_elab_model_missing_keys"
        test_cases = [
            json.dumps({"details": {"message": "..."}}),  # Missing "type"
            json.dumps({"type": "informational"}),  # Missing "details"
            json.dumps({"type": "informational", "details": "not a dict"}), # "details" not a dict
        ]
        for i, llm_response_json in enumerate(test_cases):
            with self.subTest(case_index=i):
                self.mock_llm_provider.invoke_ollama_model_async.reset_mock()
                self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_response_json
                with patch('builtins.print') as mock_print:
                    result = await self.planner.generate_project_plan_step_for_task("Task", "Goal")
                self.assertIsNone(result)
                self.assertTrue(any("Parsed JSON for task" in str(c) and "incorrect structure" in str(c) for c in mock_print.call_args_list))


    async def test_empty_detailed_task_returns_none(self):
        result = await self.planner.generate_project_plan_step_for_task("", "User Goal")
        self.assertIsNone(result)
        self.mock_llm_provider.invoke_ollama_model_async.assert_not_called()

    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_llm_empty_response_for_step_elaboration(self, mock_get_model):
        mock_get_model.return_value = "mock_empty_elab_model"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = ""
        with patch('builtins.print') as mock_print:
            result = await self.planner.generate_project_plan_step_for_task("Task", "Goal")
        self.assertIsNone(result)
        self.assertTrue(any("LLM returned empty response" in str(c) for c in mock_print.call_args_list))


    @patch('ai_assistant.planning.hierarchical_planner.get_model_for_task')
    async def test_llm_error_for_step_elaboration(self, mock_get_model):
        mock_get_model.return_value = "mock_error_elab_model"
        self.mock_llm_provider.invoke_ollama_model_async.side_effect = Exception("LLM API Error")
        with patch('builtins.print') as mock_print:
            result = await self.planner.generate_project_plan_step_for_task("Task", "Goal")
        self.assertIsNone(result)
        self.assertTrue(any("Error during LLM call" in str(c) for c in mock_print.call_args_list))
