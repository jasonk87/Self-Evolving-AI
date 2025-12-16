import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
from ai_assistant.core.orchestrator import DynamicOrchestrator
from ai_assistant.core.enums import ExecutionMode
from ai_assistant.config import DEFAULT_EXECUTION_MODE

class TestTriStateOrchestrator(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.planner = MagicMock()
        self.executor = MagicMock()
        self.learning_agent = MagicMock()
        self.action_executor = MagicMock()
        self.task_manager = MagicMock()
        self.notification_manager = MagicMock()
        self.hierarchical_planner = MagicMock()
        self.memory_manager = MagicMock()

        # Mock memory manager retrieval
        self.memory_manager.retrieve_relevant_context = AsyncMock(return_value=[])

        self.orchestrator = DynamicOrchestrator(
            self.planner, self.executor, self.learning_agent,
            self.action_executor, self.task_manager,
            self.notification_manager, self.hierarchical_planner,
            self.memory_manager
        )

    @patch('ai_assistant.core.orchestrator.invoke_gemini_model_async')
    @patch('ai_assistant.core.orchestrator.TaskRouter.determine_mode')
    async def test_direct_mode_routing(self, mock_determine_mode, mock_invoke_gemini):
        # Setup
        mock_determine_mode.return_value = ExecutionMode.DIRECT
        mock_invoke_gemini.return_value = "Direct response."

        # Execute
        success, response = await self.orchestrator.process_prompt("Hello")

        # Verify
        self.assertTrue(success)
        self.assertEqual(response, "Direct response.")
        mock_determine_mode.assert_called_once()
        mock_invoke_gemini.assert_called_once()
        # Ensure fast model was used
        args, kwargs = mock_invoke_gemini.call_args
        self.assertEqual(kwargs['model_name'], "gemini-2.0-flash")

    @patch('ai_assistant.core.orchestrator.invoke_gemini_model_async')
    @patch('ai_assistant.core.orchestrator.TaskRouter.determine_mode')
    @patch('ai_assistant.core.orchestrator.tool_system_instance')
    async def test_fast_react_mode_execution(self, mock_tool_system, mock_determine_mode, mock_invoke_gemini):
        # Setup
        mock_determine_mode.return_value = ExecutionMode.FAST_REACT

        # Mock LLM responses for the loop
        # 1. Tool call
        # 2. Final answer
        mock_invoke_gemini.side_effect = [
            '```json\n{"action": "test_tool", "args": ["arg1"]}\n```',
            'FINAL ANSWER: Done.'
        ]

        mock_tool_system.get_tools_description.return_value = "Tool 1"
        mock_tool_system.execute_tool = AsyncMock(return_value="Tool Result")

        # Execute
        success, response = await self.orchestrator.process_prompt("Do something")

        # Verify
        self.assertTrue(success)
        self.assertEqual(response, "Done.")
        self.assertEqual(mock_invoke_gemini.call_count, 2)
        mock_tool_system.execute_tool.assert_called_once_with(
            "test_tool", args=("arg1",), kwargs={},
            task_manager=self.task_manager,
            notification_manager=self.notification_manager,
            action_executor=self.action_executor
        )

    @patch('ai_assistant.core.orchestrator.invoke_parallel_thinking')
    @patch('ai_assistant.core.orchestrator.TaskRouter.determine_mode')
    @patch('ai_assistant.core.orchestrator.tool_system_instance')
    async def test_thinking_pro_mode_execution(self, mock_tool_system, mock_determine_mode, mock_parallel_thinking):
        # Setup
        mock_determine_mode.return_value = ExecutionMode.THINKING_PRO

        # Mock Parallel Thinking responses
        mock_parallel_thinking.side_effect = [
            '```json\n{"action": "complex_tool", "args": []}\n```',
            'FINAL ANSWER: Solved complex problem.'
        ]

        mock_tool_system.get_tools_description.return_value = "Complex Tools"
        mock_tool_system.execute_tool = AsyncMock(return_value="Complex Result")

        # Execute
        success, response = await self.orchestrator.process_prompt("Solve complex problem")

        # Verify
        self.assertTrue(success)
        self.assertEqual(response, "Solved complex problem.")
        self.assertEqual(mock_parallel_thinking.call_count, 2)
        # Ensure parallel thinking used correct model and branches
        args, kwargs = mock_parallel_thinking.call_args_list[0]
        self.assertEqual(kwargs['model_name'], "gemini-2.0-flash-exp")
        self.assertEqual(kwargs['num_branches'], 3)

    @patch('ai_assistant.core.orchestrator.invoke_gemini_model_async')
    @patch('ai_assistant.core.orchestrator.TaskRouter.determine_mode')
    @patch('ai_assistant.core.orchestrator.tool_system_instance')
    async def test_fallback_logic(self, mock_tool_system, mock_determine_mode, mock_invoke_gemini):
        # Setup: Router returns None or something invalid (should default to FAST_REACT)
        mock_determine_mode.side_effect = Exception("Router Error")

        mock_invoke_gemini.return_value = "FINAL ANSWER: Fallback success."
        mock_tool_system.get_tools_description.return_value = "Tools"

        # Execute
        success, response = await self.orchestrator.process_prompt("Something")

        # Verify
        self.assertTrue(success)
        self.assertEqual(response, "Fallback success.")
        # It should have called invoke_gemini_model_async (Fast React uses this)
        mock_invoke_gemini.assert_called()

if __name__ == '__main__':
    unittest.main()
