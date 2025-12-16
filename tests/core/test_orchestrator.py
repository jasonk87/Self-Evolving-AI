import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import mock
from ai_assistant.core.orchestrator import DynamicOrchestrator
from ai_assistant.planning.planning import PlannerAgent
from ai_assistant.planning.execution import ExecutionAgent
from ai_assistant.learning.learning import LearningAgent
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.core.task_manager import TaskManager, ActiveTask, ActiveTaskType
from ai_assistant.core.notification_manager import NotificationManager
from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner
from ai_assistant.llm_interface.ollama_client import OllamaProvider
from ai_assistant.code_synthesis.code_service import CodeService
from ai_assistant.tools.tool_system import ToolSystem
from ai_assistant.core.enums import ExecutionMode

class TestDynamicOrchestrator(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_planner_agent = AsyncMock(spec=PlannerAgent)
        self.mock_execution_agent = AsyncMock(spec=ExecutionAgent)
        self.mock_learning_agent = MagicMock(spec=LearningAgent)

        # Mock the ActionExecutor and its nested CodeService and llm_provider
        self.mock_llm_provider = AsyncMock(spec=OllamaProvider)
        self.mock_code_service = MagicMock(spec=CodeService)
        self.mock_code_service.llm_provider = self.mock_llm_provider
        # Set a default string return value for the mock LLM provider's async method
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "Default mock LLM response"
        self.mock_action_executor = MagicMock(spec=ActionExecutor)
        self.mock_action_executor.code_service = self.mock_code_service

        self.mock_task_manager = MagicMock(spec=TaskManager)
        self.mock_notification_manager = MagicMock()
        self.mock_hierarchical_planner = AsyncMock(spec=HierarchicalPlanner) # Added

        self.orchestrator = DynamicOrchestrator(
            planner=self.mock_planner_agent,
            executor=self.mock_execution_agent,
            learning_agent=self.mock_learning_agent,
            action_executor=self.mock_action_executor,
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager,
            hierarchical_planner=self.mock_hierarchical_planner # Added
        )
        # Mock tool_system_instance used internally by orchestrator if it's not passed in
        self.tool_system_patcher = patch('ai_assistant.core.orchestrator.tool_system_instance', MagicMock(spec=ToolSystem))
        self.mock_tool_system = self.tool_system_patcher.start()
        self.mock_tool_system.list_tools_with_sources.return_value = {"mock_tool": {"description": "A mock tool"}}

    def tearDown(self):
        self.tool_system_patcher.stop()

    async def test_process_prompt_summarizer_succeeds(self, mock_summarizer):
        user_prompt = "create a plan"
        self.mock_planner_agent.create_plan_with_llm.return_value = [{"tool_name": "mock_tool", "args": {}}]
        self.mock_execution_agent.execute_plan.return_value = ([{"tool_name": "mock_tool", "args": {}}], ["tool_result"])

        mock_summarizer.return_value = "Summary of success"

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.assertEqual(response, "Summary of success")
        self.mock_planner_agent.create_plan_with_llm.assert_called_once()
        self.mock_execution_agent.execute_plan.assert_called_once()
        mock_summarizer.assert_called_once()

    async def test_process_prompt_summarizer_fails_falls_back_to_technical_summary(self, mock_generate_exec_summary, mock_summarizer):
        user_prompt = "do something"
        self.mock_planner_agent.create_plan_with_llm.return_value = [{"tool_name": "mock_tool", "args": {}}]
        self.mock_execution_agent.execute_plan.return_value = ([{"tool_name": "mock_tool", "args": {}}], ["tool_result"])

        mock_summarizer.return_value = None # Summarizer fails
        mock_generate_exec_summary.return_value = "::Technical Summary::"

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success) # Still success because execution succeeded
        self.assertEqual(response, "::Technical Summary::")
        mock_summarizer.assert_called_once()
        mock_generate_exec_summary.assert_called_once()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    async def test_process_prompt_no_plan_created(self, mock_summarizer):
        user_prompt = "impossible task"
        self.mock_planner_agent.create_plan_with_llm.return_value = [] # No plan

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        # Should return a default failure message if no rephrasing happens here (assuming no rephraser mock for this test specific scope)
        self.assertIn("could not generate a plan", response.lower())
        self.mock_execution_agent.execute_plan.assert_not_called()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_no_plan_created_error_rephrased_succeeds(
        self, mock_rephraser, mock_log_event, mock_debug_mode
    ):
        user_prompt = "impossible task"
        self.mock_planner_agent.create_plan_with_llm.return_value = []
        mock_rephraser.return_value = "Rephrased: No plan possible."

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, "Rephrased: No plan possible.")
        mock_rephraser.assert_called_once()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_no_plan_created_error_rephraser_fails(
        self, mock_rephraser, mock_log_event, mock_debug_mode
    ):
        user_prompt = "impossible task"
        self.mock_planner_agent.create_plan_with_llm.return_value = []
        mock_rephraser.return_value = None # Rephraser fails

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertIn("could not generate a plan", response.lower()) # Fallback
        mock_rephraser.assert_called_once()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_plan_execution_fails_error_rephrased_succeeds(
        self, mock_gen_exec_summary, mock_rephraser, mock_log_event, mock_debug_mode
    ):
        user_prompt = "do something risky"
        self.mock_planner_agent.create_plan_with_llm.return_value = [{"tool_name": "risky_tool", "args": {}}]

        # execution fails
        self.mock_execution_agent.execute_plan.side_effect = Exception("Boom!")

        mock_rephraser.return_value = "Rephrased: Execution blew up."
        mock_gen_exec_summary.return_value = "::Technical Summary Fail::"

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, "Rephrased: Execution blew up.::Technical Summary Fail::")

        self.mock_execution_agent.execute_plan.assert_called_once()
        mock_rephraser.assert_called_once()
        mock_gen_exec_summary.assert_called_once()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_plan_execution_fails_rephraser_fails(
        self, mock_gen_exec_summary, mock_rephraser, mock_log_event, mock_debug_mode
    ):
        user_prompt = "do something risky"
        self.mock_planner_agent.create_plan_with_llm.return_value = [{"tool_name": "risky_tool", "args": {}}]
        self.mock_execution_agent.execute_plan.side_effect = Exception("Boom!")

        mock_rephraser.return_value = None # Rephraser fails
        mock_gen_exec_summary.return_value = "::Technical Summary Fail::"

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        # Should contain original error and tech summary
        self.assertIn("Boom!", response)
        self.assertIn("::Technical Summary Fail::", response)

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_orchestrator_exception_rephrased_succeeds(self, mock_gen_exec_summary, mock_rephraser, mock_log_event, mock_debug_mode):
        user_prompt = "break orchestrator"
        # Planner raises exception directly
        self.mock_planner_agent.create_plan_with_llm.side_effect = Exception("Core meltdown")

        mock_rephraser.return_value = "Rephrased: Core meltdown happened."

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, "Rephrased: Core meltdown happened.")

        mock_rephraser.assert_called_once()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_orchestrator_exception_rephraser_fails(self, mock_gen_exec_summary, mock_rephraser, mock_log_event, mock_debug_mode):
        user_prompt = "break orchestrator"
        self.mock_planner_agent.create_plan_with_llm.side_effect = Exception("Core meltdown")
        mock_rephraser.return_value = None

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertIn("Core meltdown", response)

    async def test_process_prompt_plan_fails_summarizer_succeeds(self, mock_generate_exec_summary, mock_summarizer):
        # Scenario: Plan executes, but returns failures in result (not exception)
        user_prompt = "do partially failing task"
        self.mock_planner_agent.create_plan_with_llm.return_value = [{"tool_name": "bad_tool", "args": {}}]

        # Tool execution result indicates failure
        tool_results = [{"status": "failed", "error": "it broke"}]
        self.mock_execution_agent.execute_plan.return_value = (
             [{"tool_name": "bad_tool", "args": {}}],
             tool_results
        )

        mock_summarizer.return_value = "Summary of failure"

        # Orchestrator considers success=True if execute_plan returns without exception,
        # but the content of response is determined by summarizer
        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.assertEqual(response, "Summary of failure")

    async def test_process_prompt_plan_fails_summarizer_fails_too(self, mock_generate_exec_summary, mock_summarizer):
        # Scenario: Plan executed (with failures in content), summarizer also fails
        user_prompt = "do partially failing task"
        self.mock_planner_agent.create_plan_with_llm.return_value = [{"tool_name": "bad_tool", "args": {}}]

        self.mock_execution_agent.execute_plan.return_value = (
             [{"tool_name": "bad_tool", "args": {}}],
             [{"status": "failed"}]
        )

        mock_summarizer.return_value = None # Summarizer gives up
        mock_generate_exec_summary.return_value = "::Tech Summary::"

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.assertEqual(response, "::Tech Summary::")

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    async def test_process_prompt_simple_plan_success(self, mock_summarizer, mock_log_event, mock_debug_mode):
        user_prompt = "simple task"
        self.mock_planner_agent.create_plan_with_llm.return_value = [{"tool_name": "simple_tool", "args": {}}]
        self.mock_execution_agent.execute_plan.return_value = ([{"tool_name": "simple_tool", "args": {}}], ["result"])

        # Reset mocks to ensure clean state
        self.mock_hierarchical_planner.generate_full_project_plan.reset_mock()
        self.mock_task_manager.add_task.reset_mock()

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.mock_hierarchical_planner.generate_full_project_plan.assert_not_called()
        self.mock_task_manager.add_task.assert_not_called()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    async def test_process_prompt_triggers_hierarchical_on_empty_simple_plan_and_keywords(
        self, mock_summarizer, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a new app"
        self.mock_planner_agent.create_plan_with_llm.return_value = [] # Simple planner fails

        # Mock hierarchical planner success
        mock_project_plan = [{"step_id": "1", "type": "code_generation", "details": {}}]
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = mock_project_plan

        mock_active_task = MagicMock(spec=ActiveTask)
        mock_active_task.task_id = "hp_task_123"
        self.mock_task_manager.add_task.return_value = mock_active_task

        self.mock_execution_agent.execute_plan.return_value = ([], ["success"])
        mock_summarizer.return_value = "Project setup complete."

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        # Check that we fell into the hierarchical branch
        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once_with(user_prompt)
        # It should also execute the plan
        self.mock_execution_agent.execute_plan.assert_called_once()

        # Verify task manager interaction (simplified check)
        self.mock_task_manager.add_task.assert_called_once()
        call_args = self.mock_task_manager.add_task.call_args
        self.assertEqual(call_args.kwargs['type'], ActiveTaskType.PROJECT_GENERATION)


    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_hierarchical_planner_fails_to_generate_plan(
        self, mock_gen_exec_summary, mock_rephraser, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a very complex AI system"
        self.mock_planner_agent.create_plan_with_llm.return_value = [] # Simple planner fails
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = [] # Hierarchical planner also fails

        mock_rephraser.return_value = "I tried, but couldn't break down the complex AI system task."
        mock_gen_exec_summary.return_value = "::Technical Summary H-Fail::"

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, "I tried, but couldn't break down the complex AI system task.::Technical Summary H-Fail::")

        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
        self.mock_task_manager.add_task.assert_not_called()
        self.mock_execution_agent.execute_plan.assert_not_called()
        # Check that the rephraser was called with the specific message from orchestrator context
        mock_rephraser.assert_called_once_with(
            technical_error_message="Hierarchical planner failed to produce a detailed project plan.",
            original_user_query=user_prompt,
            llm_provider=self.mock_llm_provider
        )

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_task_manager_unavailable_for_hierarchical(
        self, mock_gen_exec_summary, mock_rephraser, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a project without task manager"
        self.orchestrator.task_manager = None # Simulate TaskManager not being available

        self.mock_planner_agent.create_plan_with_llm.return_value = []
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = [{"step_id": "1", "type": "informational", "details": {}}]

        mock_rephraser.return_value = "Cannot manage the project as TaskManager is offline."
        mock_gen_exec_summary.return_value = "::Technical Summary TM-Fail::"

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, "Cannot manage the project as TaskManager is offline.::Technical Summary TM-Fail::")
        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
        # self.mock_task_manager.add_task is not available on orchestrator.task_manager=None
        self.mock_execution_agent.execute_plan.assert_not_called()
        mock_rephraser.assert_called_once_with(
            technical_error_message="TaskManager not available, cannot execute complex project.",
            original_user_query=user_prompt,
            llm_provider=self.mock_llm_provider
        )

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary') # Reverted to default MagicMock
    async def test_process_prompt_hierarchical_plan_execution_tool_fails(
        self, mock_gen_exec_summary, mock_rephraser, mock_summarizer, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop project where execution tool fails"
        self.mock_planner_agent.create_plan_with_llm.return_value = []
        mock_project_plan = [{"step_id": "1", "type": "python_script", "details": {"script_content": "print('hi')"}}]
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = mock_project_plan

        mock_active_task = MagicMock(spec=ActiveTask)
        mock_active_task.task_id = "hp_task_exec_fail"
        self.mock_task_manager.add_task.return_value = mock_active_task

        # Simulate execute_project_plan tool itself reporting an error
        tool_execution_failure_result = {"overall_status": "failed", "error_message": "Tool execute_project_plan had an internal error."}
        # The plan passed to execute_plan will be the one generated by orchestrator to call execute_project_plan tool
        # It returns this plan, and the result from the tool.
        self.mock_execution_agent.execute_plan.return_value = (
            [{"tool_name": "execute_project_plan", "args": {}}], # Mocked plan that was attempted
            [tool_execution_failure_result] # Result from the tool
        )

        # Summarizer might still be called, or rephraser if summarizer fails on error
        mock_summarizer.return_value = None # Simulate summarizer not handling this error type directly
        mock_rephraser.return_value = "The project execution step itself encountered a problem: Tool execute_project_plan had an internal error."

        # Configure mock_gen_exec_summary with a side_effect
        def mock_side_effect_func(*args, **kwargs):
            print(f"DEBUG_MOCK_GEN_EXEC_SUMMARY_CALLED_WITH_ARGS: {args}")
            print(f"DEBUG_MOCK_GEN_EXEC_SUMMARY_CALLED_WITH_KWARGS: {kwargs}")
            # Ensure that the first argument (self) is handled if it's part of *args
            # The actual plan is args[1] if self is args[0], or args[0] if self is not included (e.g. unbound method patch)
            # Based on @patch for an instance method, 'self' of DynamicOrchestrator won't be part of *args here.
            # So, args[0] is 'plan', args[1] is 'results'.
            if len(args) > 0:
                 print(f"DEBUG_MOCK_GEN_EXEC_SUMMARY_PLAN_ARG_TYPE: {type(args[0])}")
                 print(f"DEBUG_MOCK_GEN_EXEC_SUMMARY_PLAN_ARG_VALUE: {str(args[0])[:200]}") # Print first 200 chars
            if len(args) > 1:
                 print(f"DEBUG_MOCK_GEN_EXEC_SUMMARY_RESULTS_ARG_TYPE: {type(args[1])}")
                 print(f"DEBUG_MOCK_GEN_EXEC_SUMMARY_RESULTS_ARG_VALUE: {str(args[1])[:200]}") # Print first 200 chars
            return "::Technical Summary Tool-Fail::"

        mock_gen_exec_summary.side_effect = mock_side_effect_func

        success, response, images = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        # Based on current orchestrator logic, if summarizer returns None, it falls back to rephrased error + technical summary
        self.assertEqual(response, "The project execution step itself encountered a problem: Tool execute_project_plan had an internal error.::Technical Summary Tool-Fail::")

        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
        self.mock_task_manager.add_task.assert_called_once()
        self.mock_execution_agent.execute_plan.assert_called_once()

        # Check that rephraser was called with the error from the tool
        # The orchestrator extracts the error from the tool's result dict.
        # The exact message passed to rephraser might vary based on how orchestrator extracts it.
        # For this test, we check if the core error from the tool was part of what rephraser received.
        # The actual error passed to rephrase_error_message_conversationally is constructed by the orchestrator
        # from the tool_execution_failure_result.
        # It would be something like: "A tool reported an error: {'overall_status': 'failed', 'error_message': 'Tool execute_project_plan had an internal error.'}"
        # or a more direct extraction.
        # For this test, we assume the rephraser gets the specific error message from the tool.
        self.mock_rephraser.assert_called_once()
        rephraser_args = self.mock_rephraser.call_args[0] # Get positional arguments
        self.assertIn("Tool execute_project_plan had an internal error.", rephraser_args[0]) # technical_error_message
        self.assertEqual(rephraser_args[1], user_prompt) # original_user_query

        mock_summarizer.assert_called_once() # Summarizer is called even on failure.


if __name__ == '__main__': # pragma: no cover
    unittest.main()
