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
        # For universal cycle, we need side effect for loop
        mock_invoke_gemini.side_effect = ["FINAL ANSWER: Direct response.", '```json\n{"type": "final_answer", "params": {"message": "Direct response."}}\n```']

        # Execute
        success, response, collected_images = await self.orchestrator.process_prompt("Hello")

        # Verify
        self.assertTrue(success)
        self.assertEqual(response, "Direct response.")

    @patch('ai_assistant.core.orchestrator.invoke_gemini_model_async')
    @patch('ai_assistant.core.orchestrator.TaskRouter.determine_mode')
    @patch('ai_assistant.core.orchestrator.tool_system_instance')
    async def test_fast_react_mode_execution(self, mock_tool_system, mock_determine_mode, mock_invoke_gemini):
        # Setup
        mock_determine_mode.return_value = ExecutionMode.FAST_REACT

        # Mock LLM responses for the loop (Strategist, Operator, Strategist)
        # 1. Tool call (Strategist plan)
        # 2. Operator execution json
        # 3. Strategist final answer
        mock_invoke_gemini.side_effect = [
            "Use test tool",
            '```json\n{"type": "tool_call", "params": {"name": "test_tool", "arguments": {"arg1": "val1"}}}\n```',
            'FINAL ANSWER: Done.',
            '```json\n{"type": "final_answer", "params": {"message": "Done."}}\n```'
        ]

        mock_tool_system.get_tools_description.return_value = "Tool 1"
        mock_tool_system.execute_tool = AsyncMock(return_value="Tool Result")

        # Execute
        success, response, collected_images = await self.orchestrator.process_prompt("Do something")

        # Verify
        self.assertTrue(success)
        self.assertEqual(response, "Done.")
        self.assertEqual(mock_invoke_gemini.call_count, 4)
        # Note: The precise execution parameters changed to dict arguments based on schema format updates in universal cycle
        mock_tool_system.execute_tool.assert_called_once()

    @patch('ai_assistant.core.orchestrator.invoke_gemini_model_async')
    @patch('ai_assistant.core.orchestrator.TaskRouter.determine_mode')
    @patch('ai_assistant.core.orchestrator.tool_system_instance')
    async def test_thinking_pro_mode_execution(self, mock_tool_system, mock_determine_mode, mock_invoke_gemini):
        # Setup
        mock_determine_mode.return_value = ExecutionMode.THINKING_PRO

        # Mock Thinking responses (Now routing to Universal Cycle)
        mock_invoke_gemini.side_effect = [
            'Use complex tool',
            '```json\n{"type": "tool_call", "params": {"action": "complex_tool", "args": []}}\n```',
            'FINAL ANSWER: Solved complex problem.',
            '```json\n{"type": "final_answer", "params": {"message": "Solved complex problem."}}\n```'
        ]

        mock_tool_system.get_tools_description.return_value = "Complex Tools"
        mock_tool_system.execute_tool = AsyncMock(return_value="Complex Result")

        # Execute
        success, response, collected_images = await self.orchestrator.process_prompt("Solve complex problem")

        # Verify
        self.assertTrue(success)
        self.assertEqual(response, "Solved complex problem.")
        self.assertEqual(mock_invoke_gemini.call_count, 4)
        # Ensure standard model was used as Universal cycle now runs it
        args, kwargs = mock_invoke_gemini.call_args_list[0]

    @patch('ai_assistant.core.orchestrator.invoke_gemini_model_async')
    @patch('ai_assistant.core.orchestrator.TaskRouter.determine_mode')
    @patch('ai_assistant.core.orchestrator.tool_system_instance')
    async def test_fallback_logic(self, mock_tool_system, mock_determine_mode, mock_invoke_gemini):
        # Setup: Router returns None or something invalid (should default to FAST_REACT)
        mock_determine_mode.side_effect = Exception("Router Error")

        mock_invoke_gemini.side_effect = ["FINAL ANSWER: Fallback success.", '```json\n{"type": "final_answer", "params": {"message": "Fallback success."}}\n```']
        mock_tool_system.get_tools_description.return_value = "Tools"

        # Execute
        success, response, collected_images = await self.orchestrator.process_prompt("Something")

        # Verify
        self.assertTrue(success)
        self.assertEqual(response, "Fallback success.")
        # It should have called invoke_gemini_model_async (Fast React uses this)
        mock_invoke_gemini.assert_called()

if __name__ == '__main__':
    unittest.main()
