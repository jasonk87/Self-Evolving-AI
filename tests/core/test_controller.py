import unittest
from unittest.mock import AsyncMock, patch
import asyncio
from ai_assistant.core.controller import SystemController
from ai_assistant.core.models.state import ExecutionState

class TestSystemController(unittest.IsolatedAsyncioTestCase):
    async def test_process_request_success(self):
        mock_orchestrator = AsyncMock()
        mock_orchestrator.process_prompt.side_effect = lambda state, **kwargs: self._mutate_success(state)
        controller = SystemController(orchestrator=mock_orchestrator)

        result_state = await controller.handle_user_request("What is 2+2?")

        self.assertEqual(result_state.current_status, "completed")
        self.assertEqual(result_state.original_user_prompt, "What is 2+2?")
        self.assertEqual(result_state.final_answer, "4")

    async def test_process_request_failure(self):
        mock_orchestrator = AsyncMock()
        mock_orchestrator.process_prompt.side_effect = lambda state, **kwargs: self._mutate_failure(state)
        controller = SystemController(orchestrator=mock_orchestrator)

        result_state = await controller.handle_user_request("Trigger error")

        self.assertEqual(result_state.current_status, "failed")
        self.assertEqual(result_state.errors[0], "Simulation error")

    def _mutate_success(self, state: ExecutionState) -> ExecutionState:
        state.current_status = "completed"
        state.final_answer = "4"
        return state

    def _mutate_failure(self, state: ExecutionState) -> ExecutionState:
        state.current_status = "failed"
        state.errors.append("Simulation error")
        return state

if __name__ == '__main__':
    unittest.main()
