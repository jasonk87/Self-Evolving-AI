import unittest
from unittest.mock import MagicMock, AsyncMock, patch, ANY
import asyncio
from ai_assistant.core.orchestrator import DynamicOrchestrator
from ai_assistant.core.enums import ExecutionMode

class TestOrchestratorSelfHealing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Mock dependencies
        self.mock_planner = MagicMock()
        self.mock_executor = MagicMock()
        self.mock_learning_agent = MagicMock()
        self.mock_action_executor = AsyncMock()
        self.mock_task_manager = MagicMock()

        self.orchestrator = DynamicOrchestrator(
            planner=self.mock_planner,
            executor=self.mock_executor,
            learning_agent=self.mock_learning_agent,
            action_executor=self.mock_action_executor,
            task_manager=self.mock_task_manager
        )

        # Patch dependencies within orchestrator methods
        self.tool_system_patcher = patch('ai_assistant.core.orchestrator.tool_system_instance')
        self.mock_tool_system = self.tool_system_patcher.start()

        self.gemini_patcher = patch('ai_assistant.core.orchestrator.invoke_gemini_model_async', new_callable=AsyncMock)
        self.mock_gemini = self.gemini_patcher.start()

        self.episodic_patcher = patch('ai_assistant.core.orchestrator.EpisodicMemoryManager')
        self.mock_episodic = self.episodic_patcher.start()

        self.mock_episodic.return_value.recall_failures = AsyncMock(return_value=None)
        self.mock_episodic.return_value.record_experience = AsyncMock()

    async def asyncTearDown(self):
        self.tool_system_patcher.stop()
        self.gemini_patcher.stop()
        self.episodic_patcher.stop()

    async def test_self_healing_success(self):
        """Test that orchestrator attempts repair and retries on failure."""

        # 1. Setup Tool System Mock
        # execute_tool raises Exception on first call, returns success on second
        self.mock_tool_system.execute_tool = AsyncMock(side_effect=[Exception("Runtime Error"), "Success Result"])
        self.mock_tool_system.get_tools_description.return_value = "Tools desc"
        # get_tool returns info for a custom tool
        self.mock_tool_system.get_tool.return_value = {
            "type": "custom_discovered",
            "module_path": "test.path",
            "function_name": "broken_tool"
        }

        # 2. Setup Action Executor Mock (Repair succeeds)
        self.mock_action_executor.execute_action.return_value = True

        # 3. Setup LLM Mock (To choose the tool)
        # First call chooses tool, Second call (after loop) gives final answer?
        # Actually loop continues.
        # We need to simulate the loop.
        # Response 1: Call broken_tool
        # Response 2: Final Answer (after successful retry)
        self.mock_gemini.side_effect = [
            '```json {"action": "broken_tool", "args": []}```',
            'FINAL ANSWER: Done'
        ]

        # 4. Run Process
        success, response = await self.orchestrator._execute_react_loop(
            prompt="Fix me",
            context="",
            history=[],
            session_id="sess",
            use_parallel_thinking=False,
            model_name="model"
        )

        # 5. Assertions
        # execute_tool called twice (fail -> retry)
        self.assertEqual(self.mock_tool_system.execute_tool.call_count, 2)

        # Repair attempted
        self.mock_action_executor.execute_action.assert_called_once()
        args, _ = self.mock_action_executor.execute_action.call_args
        self.assertEqual(args[0]['action_type'], "PROPOSE_TOOL_MODIFICATION")
        self.assertEqual(args[0]['details']['tool_name'], "broken_tool")

        # Verify success
        self.assertTrue(success)
        self.assertIn("Done", response)

    async def test_self_healing_fail_retry(self):
        """Test that orchestrator fails gracefully if repair fails."""

        # execute_tool always fails
        self.mock_tool_system.execute_tool = AsyncMock(side_effect=Exception("Persistent Error"))
        self.mock_tool_system.get_tool.return_value = {
            "type": "custom_discovered",
            "module_path": "test.path",
            "function_name": "broken_tool"
        }

        # Repair fails (e.g. LLM couldn't generate code)
        self.mock_action_executor.execute_action.return_value = False

        self.mock_gemini.side_effect = [
            '```json {"action": "broken_tool", "args": []}```',
            'FINAL ANSWER: Failed' # Should loop back or exit
        ]

        # Use a short loop for testing
        with patch('ai_assistant.core.orchestrator.MAX_REACT_STEPS', 1):
            success, response = await self.orchestrator._execute_react_loop("Fix me", "", [], "sess", False, "model")

        # execute_tool called once (fail -> repair fail -> stop attempts for this step)
        # Wait, if repair fails, it breaks the retry loop immediately.
        self.assertEqual(self.mock_tool_system.execute_tool.call_count, 1)
        self.mock_action_executor.execute_action.assert_called_once()

        # Result should capture error
        # We can't easily check internal state variable 'result_str' but the step history should contain it.
        # But 'response' comes from LLM on next turn? Or if MAX_STEPS reached.
        # If MAX_STEPS=1, and tool fails, loop ends.

    async def test_no_repair_for_system_tools(self):
        """Test that system tools are not auto-repaired."""

        self.mock_tool_system.execute_tool = AsyncMock(side_effect=Exception("System Error"))
        self.mock_tool_system.get_tool.return_value = {
            "type": "system_internal", # Not custom
            "module_path": "ai_assistant.core",
            "function_name": "sys_tool"
        }

        self.mock_gemini.side_effect = ['```json {"action": "sys_tool", "args": []}```', 'FINAL ANSWER: Done']

        with patch('ai_assistant.core.orchestrator.MAX_REACT_STEPS', 1):
             await self.orchestrator._execute_react_loop("Run", "", [], "sess", False, "model")

        self.mock_action_executor.execute_action.assert_not_called()
        self.assertEqual(self.mock_tool_system.execute_tool.call_count, 1)

if __name__ == '__main__':
    unittest.main()
