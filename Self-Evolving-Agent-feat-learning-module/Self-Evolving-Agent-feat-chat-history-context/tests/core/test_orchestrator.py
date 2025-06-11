import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from typing import List, Dict, Any, Optional, Tuple

# Attempt to import from the project structure
try:
    from ai_assistant.core.orchestrator import DynamicOrchestrator
    from ai_assistant.planning.planning import PlannerAgent
    from ai_assistant.planning.execution import ExecutionAgent, AwaitingUserInputError # If this error is used
    from ai_assistant.learning.learning import LearningAgent
    from ai_assistant.execution.action_executor import ActionExecutor
    from ai_assistant.tools.tool_system import ToolSystem # Assuming ToolSystem is used by ExecutionAgent
    from ai_assistant.llm_interface.ollama_client import OllamaProvider # For type mocking
    from ai_assistant.code_services.service import CodeService # For mocking path to llm_provider
    from ai_assistant.core.task_manager import TaskManager # Added for spec
    # summarize_tool_result_conversationally and rephrase_error_message_conversationally will be patched
except ImportError: # pragma: no cover
    import sys
    import os
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core.orchestrator import DynamicOrchestrator
    from ai_assistant.planning.planning import PlannerAgent
    from ai_assistant.planning.execution import ExecutionAgent, AwaitingUserInputError
    from ai_assistant.learning.learning import LearningAgent
    from ai_assistant.execution.action_executor import ActionExecutor
    from ai_assistant.tools.tool_system import ToolSystem
    from ai_assistant.llm_interface.ollama_client import OllamaProvider
    from ai_assistant.code_services.service import CodeService


class TestDynamicOrchestrator(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_planner_agent = AsyncMock(spec=PlannerAgent)
        self.mock_execution_agent = AsyncMock(spec=ExecutionAgent)
        self.mock_learning_agent = MagicMock(spec=LearningAgent)

        # Mock the ActionExecutor and its nested CodeService and llm_provider
        self.mock_llm_provider = AsyncMock(spec=OllamaProvider)
        self.mock_code_service = MagicMock(spec=CodeService)
        self.mock_code_service.llm_provider = self.mock_llm_provider
        self.mock_action_executor = MagicMock(spec=ActionExecutor)
        self.mock_action_executor.code_service = self.mock_code_service

        self.mock_task_manager = MagicMock(spec=TaskManager) # Optional, but good to have
        self.mock_notification_manager = MagicMock() # Optional

        self.orchestrator = DynamicOrchestrator(
            planner=self.mock_planner_agent,
            executor=self.mock_execution_agent,
            learning_agent=self.mock_learning_agent,
            action_executor=self.mock_action_executor,
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager
        )
        # Mock tool_system_instance used internally by orchestrator if it's not passed in
        # It seems tool_system_instance is imported globally by orchestrator.py
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
        self.assertIn("Could not create a plan", response)
        mock_summarizer.assert_not_called() # Summarizer should not be called if no plan
        # Test the rephrasing part is not called here as it's handled by specific rephrasing tests
        # For the basic "no plan" case, we are now testing rephrasing separately.
        # This test ensures the old basic "Could not create a plan" still works if rephraser is NOT explicitly mocked to change it.
        # To test rephrasing, we'll add specific tests below.

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
        self.assertEqual(response, rephrased_error + technical_summary)
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
        # expected_fallback_error_message = f"Could not complete the task fully. An error occurred: Exception: {technical_error_detail}"
        # The above is what the orchestrator's internal fallback logic would generate if rephraser fails.
        # The rephraser is mocked to return None, so its internal "I encountered an issue" is used by orchestrator's rephrasing block.
        expected_initial_message_from_rephrase_block = "I encountered an issue."


        technical_summary = "::Technical Execution Summary Fallback::"

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_rephraser.return_value = None # Rephraser fails
        mock_summarizer.return_value = None # Summarizer also fails
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, expected_initial_message_from_rephrase_block + technical_summary)
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
        self.assertTrue(response.startswith("Could not complete the task fully."))
        self.assertIn(technical_summary_fallback, response)
        mock_summarizer.assert_called_once()
        mock_generate_exec_summary.assert_called_once()


if __name__ == '__main__': # pragma: no cover
    unittest.main()
```
