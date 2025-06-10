import unittest
from unittest import mock
from unittest.mock import patch, AsyncMock
import asyncio
import os
import sys
import uuid
import datetime
from dataclasses import dataclass, field

try:
    from ai_assistant.execution.action_executor import ActionExecutor
    from ai_assistant.core.reflection import ReflectionLogEntry, global_reflection_log as core_global_reflection_log
    from ai_assistant.planning.execution import ExecutionAgent
    from ai_assistant.code_services.service import CodeService # Added for mocking
except ImportError: # pragma: no cover
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.execution.action_executor import ActionExecutor
    from ai_assistant.core.reflection import ReflectionLogEntry, global_reflection_log as core_global_reflection_log
    from ai_assistant.planning.execution import ExecutionAgent
    from ai_assistant.code_services.service import CodeService


@dataclass
class MockReflectionLogEntryForTest:
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal_description: str = "Mock Goal"
    plan: Optional[List[Dict[str, Any]]] = None
    execution_results: Optional[List[Any]] = None
    status: str = "UNKNOWN"
    notes: Optional[str] = ""
    timestamp: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    traceback_snippet: Optional[str] = None
    is_self_modification_attempt: bool = False
    source_suggestion_id: Optional[str] = None
    modification_type: Optional[str] = None
    modification_details: Optional[Dict[str, Any]] = None
    post_modification_test_passed: Optional[bool] = None
    post_modification_test_details: Optional[Dict[str, Any]] = None
    commit_info: Optional[Dict[str, Any]] = None

class TestActionExecutor(unittest.TestCase):

    def setUp(self):
        self.executor = ActionExecutor()
        self.original_log_entries = list(core_global_reflection_log.log_entries)
        core_global_reflection_log.log_entries = []

    def tearDown(self):
        core_global_reflection_log.log_entries = self.original_log_entries

# Mock classes for ExecutionAgent tests
class MockToolSystemForExecutionAgent:
    def __init__(self):
        self.tools_called_with_args = []
        self.tool_outputs = {} # Store predefined outputs for tools

    async def execute_tool(self, name, args=(), kwargs=None, task_manager=None, notification_manager=None): # Match signature
        self.tools_called_with_args.append({"name": name, "args": args, "kwargs": kwargs or {}})
        print(f"[MockToolSystemForExecutionAgent] Called: {name} with args: {args}, kwargs: {kwargs}")
        if name in self.tool_outputs:
            output = self.tool_outputs[name]
            if callable(output): # Allow dynamic output based on args
                return output(*args, **(kwargs or {}))
            return output
        return f"Mock success for {name}"

    def list_tools_with_sources(self): # Needed by PlannerAgent
        # Provide a basic structure; adapt if PlannerAgent needs more details
        return {
            "request_user_clarification": {"description": "Asks for clarification.", "schema_details": {}},
            "some_other_tool": {"description": "Another tool.", "schema_details": {}},
        }

    def list_tools(self): # Needed by PlannerAgent if it uses this
        return {name: data["description"] for name, data in self.list_tools_with_sources().items()}


class MockPlannerAgentForExecutionAgent(PlannerAgent): # Inherit from real PlannerAgent
    async def create_plan_with_llm(self, goal_description, available_tools, project_context_summary=None, project_name_for_context=None):
        # Not actively used if ExecutionAgent doesn't re-plan in this specific test
        return []
    async def replan_after_failure(self, original_goal, failure_analysis, available_tools, ollama_model_name=None):
        # Not actively used if ExecutionAgent doesn't re-plan in this specific test
        return []

class MockLearningAgentForExecutionAgent: # Simple mock, doesn't need to inherit
    def process_reflection_entry(self, entry):
        print(f"[MockLearningAgentForExecutionAgent] Processing reflection for goal: {entry.goal_description}")


class TestExecutionAgentFlows(unittest.IsolatedAsyncioTestCase): # Use IsolatedAsyncioTestCase for async tests

    async def test_execute_plan_with_clarification_tool(self):
        """
        Tests ExecutionAgent's ability to execute a plan involving the
        request_user_clarification tool and use its output in a subsequent step.
        """
        execution_agent = ExecutionAgent()
        mock_tool_system = MockToolSystemForExecutionAgent()
        mock_planner_agent = MockPlannerAgentForExecutionAgent()
        mock_learning_agent = MockLearningAgentForExecutionAgent()

        # Define the output for the clarification tool
        clarification_response = "my_preferred_file.txt"
        mock_tool_system.tool_outputs["request_user_clarification"] = clarification_response

        test_plan = [
            {"tool_name": "request_user_clarification", "args": ("What is your preferred filename?",), "kwargs": {}},
            {"tool_name": "some_other_tool", "args": ("[[step_1_output]]", "static_value"), "kwargs": {}}
        ]

        goal_desc = "Test goal using clarification"

        # Execute the plan
        # ExecutionAgent.execute_plan returns -> Tuple[List[Dict[str, Any]], List[Any]] (final_plan, results)
        final_plan, results = await execution_agent.execute_plan(
            goal_description=goal_desc,
            initial_plan=test_plan,
            tool_system=mock_tool_system,
            planner_agent=mock_planner_agent,
            learning_agent=mock_learning_agent
            # task_manager and notification_manager can be None for this test if not essential to the flow
        )

        self.assertEqual(len(mock_tool_system.tools_called_with_args), 2, "Expected two tools to be called")

        # Check call to request_user_clarification
        clarification_call = mock_tool_system.tools_called_with_args[0]
        self.assertEqual(clarification_call["name"], "request_user_clarification")
        self.assertEqual(clarification_call["args"], ("What is your preferred filename?",))

        # Check call to some_other_tool
        other_tool_call = mock_tool_system.tools_called_with_args[1]
        self.assertEqual(other_tool_call["name"], "some_other_tool")
        # Verify that the output of the first step was correctly substituted
        self.assertEqual(other_tool_call["args"], (clarification_response, "static_value"))

        # Check overall results
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], clarification_response) # Result of first step
        # The result of the second step would be the mock output for "some_other_tool"
        self.assertEqual(results[1], f"Mock success for some_other_tool")

        # Ensure the final plan matches the initial plan if no re-planning occurred
        self.assertEqual(final_plan, test_plan)


class TestActionExecutor(unittest.TestCase):

    def setUp(self):
        self.executor = ActionExecutor(learning_agent=mock.MagicMock()) # Pass a mock LearningAgent
        self.original_log_entries = list(core_global_reflection_log.log_entries)
        core_global_reflection_log.log_entries = []

    def tearDown(self):
        core_global_reflection_log.log_entries = self.original_log_entries

    @patch('ai_assistant.execution.action_executor.self_modification.edit_function_source_code')
    @patch('ai_assistant.execution.action_executor.global_reflection_log.log_execution')
    @patch.object(ActionExecutor, '_run_post_modification_test', new_callable=AsyncMock)
    def test_execute_action_propose_tool_modification_success_with_test_pass(self, mock_run_post_mod_test, mock_log_execution, mock_edit_code):
        mock_edit_code.return_value = "Successfully modified function 'test_func' in module 'test_module'."
        mock_run_post_mod_test.return_value = (True, "Post-modification test passed successfully.")

        original_desc = "This is the original description for the change."
        action_details = {
            "module_path": "test_module.py", "function_name": "test_func", "tool_name": "test_tool",
            "suggested_code_change": "def test_func(): pass # new code",
            "original_reflection_entry_id": "entry_id_for_test_pass",
            "suggested_change_description": original_desc # This should be passed as change_description
        }
        proposed_action = {"source_insight_id": "insight1", "action_type": "PROPOSE_TOOL_MODIFICATION", "details": action_details}

        result = asyncio.run(self.executor.execute_action(proposed_action))

        self.assertTrue(result)
        mock_edit_code.assert_called_once()
        args, kwargs = mock_edit_code.call_args
        self.assertEqual(kwargs.get('change_description'), original_desc)

        mock_run_post_mod_test.assert_called_once()
        final_log_call_args = mock_log_execution.call_args_list[-1].kwargs
        self.assertTrue(final_log_call_args.get('overall_success'))
        self.assertTrue(final_log_call_args.get('post_modification_test_passed'))
        self.assertEqual(final_log_call_args.get('modification_details', {}).get('source_of_code'), "Insight")


    @patch('ai_assistant.execution.action_executor.global_reflection_log.log_execution')
    @patch('ai_assistant.execution.action_executor.self_modification.edit_function_source_code')
    @patch.object(ActionExecutor, '_run_post_modification_test', new_callable=AsyncMock)
    def test_tool_mod_via_codeservice_and_test_pass(self, mock_run_post_mod_test, mock_edit_code, mock_log_execution):
        # Mock the CodeService's modify_code method on the executor's instance
        self.executor.code_service = mock.AsyncMock(spec=CodeService)
        self.executor.code_service.modify_code.return_value = {
            "status": "SUCCESS_CODE_GENERATED",
            "modified_code_string": "def new_llm_func(): return 'fixed_by_codeservice'"
        }
        mock_edit_code.return_value = "Successfully modified function 'old_func'."
        mock_run_post_mod_test.return_value = (True, "Post-CodeService-mod test passed.")

        action_details = {
            "module_path": "test_module.py", "function_name": "old_func", "tool_name": "test_tool",
            "suggested_change_description": "Needs a fix via CodeService.",
            # NO suggested_code_change, to trigger the CodeService path
            "original_reflection_entry_id": "dummy_ref_id_cs"
        }
        proposed_action = {"source_insight_id": "insight_cs_llm", "action_type": "PROPOSE_TOOL_MODIFICATION", "details": action_details}

        result = asyncio.run(self.executor.execute_action(proposed_action))

        self.assertTrue(result)
        self.executor.code_service.modify_code.assert_called_once_with(
            context="SELF_FIX_TOOL",
            modification_instruction="Needs a fix via CodeService.",
            module_path="test_module.py",
            function_name="old_func",
            existing_code=None
        )
        mock_edit_code.assert_called_once()
        args, kwargs = mock_edit_code.call_args
        self.assertEqual(kwargs.get('module_path'), "test_module.py")
        self.assertEqual(kwargs.get('function_name'), "old_func")
        self.assertEqual(kwargs.get('new_code_string'), "def new_llm_func(): return 'fixed_by_codeservice'")
        self.assertEqual(kwargs.get('change_description'), "Needs a fix via CodeService.") # Verify here

        mock_run_post_mod_test.assert_called_once()

        code_service_log_call = next(call for call in mock_log_execution.call_args_list if call.kwargs.get('status_override') == "CODE_SERVICE_GEN_SUCCESS")
        self.assertIsNotNone(code_service_log_call)
        self.assertTrue(code_service_log_call.kwargs.get('overall_success'))

        final_log_call_args = mock_log_execution.call_args_list[-1].kwargs
        self.assertTrue(final_log_call_args.get('overall_success'))
        self.assertEqual(final_log_call_args.get('modification_details', {}).get('source_of_code'), "CodeService_LLM")
        self.assertTrue(final_log_call_args.get('post_modification_test_passed'))

    @patch('ai_assistant.execution.action_executor.global_reflection_log.log_execution')
    def test_tool_mod_via_codeservice_fails_to_generate(self, mock_log_execution):
        self.executor.code_service = mock.AsyncMock(spec=CodeService)
        self.executor.code_service.modify_code.return_value = {
            "status": "ERROR_LLM_NO_SUGGESTION",
            "modified_code_string": None,
            "error": "LLM said no."
        }
        action_details = {
            "module_path": "test_module.py", "function_name": "old_func", "tool_name": "test_tool",
            "suggested_change_description": "Needs a fix via CodeService, but CS will fail.",
            "original_reflection_entry_id": "dummy_ref_id_cs_fail"
        }
        proposed_action = {"source_insight_id": "insight_cs_llm_fail", "action_type": "PROPOSE_TOOL_MODIFICATION", "details": action_details}

        result = asyncio.run(self.executor.execute_action(proposed_action))

        self.assertFalse(result)
        self.executor.code_service.modify_code.assert_called_once()

        code_service_fail_log_call = next(call for call in mock_log_execution.call_args_list if call.kwargs.get('status_override') == "CODE_SERVICE_GEN_FAILED")
        self.assertIsNotNone(code_service_fail_log_call)
        self.assertFalse(code_service_fail_log_call.kwargs.get('overall_success'))


    @patch('ai_assistant.execution.action_executor.global_reflection_log.log_execution')
    @patch('ai_assistant.execution.action_executor.self_modification.get_backup_function_source_code')
    @patch('ai_assistant.execution.action_executor.self_modification.edit_function_source_code')
    @patch.object(ActionExecutor, '_run_post_modification_test', new_callable=AsyncMock)
    def test_tool_mod_test_fails_and_reversion_succeeds(self, mock_run_post_mod_test, mock_edit_code,
                                                       mock_get_backup, mock_log_execution):
        mock_edit_code.side_effect = [
            "Successfully modified function 'test_func'.",
            "Successfully reverted function 'test_func'."
        ]
        mock_run_post_mod_test.return_value = (False, "Post-mod test failed critically.")
        mock_get_backup.return_value = "def test_func(): pass # Original backup code"
        original_desc_for_revert_test = "Buggy change attempt"
        action_details = {
            "module_path": "test_module.py", "function_name": "test_func", "tool_name": "test_tool",
            "suggested_code_change": "def test_func(): pass # new potentially buggy code",
            "original_reflection_entry_id": "dummy_orig_ref_id_for_revert_test",
            "suggested_change_description": original_desc_for_revert_test
        }
        proposed_action = {"source_insight_id": "insight_revert", "action_type": "PROPOSE_TOOL_MODIFICATION", "details": action_details}
        result = asyncio.run(self.executor.execute_action(proposed_action))
        self.assertFalse(result)
        self.assertEqual(mock_edit_code.call_count, 2)

        # Check first call (attempt)
        args_attempt, kwargs_attempt = mock_edit_code.call_args_list[0]
        self.assertEqual(kwargs_attempt.get('change_description'), original_desc_for_revert_test)

        # Check second call (revert)
        args_revert, kwargs_revert = mock_edit_code.call_args_list[1]
        self.assertIn("Reverting function 'test_func' to backup", kwargs_revert.get('change_description'))
        self.assertEqual(kwargs_revert.get('new_code_string'), "def test_func(): pass # Original backup code")

        final_log_call_args = mock_log_execution.call_args_list[-1].kwargs
        self.assertTrue(final_log_call_args.get('modification_details', {}).get('reversion_attempted'))
        self.assertTrue(final_log_call_args.get('modification_details', {}).get('reversion_successful'))

    def test_find_original_reflection_entry(self):
        mock_entry_id_to_find = str(uuid.uuid4())
        mock_original_entry = MockReflectionLogEntryForTest(entry_id=mock_entry_id_to_find)
        core_global_reflection_log.log_entries = [mock_original_entry]
        found_entry = self.executor._find_original_reflection_entry(mock_entry_id_to_find)
        self.assertIsNotNone(found_entry)
        if found_entry: self.assertEqual(found_entry.entry_id, mock_entry_id_to_find)
        self.assertIsNone(self.executor._find_original_reflection_entry("non_existent_id"))

    @patch('ai_assistant.execution.action_executor.self_modification.edit_function_source_code')
    @patch('ai_assistant.execution.action_executor.global_reflection_log.log_execution')
    @patch.object(ActionExecutor, '_run_post_modification_test', new_callable=AsyncMock)
    def test_execute_action_propose_tool_modification_edit_fails(self, mock_run_post_mod_test, mock_log_execution, mock_edit_code):
        mock_edit_code.return_value = "Error: Failed to modify function."
        action_details = {
            "module_path": "test_module.py", "function_name": "test_func", "tool_name": "test_tool",
            "suggested_code_change": "def test_func(): pass # new code",
            "original_reflection_entry_id": "entry_id_for_edit_fail"
        }
        proposed_action = {"source_insight_id": "insight_edit_fail", "action_type": "PROPOSE_TOOL_MODIFICATION", "details": action_details}
        result = asyncio.run(self.executor.execute_action(proposed_action))
        self.assertFalse(result)
        mock_run_post_mod_test.assert_not_called()
        final_log_call_args = mock_log_execution.call_args_list[-1].kwargs
        self.assertIsNone(final_log_call_args.get('modification_details', {}).get('reversion_attempted'))

if __name__ == '__main__': # pragma: no cover
    unittest.main()
