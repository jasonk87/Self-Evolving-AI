import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from typing import List, Dict, Any, Optional, Tuple

# Attempt to import from the project structure
try:
    from ai_assistant.core.orchestrator import DynamicOrchestrator
    from ai_assistant.planning.planning import PlannerAgent
    from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner # Added
    from ai_assistant.planning.execution import ExecutionAgent # If this error is used
    from ai_assistant.learning.learning import LearningAgent
    from ai_assistant.execution.action_executor import ActionExecutor
    from ai_assistant.tools.tool_system import ToolSystem # Assuming ToolSystem is used by ExecutionAgent
    from ai_assistant.llm_interface.ollama_client import OllamaProvider # For type mocking
    # from ai_assistant.code_services.service import CodeService # REMOVED
    from ai_assistant.core.task_manager import TaskManager, ActiveTask, ActiveTaskType # Added ActiveTask, ActiveTaskType
    # summarize_tool_result_conversationally and rephrase_error_message_conversationally will be patched
except ImportError: # pragma: no cover
    import sys
    import os
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core.orchestrator import DynamicOrchestrator
    from ai_assistant.planning.planning import PlannerAgent
    from ai_assistant.planning.execution import ExecutionAgent
    from ai_assistant.learning.learning import LearningAgent
    from ai_assistant.execution.action_executor import ActionExecutor
    from ai_assistant.tools.tool_system import ToolSystem
    from ai_assistant.llm_interface.ollama_client import OllamaProvider
    # from ai_assistant.code_services.service import CodeService # REMOVED


class TestDynamicOrchestrator(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_planner_agent = AsyncMock(spec=PlannerAgent)
        self.mock_execution_agent = AsyncMock(spec=ExecutionAgent)
        self.mock_learning_agent = MagicMock(spec=LearningAgent)

        # Mock the ActionExecutor and its nested CodeService and llm_provider
        self.mock_llm_provider = AsyncMock(spec=OllamaProvider)
        # Mocking the property structure expected: self.code_service.llm_provider
        # Since CodeService is removed, we mock the object that ActionExecutor exposes as 'code_service' property
        self.mock_code_service_wrapper = MagicMock()
        self.mock_code_service_wrapper.llm_provider = self.mock_llm_provider

        # Set a default string return value for the mock LLM provider's async method
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "Default mock LLM response"

        self.mock_action_executor = MagicMock(spec=ActionExecutor)
        # ActionExecutor.code_service is now a property returning a wrapper with llm_provider
        self.mock_action_executor.code_service = self.mock_code_service_wrapper

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
        # Patch load_learned_facts used in orchestrator
        self.load_facts_patcher = patch('ai_assistant.core.orchestrator.load_learned_facts', MagicMock(return_value=[]))
        self.mock_load_facts = self.load_facts_patcher.start()


    def tearDown(self):
        self.tool_system_patcher.stop()
        self.load_facts_patcher.stop()

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_summarizer_succeeds(self, mock_summarizer):
        user_prompt = "Test user prompt"
        mock_plan = [{"tool_name": "mock_tool", "args": (), "kwargs": {}}]
        mock_results = ["Mock tool result"]
        expected_conversational_summary = "This is a great conversational summary."

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results) # final_plan, results
        mock_summarizer.return_value = expected_conversational_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.assertEqual(response, expected_conversational_summary)
        mock_summarizer.assert_called_once_with(
            original_user_query=user_prompt,
            executed_plan_steps=mock_plan,
            tool_results=mock_results,
            overall_success=True,
            llm_provider=self.mock_llm_provider
        )

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary') # Patch the instance method
    async def test_process_prompt_summarizer_fails_falls_back_to_technical_summary(self, mock_generate_exec_summary, mock_summarizer):
        user_prompt = "Test prompt for summarizer failure"
        mock_plan = [{"tool_name": "another_mock_tool", "args": ("arg1",), "kwargs": {}}]
        mock_results = ["Another mock result"]
        technical_summary = "\n\nHere's a summary of what I did:\n- Ran 'another_mock_tool'..."

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_summarizer.return_value = None # Simulate summarizer failure
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success) # Assuming plan itself succeeded
        self.assertTrue(technical_summary in response)
        # Check that the initial part of the fallback response is there
        self.assertTrue(response.startswith("Successfully completed the task."))
        mock_summarizer.assert_called_once()
        mock_generate_exec_summary.assert_called_once_with(mock_plan, mock_results)

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_no_plan_created(self, mock_summarizer):
        user_prompt = "A very complex prompt leading to no plan"
        self.mock_planner_agent.create_plan_with_llm.return_value = [] # No plan

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        # Check if the default mock LLM response (from rephrasing) is in the response,
        # plus the technical summary for no actions.
        expected_response_part = self.mock_llm_provider.invoke_ollama_model_async.return_value
        expected_technical_summary = self.orchestrator._generate_execution_summary([], [])
        self.assertEqual(response, expected_response_part + expected_technical_summary)
        mock_summarizer.assert_not_called() # Summarizer should not be called if no plan

    # --- Tests for Conversational Error Rephrasing ---

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_no_plan_created_error_rephrased_succeeds(
            self, mock_generate_exec_summary, mock_rephraser):
        user_prompt = "Goal leading to no plan"
        technical_error_msg = "Could not create a plan for the given prompt."
        rephrased_error = "I couldn't figure out a plan for that, sorry!"
        technical_summary = "::Technical Summary No Plan::"

        self.mock_planner_agent.create_plan_with_llm.return_value = []  # No plan
        mock_rephraser.return_value = rephrased_error
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, rephrased_error + technical_summary)
        mock_rephraser.assert_called_once_with(
            technical_error_message=technical_error_msg,
            original_user_query=user_prompt,
            llm_provider=self.mock_llm_provider
        )
        mock_generate_exec_summary.assert_called_once_with([], []) # Called with (None, []) or ([], [])

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_no_plan_created_error_rephraser_fails(
            self, mock_generate_exec_summary, mock_rephraser):
        user_prompt = "Goal leading to no plan"
        technical_error_msg = "Could not create a plan for the given prompt."
        technical_summary = "::Technical Summary No Plan Fallback::"

        self.mock_planner_agent.create_plan_with_llm.return_value = []
        mock_rephraser.return_value = None # Rephraser fails
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, technical_error_msg + technical_summary) # Falls back to technical
        mock_rephraser.assert_called_once()
        mock_generate_exec_summary.assert_called_once()

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_plan_execution_fails_error_rephrased_succeeds(
            self, mock_generate_exec_summary, mock_summarizer, mock_rephraser):
        user_prompt = "Prompt for execution failure"
        mock_plan = [{"tool_name": "failing_tool", "args": (), "kwargs": {}}]
        technical_error_detail = "Tool failed with specific details"
        mock_results = [Exception(technical_error_detail)]
        rephrased_error = "It seems a step in the plan didn't go as expected!"
        technical_summary = "::Technical Execution Summary::"

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_rephraser.return_value = rephrased_error
        mock_summarizer.return_value = None # Simulate summarizer also failing or not providing primary content for failure
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        # The rephrased error should be primary, followed by the technical summary because summarize_tool_result_conversationally returned None
        # Orchestrator adds a '.' if the rephrased_error doesn't end with punctuation.
        expected_response = rephrased_error
        if not rephrased_error.endswith(('.', '\n', '!', '?')):
            expected_response += "."
        expected_response += technical_summary
        self.assertEqual(response, expected_response)
        mock_rephraser.assert_called_once_with(
            technical_error_message=f"An error occurred: Exception: {technical_error_detail}",
            original_user_query=user_prompt,
            llm_provider=self.mock_llm_provider
        )
        mock_summarizer.assert_called_once() # Summarizer is still called for failures
        mock_generate_exec_summary.assert_called_once_with(mock_plan, mock_results)

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_plan_execution_fails_rephraser_fails(
            self, mock_generate_exec_summary, mock_summarizer, mock_rephraser):
        user_prompt = "Prompt for execution failure, rephraser fails"
        mock_plan = [{"tool_name": "failing_tool", "args": (), "kwargs": {}}]
        technical_error_detail = "Tool failed badly"
        mock_results = [Exception(technical_error_detail)]
        # The rephraser is mocked to return None, so its internal "I encountered an issue" is used by orchestrator's rephrasing block.
        expected_initial_message_from_rephrase_block = f"An error occurred: Exception: {technical_error_detail}"


        technical_summary = "::Technical Execution Summary Fallback::"

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_rephraser.return_value = None # Rephraser fails
        mock_summarizer.return_value = None # Summarizer also fails
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        # We need to account for potential punctuation additions in the orchestrator
        expected_response = expected_initial_message_from_rephrase_block
        if not expected_response.endswith('.'):
             expected_response += "."
        expected_response += technical_summary

        self.assertEqual(response, expected_response)
        mock_rephraser.assert_called_once()
        mock_summarizer.assert_called_once()
        mock_generate_exec_summary.assert_called_once()

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_orchestrator_exception_rephrased_succeeds(self, mock_rephraser):
        user_prompt = "Prompt causing orchestrator error"
        orchestrator_error_msg = "Orchestrator boom"
        rephrased_error = "Something unexpected happened while I was trying to process that."

        self.mock_planner_agent.create_plan_with_llm.side_effect = Exception(orchestrator_error_msg)
        mock_rephraser.return_value = rephrased_error

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, rephrased_error)
        mock_rephraser.assert_called_once_with(
            technical_error_message=orchestrator_error_msg,
            original_user_query=user_prompt,
            llm_provider=self.mock_llm_provider
        )

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_orchestrator_exception_rephraser_fails(self, mock_rephraser):
        user_prompt = "Prompt causing orchestrator error, rephraser fails"
        orchestrator_error_msg = "Orchestrator critical failure"
        expected_technical_response = f"Error during orchestration: {orchestrator_error_msg}"

        self.mock_planner_agent.create_plan_with_llm.side_effect = Exception(orchestrator_error_msg)
        mock_rephraser.return_value = None # Rephraser fails

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, expected_technical_response) # Falls back to technical
        mock_rephraser.assert_called_once()

    # --- End of Tests for Conversational Error Rephrasing ---

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_plan_fails_summarizer_succeeds(self, mock_generate_exec_summary, mock_summarizer):
        user_prompt = "Prompt that causes plan failure"
        mock_plan = [{"tool_name": "failing_tool", "args": (), "kwargs": {}}]
        mock_results = [Exception("Tool failed")]
        expected_conversational_failure_summary = "It seems there was an issue with the 'failing_tool'."

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results) # overall_success will be False
        mock_summarizer.return_value = expected_conversational_failure_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, expected_conversational_failure_summary)
        mock_summarizer.assert_called_once_with(
            original_user_query=user_prompt,
            executed_plan_steps=mock_plan,
            tool_results=mock_results,
            overall_success=False, # This is key
            llm_provider=self.mock_llm_provider
        )
        mock_generate_exec_summary.assert_not_called() # Because conversational summary succeeded

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_plan_fails_summarizer_fails_too(self, mock_generate_exec_summary, mock_summarizer):
        user_prompt = "Prompt for double failure"
        mock_plan = [{"tool_name": "another_failing_tool", "args": (), "kwargs": {}}]
        mock_results = [RuntimeError("Critical tool error")]
        technical_summary_fallback = "\n\nHere's a summary of what I did:\n- Ran 'another_failing_tool'..."

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results) # overall_success will be False
        mock_summarizer.return_value = None # Summarizer fails
        mock_generate_exec_summary.return_value = technical_summary_fallback

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        # Expect the rephrased response (default from LLM mock) + technical summary
        expected_start = self.mock_llm_provider.invoke_ollama_model_async.return_value
        self.assertTrue(response.startswith(expected_start))
        self.assertIn(technical_summary_fallback, response)
        mock_summarizer.assert_called_once()
        mock_generate_exec_summary.assert_called_once()

    # --- Tests for Hierarchical Planner Integration ---

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False) # Keep debug prints quiet
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_simple_plan_success(self, mock_summarizer, mock_log_event, mock_debug_mode):
        user_prompt = "Show me a list of files."
        simple_plan = [{"tool_name": "list_files", "args": (), "description": "List files in current directory"}]
        execution_results = ["file1.txt, file2.py"]

        self.mock_planner_agent.create_plan_with_llm.return_value = simple_plan
        self.mock_execution_agent.execute_plan.return_value = (simple_plan, execution_results)
        mock_summarizer.return_value = "I listed the files for you: file1.txt, file2.py"

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.assertEqual(response, "I listed the files for you: file1.txt, file2.py")
        self.mock_planner_agent.create_plan_with_llm.assert_called_once()
        self.mock_hierarchical_planner.generate_full_project_plan.assert_not_called()
        self.mock_execution_agent.execute_plan.assert_called_once()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=True) # Enable debug for prints
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_triggers_hierarchical_on_empty_simple_plan_and_keywords(
        self, mock_rephraser, mock_summarizer, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a new python project for web scraping"
        project_context_summary = "Some project context" # Assume this is built earlier

        self.mock_planner_agent.create_plan_with_llm.return_value = [] # Simple planner returns no plan

        mock_project_plan = [
            {"step_id": "1", "type": "informational", "description": "Setup project", "details": {"message": "Setup complete"}}
        ]
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = mock_project_plan

        mock_active_task = MagicMock(spec=ActiveTask)
        mock_active_task.task_id = "hp_task_123"
        self.mock_task_manager.add_task.return_value = mock_active_task

        # Simulate execute_project_plan tool succeeding
        # IMPORTANT: The Orchestrator's process_prompt calls execute_plan.
        # execute_plan returns (final_plan, results).
        # We need to ensure that the mocked return value for execute_plan simulates success properly.
        # Specifically, orchestrator checks `overall_success_of_plan`.

        executed_tool_plan = [{"tool_name": "execute_project_plan", "args": {}, "description": "Execute plan"}]

        # The result needs to be robust for orchestrator checks.
        # The orchestrator uses the return value of execute_plan to determine success.
        execution_results = [{"ran_successfully": True, "overall_status": "success", "step_results": [{"status": "success"}]}]

        # We mock execute_plan to return the plan that was supposedly executed and the results.
        self.mock_execution_agent.execute_plan.return_value = (executed_tool_plan, execution_results)

        mock_summarizer.return_value = "I've started your web scraping project!"

        # Need to mock the ActiveTaskType if not imported correctly in orchestrator
        # But we import TaskManager, ActiveTask, ActiveTaskType in the test file.
        # We assume orchestrator imports it correctly.

        # Mock rephraser to return a string, so that if process_prompt fails, we get a string response instead of a Mock object in failure messages
        mock_rephraser.return_value = "I encountered an error."

        success, response = await self.orchestrator.process_prompt(user_prompt)

        # Debugging output if failure persists
        if not success:
            print(f"DEBUG: process_prompt returned False. Response: {response}")

        self.assertTrue(success)
        self.assertEqual(response, "I've started your web scraping project!")

        self.mock_planner_agent.create_plan_with_llm.assert_called_once()
        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once_with(
            user_goal=user_prompt,
            project_context=mock.ANY
        )
        self.mock_task_manager.add_task.assert_called_once_with(
            task_type=ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION,
            description=mock.ANY,
            details={
                "project_plan": mock_project_plan,
                "user_goal": user_prompt,
                "project_name": mock.ANY
            }
        )
        self.assertIn(user_prompt[:100], self.mock_task_manager.add_task.call_args.kwargs['description'])

        # execute_plan should be called with the single-step plan to execute the project
        # The first arg is prompt, second is plan.
        self.mock_execution_agent.execute_plan.assert_called_once()
        # Verify the plan passed to execute_plan
        call_args = self.mock_execution_agent.execute_plan.call_args
        self.assertIsNotNone(call_args)
        # prompt arg
        self.assertEqual(call_args[0][0], user_prompt)
        # plan arg
        plan_passed = call_args[0][1]
        self.assertEqual(len(plan_passed), 1)
        self.assertEqual(plan_passed[0]["tool_name"], "execute_project_plan")


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

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        # Ensure correct spacing if punctuation is missing
        expected_response = "I tried, but couldn't break down the complex AI system task."
        if not expected_response.endswith('.'):
             expected_response += "."
        expected_response += "::Technical Summary H-Fail::"

        self.assertEqual(response, expected_response)

        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
        self.mock_task_manager.add_task.assert_not_called()
        self.mock_execution_agent.execute_plan.assert_not_called()
        # Check that the rephraser was called with the specific message from orchestrator context
        # The Orchestrator's internal message might vary slightly
        mock_rephraser.assert_called_once()
        args = mock_rephraser.call_args
        self.assertIn("Hierarchical planner failed to produce a detailed project plan.", args.kwargs['technical_error_message'])
        self.assertEqual(args.kwargs['original_user_query'], user_prompt)

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

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)

        # Account for potential punctuation
        expected_response = "Cannot manage the project as TaskManager is offline."
        if not expected_response.endswith('.'):
             expected_response += "."
        expected_response += "::Technical Summary TM-Fail::"

        self.assertEqual(response, expected_response)
        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
        # self.mock_task_manager.add_task is not available on orchestrator.task_manager=None
        self.mock_execution_agent.execute_plan.assert_not_called()
        mock_rephraser.assert_called_once()
        args = mock_rephraser.call_args
        self.assertIn("TaskManager not available", args.kwargs['technical_error_message'])
        self.assertEqual(args.kwargs['original_user_query'], user_prompt)

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
            return "::Technical Summary Tool-Fail::"

        mock_gen_exec_summary.side_effect = mock_side_effect_func

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        # Based on current orchestrator logic, if summarizer returns None, it falls back to rephrased error + technical summary
        # And we need to account for punctuation addition.
        expected_response_part1 = "The project execution step itself encountered a problem: Tool execute_project_plan had an internal error."
        if not expected_response_part1.endswith('.'):
             expected_response_part1 += "."

        self.assertEqual(response, expected_response_part1 + "::Technical Summary Tool-Fail::")

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
        mock_rephraser.assert_called_once()
        args = mock_rephraser.call_args
        # The Orchestrator might format the error message slightly differently
        self.assertIn("Tool execute_project_plan had an internal error.", args.kwargs['technical_error_message'])
        self.assertEqual(args.kwargs['original_user_query'], user_prompt)

        mock_summarizer.assert_called_once() # Summarizer is called even on failure.


if __name__ == '__main__': # pragma: no cover
    unittest.main()
