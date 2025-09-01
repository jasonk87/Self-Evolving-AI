import unittest
from unittest.mock import MagicMock, AsyncMock, patch, ANY
from typing import List, Dict, Any, Optional, Tuple
from unittest import mock

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
    from ai_assistant.code_services.service import CodeService # For mocking path to llm_provider
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

        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": mock_plan}
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results) # final_plan, results
        mock_summarizer.return_value = expected_conversational_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.assertEqual(response['chat_response'], expected_conversational_summary)
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

        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": mock_plan}
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_summarizer.return_value = None # Simulate summarizer failure
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success) # Assuming plan itself succeeded
        self.assertIn(technical_summary, response['chat_response'])
        # Check that the initial part of the fallback response is there
        self.assertTrue(response['chat_response'].startswith("Successfully completed the task."))
        mock_summarizer.assert_called_once()
        mock_generate_exec_summary.assert_called_once_with(mock_plan, mock_results)

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_no_plan_created_conversational_fallback_succeeds(self, mock_summarizer):
        user_prompt = "A very complex prompt leading to no plan"
        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": []} # No plan
        # The mock LLM provider is configured in setUp to return "Default mock LLM response"
        # which simulates a successful conversational fallback.

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success) # Should be True because conversational fallback is a valid response
        self.assertEqual(response['chat_response'], "Default mock LLM response")
        mock_summarizer.assert_not_called()

    # --- Tests for Conversational Error Rephrasing ---

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_no_plan_created_error_rephrased_succeeds(
            self, mock_generate_exec_summary, mock_rephraser):
        user_prompt = "Goal leading to no plan"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = None

        technical_error_msg_from_orchestrator = "Conversational model returned an empty response."
        rephrased_error = "I couldn't figure out a plan for that, sorry!"
        technical_summary = "::Technical Summary No Plan::"

        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": []}
        mock_rephraser.return_value = rephrased_error
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response['chat_response'], rephrased_error + technical_summary)
        mock_rephraser.assert_called_once_with(
            technical_error_message=technical_error_msg_from_orchestrator,
            original_user_query=user_prompt,
            llm_provider=self.mock_llm_provider
        )
        mock_generate_exec_summary.assert_called_once_with([], [])

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_no_plan_created_error_rephraser_fails(
            self, mock_generate_exec_summary, mock_rephraser):
        user_prompt = "Goal leading to no plan"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = None

        technical_error_msg = "Conversational model returned an empty response."
        technical_summary = "::Technical Summary No Plan Fallback::"

        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": []}
        mock_rephraser.return_value = None
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response['chat_response'], technical_error_msg + technical_summary)
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

        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": mock_plan}
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_rephraser.return_value = rephrased_error
        mock_summarizer.return_value = None # Simulate summarizer also failing
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        expected_response = rephrased_error
        if not rephrased_error.endswith(('.', '\n', '!', '?')):
            expected_response += "."
        expected_response += technical_summary
        self.assertEqual(response['chat_response'], expected_response)
        mock_rephraser.assert_called_once_with(
            technical_error_message=f"An error occurred: Exception: {technical_error_detail}",
            original_user_query=user_prompt,
            llm_provider=self.mock_llm_provider
        )
        mock_summarizer.assert_called_once()
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
        expected_fallback_error_message = f"An error occurred: Exception: {technical_error_detail}"
        technical_summary = "::Technical Execution Summary Fallback::"

        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": mock_plan}
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_rephraser.return_value = None # Rephraser fails
        mock_summarizer.return_value = None # Summarizer also fails
        mock_generate_exec_summary.return_value = technical_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        # It should fall back to the technical error detail + the summary
        self.assertEqual(response['chat_response'], expected_fallback_error_message + "." + technical_summary)
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
        self.assertEqual(response['chat_response'], rephrased_error)
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
        self.assertEqual(response['chat_response'], expected_technical_response) # Falls back to technical
        mock_rephraser.assert_called_once()

    # --- End of Tests for Conversational Error Rephrasing ---

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_plan_fails_summarizer_succeeds(self, mock_generate_exec_summary, mock_summarizer):
        user_prompt = "Prompt that causes plan failure"
        mock_plan = [{"tool_name": "failing_tool", "args": (), "kwargs": {}}]
        mock_results = [Exception("Tool failed")]
        expected_conversational_failure_summary = "It seems there was an issue with the 'failing_tool'."

        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": mock_plan}
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results) # overall_success will be False
        mock_summarizer.return_value = expected_conversational_failure_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response['chat_response'], expected_conversational_failure_summary)
        mock_summarizer.assert_called_once_with(
            original_user_query=user_prompt,
            executed_plan_steps=mock_plan,
            tool_results=mock_results,
            overall_success=False, # This is key
            llm_provider=self.mock_llm_provider
        )
        mock_generate_exec_summary.assert_not_called() # Because conversational summary succeeded

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_plan_fails_summarizer_fails_too(self, mock_generate_exec_summary, mock_summarizer, mock_rephraser):
        user_prompt = "Prompt for double failure"
        mock_plan = [{"tool_name": "another_failing_tool", "args": (), "kwargs": {}}]
        mock_results = [RuntimeError("Critical tool error")]
        technical_summary_fallback = "\n\nHere's a summary of what I did:\n- Ran 'another_failing_tool'..."
        rephrased_error = "There was a problem running the tools."

        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": mock_plan}
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_summarizer.return_value = None
        mock_rephraser.return_value = rephrased_error
        mock_generate_exec_summary.return_value = technical_summary_fallback

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        expected_response = rephrased_error + technical_summary_fallback
        self.assertEqual(response['chat_response'], expected_response)
        mock_summarizer.assert_called_once()
        mock_rephraser.assert_called_once()
        mock_generate_exec_summary.assert_called_once()

    # --- Tests for Hierarchical Planner Integration ---

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False) # Keep debug prints quiet
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_simple_plan_success(self, mock_summarizer, mock_log_event, mock_debug_mode):
        user_prompt = "Show me a list of files."
        simple_plan = [{"tool_name": "list_files", "args": (), "description": "List files in current directory"}]
        execution_results = ["file1.txt, file2.py"]

        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": simple_plan}
        self.mock_execution_agent.execute_plan.return_value = (simple_plan, execution_results)
        mock_summarizer.return_value = "I listed the files for you: file1.txt, file2.py"

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.assertEqual(response['chat_response'], "I listed the files for you: file1.txt, file2.py")
        self.mock_planner_agent.create_plan_with_llm.assert_called_once()
        self.mock_hierarchical_planner.generate_full_project_plan.assert_not_called()
        self.mock_execution_agent.execute_plan.assert_called_once()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_triggers_hierarchical_on_project_intent(
        self, mock_summarizer, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a new python project for web scraping"
        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": []}

        mock_project_plan = [{"step_id": "1", "type": "informational", "description": "Setup project"}]
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = mock_project_plan

        mock_active_task = MagicMock(spec=ActiveTask)
        mock_active_task.task_id = "hp_task_123"
        mock_active_task.details = {"project_name_for_context": "web scraping project"}
        self.mock_task_manager.add_task.return_value = mock_active_task

        # This test now simulates the fallback path where project intent is FALSE, but HP is triggered by keywords
        self.mock_execution_agent.execute_plan.return_value = (mock.MagicMock(), [{"status": "ok"}])
        mock_summarizer.return_value = "Project started via fallback."

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.assertEqual(response['chat_response'], "Project started via fallback.")

        self.mock_planner_agent.create_plan_with_llm.assert_called_once()
        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
        self.mock_task_manager.add_task.assert_called_once()
        self.mock_execution_agent.execute_plan.assert_called_once()


    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_hierarchical_planner_fails_to_generate_plan(
        self, mock_gen_exec_summary, mock_rephraser, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a very complex AI system"
        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": []}
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = []
        self.mock_llm_provider.invoke_ollama_model_async.return_value = None

        rephrased_error = "I tried, but couldn't break down the complex AI system task."
        mock_rephraser.return_value = rephrased_error
        mock_gen_exec_summary.return_value = "::Technical Summary H-Fail::"

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response['chat_response'], rephrased_error + "::Technical Summary H-Fail::")

        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
        self.mock_task_manager.add_task.assert_not_called()
        self.mock_execution_agent.execute_plan.assert_not_called()
        mock_rephraser.assert_called_once_with(
            technical_error_message="Conversational model returned an empty response.",
            original_user_query=user_prompt,
            llm_provider=self.mock_llm_provider
        )

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_task_manager_unavailable_for_hierarchical_falls_back_to_direct_execution(
        self, mock_summarizer, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a project without task manager"
        self.orchestrator.task_manager = None
        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": []}
        mock_hp_plan = [{"tool_name": "some_tool", "args": (), "description": "Step 1"}]
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = mock_hp_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_hp_plan, ["Success!"])
        mock_summarizer.return_value = "I did the thing: Success!"

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.assertEqual(response['chat_response'], "I did the thing: Success!")
        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
        self.mock_execution_agent.execute_plan.assert_called_once_with(
            user_prompt,
            mock_hp_plan,
            mock.ANY, mock.ANY, mock.ANY, task_manager=None, notification_manager=mock.ANY, current_plan_large_content_store=mock.ANY
        )

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.DynamicOrchestrator._generate_execution_summary')
    async def test_process_prompt_hierarchical_plan_execution_tool_fails(
        self, mock_gen_exec_summary, mock_rephraser, mock_summarizer, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a complex feature"
        self.mock_planner_agent.create_plan_with_llm.return_value = {"plan": []}
        mock_project_plan = [{"step_id": "1", "type": "python_script"}]
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = mock_project_plan

        mock_active_task = MagicMock(spec=ActiveTask)
        mock_active_task.task_id = "hp_task_exec_fail"
        mock_active_task.details = {"project_name_for_context": "complex feature"}
        self.mock_task_manager.add_task.return_value = mock_active_task

        tool_execution_failure_result = {"overall_status": "failed", "error_message": "Tool execute_project_plan had an internal error."}

        orchestrator_plan = [{
            "tool_name": "execute_project_plan",
            "args": mock.ANY,
            "description": mock.ANY,
            "reasoning": mock.ANY
        }]

        self.mock_execution_agent.execute_plan.return_value = (orchestrator_plan, [tool_execution_failure_result])

        mock_summarizer.return_value = None
        mock_rephraser.return_value = "The project execution step itself encountered a problem."
        mock_gen_exec_summary.return_value = "::Technical Summary Tool-Fail::"

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response['chat_response'], "The project execution step itself encountered a problem.::Technical Summary Tool-Fail::")

        self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
        self.mock_task_manager.add_task.assert_called_once()
        self.mock_execution_agent.execute_plan.assert_called_once()

        mock_rephraser.assert_called_once()
        self.assertIn("A tool reported an error: Tool execute_project_plan had an internal error.", mock_rephraser.call_args.kwargs['technical_error_message'])

        mock_summarizer.assert_called_once()


if __name__ == '__main__': # pragma: no cover
    unittest.main()
