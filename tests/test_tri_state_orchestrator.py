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
        mock_invoke_gemini.side_effect = ['```json\n{"type": "final_answer", "params": {"message": "Direct response."}}\n```']

        # Execute
        from ai_assistant.core.models.state import ExecutionState
        state = ExecutionState(original_user_prompt="Hello", context_limits={"max_tokens": 100000})
        state = await self.orchestrator.process_prompt(state=state)

        # Verify
        success = state.current_status == "completed"
        response = state.tool_results[-1].get("result") if state.tool_results else ""

        self.assertTrue(success)
        self.assertEqual(response, "Direct response.")

    @patch('ai_assistant.core.orchestrator.invoke_gemini_model_async')
    @patch('ai_assistant.core.orchestrator.TaskRouter.determine_mode')
    @patch('ai_assistant.core.orchestrator.tool_system_instance')
    async def test_fast_react_mode_execution(self, mock_tool_system, mock_determine_mode, mock_invoke_gemini):
        # Setup
        mock_determine_mode.return_value = ExecutionMode.FAST_REACT

        mock_invoke_gemini.side_effect = [
            '```json\n{"type": "tool_call", "name": "test_tool", "params": {"arg1": "val1"}}\n```',
            '```json\n{"type": "final_answer", "params": {"message": "Done."}}\n```'
        ]

        mock_tool_system.get_tools_description.return_value = "Tool 1"
        mock_tool_system.execute_tool = AsyncMock(return_value={"success": True, "result": "Tool Result"})

        # Execute
        from ai_assistant.core.models.state import ExecutionState
        state = ExecutionState(original_user_prompt="Do something", context_limits={"max_tokens": 100000})
        state = await self.orchestrator.process_prompt(state=state)

        # Verify
        success = state.current_status == "completed"
        response = state.tool_results[-1].get("result") if state.tool_results else ""

        self.assertTrue(success)
        self.assertEqual(response, "Done.")
        self.assertEqual(mock_invoke_gemini.call_count, 2)
        mock_tool_system.execute_tool.assert_called_once()

    @patch('ai_assistant.core.orchestrator.invoke_gemini_model_async')
    @patch('ai_assistant.core.orchestrator.TaskRouter.determine_mode')
    @patch('ai_assistant.core.orchestrator.tool_system_instance')
    async def test_complex_fast_react_execution(self, mock_tool_system, mock_determine_mode, mock_invoke_gemini):
        # Setup
        mock_determine_mode.return_value = ExecutionMode.FAST_REACT

        mock_invoke_gemini.side_effect = [
            '```json\n{"type": "tool_call", "name": "complex_tool", "params": {"args": []}}\n```',
            '```json\n{"type": "final_answer", "params": {"message": "Solved complex problem."}}\n```'
        ]

        mock_tool_system.get_tools_description.return_value = "Complex Tools"
        mock_tool_system.execute_tool = AsyncMock(return_value={"success": True, "result": "Complex Result"})

        # Execute
        from ai_assistant.core.models.state import ExecutionState
        state = ExecutionState(original_user_prompt="Solve complex problem", context_limits={"max_tokens": 100000})
        state = await self.orchestrator.process_prompt(state=state)

        # Verify
        success = state.current_status == "completed"
        response = state.tool_results[-1].get("result") if state.tool_results else ""

        self.assertTrue(success)
        self.assertEqual(response, "Solved complex problem.")
        self.assertEqual(mock_invoke_gemini.call_count, 2)
        args, kwargs = mock_invoke_gemini.call_args_list[0]

    @patch('ai_assistant.core.orchestrator.invoke_gemini_model_async')
    @patch('ai_assistant.core.orchestrator.TaskRouter.determine_mode')
    @patch('ai_assistant.core.orchestrator.tool_system_instance')
    async def test_fallback_logic(self, mock_tool_system, mock_determine_mode, mock_invoke_gemini):
        # Setup: Router returns None or something invalid (should default to FAST_REACT)
        mock_determine_mode.side_effect = Exception("Router Error")

        mock_invoke_gemini.side_effect = ['```json\n{"type": "final_answer", "params": {"message": "Fallback success."}}\n```']
        mock_tool_system.get_tools_description.return_value = "Tools"

        # Execute
        from ai_assistant.core.models.state import ExecutionState
        state = ExecutionState(original_user_prompt="Something", context_limits={"max_tokens": 100000})
        state = await self.orchestrator.process_prompt(state=state)

        # Verify
        success = state.current_status == "completed"
        response = state.tool_results[-1].get("result") if state.tool_results else ""

        self.assertTrue(success)
        self.assertEqual(response, "Fallback success.")
        # It should have called invoke_gemini_model_async (Fast React uses this)
        mock_invoke_gemini.assert_called()

if __name__ == '__main__':
    unittest.main()
