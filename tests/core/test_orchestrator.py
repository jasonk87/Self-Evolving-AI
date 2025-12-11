import unittest
from unittest import mock
from unittest.mock import MagicMock, AsyncMock, patch
from typing import List, Dict, Any, Optional, Tuple

# Attempt to import from the project structure
try:
    from ai_assistant.core.orchestrator import DynamicOrchestrator
    from ai_assistant.planning.planning import PlannerAgent
    from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner
    from ai_assistant.planning.execution import ExecutionAgent
    from ai_assistant.learning.learning import LearningAgent
    from ai_assistant.execution.action_executor import ActionExecutor
    from ai_assistant.tools.tool_system import ToolSystem
    from ai_assistant.llm_interface.ollama_client import OllamaProvider
    from ai_assistant.core.task_manager import TaskManager, ActiveTask, ActiveTaskType
except ImportError: # pragma: no cover
    import sys
    import os
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core.orchestrator import DynamicOrchestrator
    from ai_assistant.planning.planning import PlannerAgent
    from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner
    from ai_assistant.planning.execution import ExecutionAgent
    from ai_assistant.learning.learning import LearningAgent
    from ai_assistant.execution.action_executor import ActionExecutor
    from ai_assistant.tools.tool_system import ToolSystem
    from ai_assistant.llm_interface.ollama_client import OllamaProvider
    from ai_assistant.core.task_manager import TaskManager, ActiveTask, ActiveTaskType


class TestDynamicOrchestrator(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_planner_agent = AsyncMock(spec=PlannerAgent)
        self.mock_execution_agent = AsyncMock(spec=ExecutionAgent)
        self.mock_learning_agent = MagicMock(spec=LearningAgent)

        # Mock the ActionExecutor and its nested CodeService and llm_provider
        self.mock_llm_provider = AsyncMock(spec=OllamaProvider)
        # Mocking the property structure expected: self.code_service.llm_provider
        self.mock_code_service_wrapper = MagicMock()
        self.mock_code_service_wrapper.llm_provider = self.mock_llm_provider
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "Default mock LLM response"

        self.mock_action_executor = MagicMock(spec=ActionExecutor)
        self.mock_action_executor.code_service = self.mock_code_service_wrapper

        self.mock_task_manager = MagicMock(spec=TaskManager)
        self.mock_notification_manager = MagicMock()
        self.mock_hierarchical_planner = AsyncMock(spec=HierarchicalPlanner)

        self.orchestrator = DynamicOrchestrator(
            planner=self.mock_planner_agent,
            executor=self.mock_execution_agent,
            learning_agent=self.mock_learning_agent,
            action_executor=self.mock_action_executor,
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager,
            hierarchical_planner=self.mock_hierarchical_planner
        )
        self.tool_system_patcher = patch('ai_assistant.core.orchestrator.tool_system_instance', MagicMock(spec=ToolSystem))
        self.mock_tool_system = self.tool_system_patcher.start()
        self.mock_tool_system.list_tools_with_sources.return_value = {"mock_tool": {"description": "A mock tool"}}
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
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_summarizer.return_value = expected_conversational_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertTrue(success)
        self.assertEqual(response, expected_conversational_summary)

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_summarizer_fails_falls_back_to_technical_summary(self, mock_summarizer):
        user_prompt = "Test prompt for summarizer failure"
        mock_plan = [{"tool_name": "another_mock_tool", "args": ("arg1",), "kwargs": {}}]
        mock_results = ["Another mock result"]
        technical_summary = "\n\nHere's a summary of what I did:\n- Ran 'another_mock_tool'..."

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_summarizer.return_value = None

        with patch.object(self.orchestrator, '_generate_execution_summary', return_value=technical_summary) as mock_gen_exec:
            success, response = await self.orchestrator.process_prompt(user_prompt)

            self.assertTrue(success)
            self.assertTrue(technical_summary in response)
            self.assertTrue(response.startswith("Successfully completed the task."))
            mock_summarizer.assert_called_once()
            mock_gen_exec.assert_called_once_with(mock_plan, mock_results)

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_no_plan_created(self, mock_summarizer):
        user_prompt = "A very complex prompt leading to no plan"
        self.mock_planner_agent.create_plan_with_llm.return_value = []

        # We need to verify that _generate_execution_summary is called and used
        expected_tech_summary = "::TechSummary::"
        with patch.object(self.orchestrator, '_generate_execution_summary', return_value=expected_tech_summary):
             success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        expected_response_part = self.mock_llm_provider.invoke_ollama_model_async.return_value
        self.assertEqual(response, expected_response_part + expected_tech_summary)
        mock_summarizer.assert_not_called()

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_no_plan_created_error_rephrased_succeeds(self, mock_rephraser):
        user_prompt = "Goal leading to no plan"
        technical_error_msg = "Could not create a plan for the given prompt."
        rephrased_error = "I couldn't figure out a plan for that, sorry!"
        technical_summary = "::Technical Summary No Plan::"

        self.mock_planner_agent.create_plan_with_llm.return_value = []
        mock_rephraser.return_value = rephrased_error

        with patch.object(self.orchestrator, '_generate_execution_summary', return_value=technical_summary) as mock_gen_exec:
            success, response = await self.orchestrator.process_prompt(user_prompt)

            self.assertFalse(success)
            self.assertEqual(response, rephrased_error + technical_summary)
            mock_rephraser.assert_called_once_with(
                technical_error_message=technical_error_msg,
                original_user_query=user_prompt,
                llm_provider=self.mock_llm_provider
            )
            mock_gen_exec.assert_called_once_with([], [])

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_no_plan_created_error_rephraser_fails(self, mock_rephraser):
        user_prompt = "Goal leading to no plan"
        technical_error_msg = "Could not create a plan for the given prompt."
        technical_summary = "::Technical Summary No Plan Fallback::"

        self.mock_planner_agent.create_plan_with_llm.return_value = []
        mock_rephraser.return_value = None

        with patch.object(self.orchestrator, '_generate_execution_summary', return_value=technical_summary):
            success, response = await self.orchestrator.process_prompt(user_prompt)

            self.assertFalse(success)
            self.assertEqual(response, technical_error_msg + technical_summary)
            mock_rephraser.assert_called_once()

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_plan_execution_fails_error_rephrased_succeeds(self, mock_summarizer, mock_rephraser):
        user_prompt = "Prompt for execution failure"
        mock_plan = [{"tool_name": "failing_tool", "args": (), "kwargs": {}}]
        technical_error_detail = "Tool failed with specific details"
        mock_results = [Exception(technical_error_detail)]
        rephrased_error = "It seems a step in the plan didn't go as expected!"
        technical_summary = "::Technical Execution Summary::"

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_rephraser.return_value = rephrased_error
        mock_summarizer.return_value = None

        with patch.object(self.orchestrator, '_generate_execution_summary', return_value=technical_summary) as mock_gen_exec:
            success, response = await self.orchestrator.process_prompt(user_prompt)

            self.assertFalse(success)
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
            mock_summarizer.assert_called_once()
            mock_gen_exec.assert_called_once_with(mock_plan, mock_results)

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_plan_execution_fails_rephraser_fails(self, mock_summarizer, mock_rephraser):
        user_prompt = "Prompt for execution failure, rephraser fails"
        mock_plan = [{"tool_name": "failing_tool", "args": (), "kwargs": {}}]
        technical_error_detail = "Tool failed badly"
        mock_results = [Exception(technical_error_detail)]
        expected_initial_message_from_rephrase_block = f"An error occurred: Exception: {technical_error_detail}"
        technical_summary = "::Technical Execution Summary Fallback::"

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_rephraser.return_value = None
        mock_summarizer.return_value = None

        with patch.object(self.orchestrator, '_generate_execution_summary', return_value=technical_summary):
            success, response = await self.orchestrator.process_prompt(user_prompt)

            self.assertFalse(success)
            expected_response = expected_initial_message_from_rephrase_block
            if not expected_response.endswith('.'):
                 expected_response += "."
            expected_response += technical_summary

            self.assertEqual(response, expected_response)
            mock_rephraser.assert_called_once()

    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_orchestrator_exception_rephrased_succeeds(self, mock_rephraser):
        user_prompt = "Prompt causing orchestrator error"
        orchestrator_error_msg = "Orchestrator boom"
        rephrased_error = "Something unexpected happened."

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
        mock_rephraser.return_value = None

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, expected_technical_response)
        mock_rephraser.assert_called_once()

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_plan_fails_summarizer_succeeds(self, mock_summarizer):
        user_prompt = "Prompt that causes plan failure"
        mock_plan = [{"tool_name": "failing_tool", "args": (), "kwargs": {}}]
        mock_results = [Exception("Tool failed")]
        expected_conversational_failure_summary = "It seems there was an issue with the 'failing_tool'."

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_summarizer.return_value = expected_conversational_failure_summary

        success, response = await self.orchestrator.process_prompt(user_prompt)

        self.assertFalse(success)
        self.assertEqual(response, expected_conversational_failure_summary)
        mock_summarizer.assert_called_once_with(
            original_user_query=user_prompt,
            executed_plan_steps=mock_plan,
            tool_results=mock_results,
            overall_success=False,
            llm_provider=self.mock_llm_provider
        )

    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_plan_fails_summarizer_fails_too(self, mock_summarizer):
        user_prompt = "Prompt for double failure"
        mock_plan = [{"tool_name": "another_failing_tool", "args": (), "kwargs": {}}]
        mock_results = [RuntimeError("Critical tool error")]
        technical_summary_fallback = "\n\nHere's a summary of what I did:\n- Ran 'another_failing_tool'..."

        self.mock_planner_agent.create_plan_with_llm.return_value = mock_plan
        self.mock_execution_agent.execute_plan.return_value = (mock_plan, mock_results)
        mock_summarizer.return_value = None

        with patch.object(self.orchestrator, '_generate_execution_summary', return_value=technical_summary_fallback) as mock_gen_exec:
            success, response = await self.orchestrator.process_prompt(user_prompt)

            self.assertFalse(success)
            expected_start = self.mock_llm_provider.invoke_ollama_model_async.return_value
            self.assertTrue(response.startswith(expected_start))
            self.assertIn(technical_summary_fallback, response)
            mock_summarizer.assert_called_once()
            mock_gen_exec.assert_called_once()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
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

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=True)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_triggers_hierarchical_on_empty_simple_plan_and_keywords(
        self, mock_rephraser, mock_summarizer, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a new python project for web scraping"

        self.mock_planner_agent.create_plan_with_llm.return_value = []

        mock_project_plan = [
            {"step_id": "1", "type": "informational", "description": "Setup project", "details": {"message": "Setup complete"}}
        ]
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = mock_project_plan

        mock_active_task = MagicMock(spec=ActiveTask)
        mock_active_task.task_id = "hp_task_123"
        self.mock_task_manager.add_task.return_value = mock_active_task

        executed_tool_plan = [{"tool_name": "execute_project_plan", "args": {}, "description": "Execute plan"}]
        execution_results = [{"ran_successfully": True, "overall_status": "success", "step_results": [{"status": "success"}]}]

        self.mock_execution_agent.execute_plan.return_value = (executed_tool_plan, execution_results)

        mock_summarizer.return_value = "I've started your web scraping project!"
        mock_rephraser.return_value = "I encountered an error."

        success, response = await self.orchestrator.process_prompt(user_prompt)

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

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_hierarchical_planner_fails_to_generate_plan(
        self, mock_rephraser, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a very complex AI system"
        self.mock_planner_agent.create_plan_with_llm.return_value = []
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = []

        mock_rephraser.return_value = "I tried, but couldn't break down the complex AI system task."
        expected_tech_summary = "::Technical Summary H-Fail::"

        with patch.object(self.orchestrator, '_generate_execution_summary', return_value=expected_tech_summary):
            success, response = await self.orchestrator.process_prompt(user_prompt)

            self.assertFalse(success)
            expected_response = "I tried, but couldn't break down the complex AI system task."
            if not expected_response.endswith('.'):
                 expected_response += "."
            expected_response += expected_tech_summary

            self.assertEqual(response, expected_response)

            self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
            self.mock_task_manager.add_task.assert_not_called()
            self.mock_execution_agent.execute_plan.assert_not_called()
            mock_rephraser.assert_called_once()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_task_manager_unavailable_for_hierarchical(
        self, mock_rephraser, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop a project without task manager"
        self.orchestrator.task_manager = None

        self.mock_planner_agent.create_plan_with_llm.return_value = []
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = [{"step_id": "1", "type": "informational", "details": {}}]

        mock_rephraser.return_value = "Cannot manage the project as TaskManager is offline."
        expected_tech_summary = "::Technical Summary TM-Fail::"

        with patch.object(self.orchestrator, '_generate_execution_summary', return_value=expected_tech_summary):
            success, response = await self.orchestrator.process_prompt(user_prompt)

            self.assertFalse(success)

            expected_response = "Cannot manage the project as TaskManager is offline."
            if not expected_response.endswith('.'):
                 expected_response += "."
            expected_response += expected_tech_summary

            self.assertEqual(response, expected_response)
            self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
            self.mock_execution_agent.execute_plan.assert_not_called()
            mock_rephraser.assert_called_once()

    @patch('ai_assistant.core.orchestrator.is_debug_mode', return_value=False)
    @patch('ai_assistant.core.orchestrator.log_event')
    @patch('ai_assistant.core.orchestrator.summarize_tool_result_conversationally', new_callable=AsyncMock)
    @patch('ai_assistant.core.orchestrator.rephrase_error_message_conversationally', new_callable=AsyncMock)
    async def test_process_prompt_hierarchical_plan_execution_tool_fails(
        self, mock_rephraser, mock_summarizer, mock_log_event, mock_debug_mode
    ):
        user_prompt = "develop project where execution tool fails"
        self.mock_planner_agent.create_plan_with_llm.return_value = []
        mock_project_plan = [{"step_id": "1", "type": "python_script", "details": {"script_content": "print('hi')"}}]
        self.mock_hierarchical_planner.generate_full_project_plan.return_value = mock_project_plan

        mock_active_task = MagicMock(spec=ActiveTask)
        mock_active_task.task_id = "hp_task_exec_fail"
        self.mock_task_manager.add_task.return_value = mock_active_task

        tool_execution_failure_result = {"overall_status": "failed", "error_message": "Tool execute_project_plan had an internal error."}
        self.mock_execution_agent.execute_plan.return_value = (
            [{"tool_name": "execute_project_plan", "args": {}}],
            [tool_execution_failure_result]
        )

        mock_summarizer.return_value = None
        mock_rephraser.return_value = "The project execution step itself encountered a problem: Tool execute_project_plan had an internal error."

        expected_tech_summary = "::Technical Summary Tool-Fail::"

        with patch.object(self.orchestrator, '_generate_execution_summary', return_value=expected_tech_summary):
            success, response = await self.orchestrator.process_prompt(user_prompt)

            self.assertFalse(success)
            expected_response_part1 = "The project execution step itself encountered a problem: Tool execute_project_plan had an internal error."
            if not expected_response_part1.endswith('.'):
                 expected_response_part1 += "."

            self.assertEqual(response, expected_response_part1 + expected_tech_summary)

            self.mock_hierarchical_planner.generate_full_project_plan.assert_called_once()
            self.mock_task_manager.add_task.assert_called_once()
            self.mock_execution_agent.execute_plan.assert_called_once()
            mock_rephraser.assert_called_once()
            mock_summarizer.assert_called_once()


if __name__ == '__main__': # pragma: no cover
    unittest.main()
