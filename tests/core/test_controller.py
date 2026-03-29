import unittest
from unittest.mock import AsyncMock, patch
import asyncio
from ai_assistant.core.controller import SystemController
from ai_assistant.core.models.state import ExecutionState, ExecutionStatus

class TestSystemController(unittest.IsolatedAsyncioTestCase):
    @patch('ai_assistant.core.controller.invoke_gemini_model_async', new_callable=AsyncMock)
    @patch('ai_assistant.core.router.TaskRouter.determine_mode', new_callable=AsyncMock)
    async def test_process_request_success(self, mock_determine, mock_invoke):
        mock_orchestrator = AsyncMock()
        mock_orchestrator.process_prompt.side_effect = lambda state, **kwargs: self._mutate_success(state)
        controller = SystemController(orchestrator=mock_orchestrator)

        from ai_assistant.core.enums import ExecutionMode
        mock_determine.return_value = ExecutionMode.FAST_REACT
        result_state = await controller.handle_user_request("What is 2+2?")

        self.assertEqual(result_state.current_status, ExecutionStatus.COMPLETED)
        self.assertEqual(result_state.original_user_prompt, "What is 2+2?")
        self.assertEqual(result_state.final_answer, "4")

    @patch('ai_assistant.core.controller.invoke_gemini_model_async', new_callable=AsyncMock)
    @patch('ai_assistant.core.router.TaskRouter.determine_mode', new_callable=AsyncMock)
    async def test_process_request_failure(self, mock_determine, mock_invoke):
        mock_orchestrator = AsyncMock()
        mock_orchestrator.process_prompt.side_effect = lambda state, **kwargs: self._mutate_failure(state)
        controller = SystemController(orchestrator=mock_orchestrator)

        from ai_assistant.core.enums import ExecutionMode
        mock_determine.return_value = ExecutionMode.FAST_REACT
        result_state = await controller.handle_user_request("Trigger error")

        self.assertEqual(result_state.current_status, ExecutionStatus.FAILED)
        self.assertEqual(result_state.errors[0], "Simulation error")

    def _mutate_success(self, state: ExecutionState) -> ExecutionState:
        state.current_status = ExecutionStatus.COMPLETED
        state.final_answer = "4"
        return state

    def _mutate_failure(self, state: ExecutionState) -> ExecutionState:
        state.current_status = ExecutionStatus.FAILED
        state.errors.append("Simulation error")
        return state

if __name__ == '__main__':
    unittest.main()
