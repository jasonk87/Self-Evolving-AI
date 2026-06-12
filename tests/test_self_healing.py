import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from ai_assistant.core.orchestrator import DynamicOrchestrator
from ai_assistant.core.models.state import ExecutionState
from ai_assistant.core.reflection import InsightType

class TestOrchestratorSelfHealing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Mock dependencies
        self.mock_planner = MagicMock()
        self.mock_executor = MagicMock()
        self.mock_learning_agent = MagicMock()
        self.mock_action_executor = AsyncMock()
        self.mock_task_manager = MagicMock()

        # Patch _load_quarantine_state and _save_quarantine_state to avoid file IO
        self.load_quarantine_patcher = patch('ai_assistant.core.orchestrator.DynamicOrchestrator._load_quarantine_state')
        self.mock_load_quarantine = self.load_quarantine_patcher.start()

        self.save_quarantine_patcher = patch('ai_assistant.core.orchestrator.DynamicOrchestrator._save_quarantine_state')
        self.mock_save_quarantine = self.save_quarantine_patcher.start()

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
        self.load_quarantine_patcher.stop()
        self.save_quarantine_patcher.stop()
        self.tool_system_patcher.stop()
        self.gemini_patcher.stop()
        self.episodic_patcher.stop()

    async def test_circuit_breaker_quarantine_activation(self):
        """Test that repeated tool failures activate circuit breaker and report insight."""
        # Setup mock learning agent
        self.mock_learning_agent.add_insight = MagicMock()

        # Clear blocked tools and failure counts
        self.orchestrator.blocked_tools = {}
        self.orchestrator.failure_counts = {}

        error = ValueError("Simulation failure")

        # 1. First failure registration (threshold is 2)
        res1 = self.orchestrator._register_tool_failure("test_tool", error, threshold=2)
        self.assertEqual(res1["count"], 1)
        self.assertFalse(res1["activated"])
        self.assertNotIn("test_tool", self.orchestrator.blocked_tools)
        self.mock_learning_agent.add_insight.assert_not_called()

        # 2. Second failure registration (reaches threshold)
        res2 = self.orchestrator._register_tool_failure("test_tool", error, threshold=2)
        self.assertEqual(res2["count"], 2)
        self.assertTrue(res2["activated"])
        self.assertIn("test_tool", self.orchestrator.blocked_tools)
        self.mock_learning_agent.add_insight.assert_called_once()
        
        insight = self.mock_learning_agent.add_insight.call_args[0][0]
        self.assertEqual(insight.related_tool_name, "test_tool")
        self.assertEqual(insight.type, InsightType.TOOL_BUG_SUSPECTED)

    async def test_circuit_breaker_blocks_execution(self):
        """Test that blocked tool execution is prevented and recorded as circuit breaker message."""
        # Block the tool manually
        import time
        self.orchestrator.blocked_tools = {
            "quarantined_tool": {
                "signature": "quarantined_tool|ValueError|Simulation failure",
                "count": 2,
                "reason": "Repeated failure",
                "timestamp": time.time(),
                "context_data": {}
            }
        }
        self.orchestrator.failure_counts = {}

        # Setup LLM response to call the blocked tool
        tool_call_json = '{"type": "tool_call", "name": "quarantined_tool", "thought": "Call quarantined tool", "params": {}}'
        self.mock_gemini.side_effect = [
            f"```json\n{tool_call_json}\n```",
            '{"type": "final_answer", "params": {"message": "done"}}'
        ]

        self.mock_tool_system.get_tools_description.return_value = "Tools list"

        state = ExecutionState(original_user_prompt="Run blocked tool")

        # Run process_prompt
        # Patch asyncio.sleep to avoid delays
        with patch('asyncio.sleep', new_callable=AsyncMock):
            state = await self.orchestrator.process_prompt(state)

        # Assertions
        # execute_tool should NOT be called at all
        self.mock_tool_system.execute_tool.assert_not_called()

        # The error or tool failure state should indicate circuit breaker active
        self.assertTrue(any("Circuit breaker" in err for err in state.errors))

    async def test_retry_mechanism_on_tool_failure(self):
        """Test that orchestrator retries tool execution on transient failure, succeeding on retry."""
        self.orchestrator.blocked_tools = {}
        self.orchestrator.failure_counts = {}

        # Mock execute_tool to fail once, then succeed
        fail_response = {"success": False, "error_message": "Transient error"}
        success_response = {"success": True, "result": "Success Result"}
        self.mock_tool_system.execute_tool = AsyncMock(side_effect=[
            fail_response,
            success_response
        ])
        
        self.mock_tool_system.get_tools_description.return_value = "Tools list"

        # LLM response: call tool, then final answer
        tool_call_json = '{"type": "tool_call", "name": "transient_tool", "thought": "Call transient tool", "params": {}}'
        self.mock_gemini.side_effect = [
            f"```json\n{tool_call_json}\n```",
            '{"type": "final_answer", "params": {"message": "done"}}'
        ]

        state = ExecutionState(original_user_prompt="Run transient tool")

        with patch('asyncio.sleep', new_callable=AsyncMock):
            state = await self.orchestrator.process_prompt(state)

        # Assertions
        # execute_tool should be called twice (first failure, second success)
        self.assertEqual(self.mock_tool_system.execute_tool.call_count, 2)
        self.assertTrue(state.tool_results[0]["success"])
        self.assertEqual(state.tool_results[0]["result"], "Success Result")

    async def test_unblock_tool(self):
        """Test that unblock_tool removes a tool from blocked_tools."""
        self.orchestrator.blocked_tools = {
            "test_tool": {
                "signature": "signature",
                "count": 2,
                "reason": "reason",
                "timestamp": 123.0,
                "context_data": {}
            }
        }
        self.assertIn("test_tool", self.orchestrator.blocked_tools)
        
        result = self.orchestrator.unblock_tool("test_tool")
        self.assertTrue(result)
        self.assertNotIn("test_tool", self.orchestrator.blocked_tools)

    async def test_tool_signature_validation_raises_error(self):
        """Test that execute_tool raises ToolValidationError when arguments mismatch signature."""
        from ai_assistant.tools.tool_system import tool_system_instance, ToolValidationError
        
        # view_function_code requires: module_path, function_name
        # Try calling with unexpected keyword argument
        with self.assertRaises(ToolValidationError) as context:
            await tool_system_instance.execute_tool(
                name="view_function_code",
                kwargs={"module_path": "foo", "function_name": "bar", "invalid_param": "baz"}
            )
        self.assertIn("Parameter signature mismatch", str(context.exception))

        # Try calling with missing required arguments
        with self.assertRaises(ToolValidationError) as context2:
            await tool_system_instance.execute_tool(
                name="view_function_code",
                kwargs={"module_path": "foo"}
            )
        self.assertIn("Parameter signature mismatch", str(context2.exception))

    async def test_validation_error_bypasses_quarantine(self):
        """Test that ToolValidationError does not increment failure count or trigger quarantine."""
        from ai_assistant.tools.tool_system import ToolValidationError
        self.mock_learning_agent.add_insight = MagicMock()

        self.orchestrator.blocked_tools = {}
        self.orchestrator.failure_counts = {}

        error = ToolValidationError("Validation failure")

        # 1. Register validation failure
        res = self.orchestrator._register_tool_failure("test_tool", error, threshold=2)
        self.assertEqual(res["count"], 0)
        self.assertFalse(res["activated"])
        self.assertNotIn("test_tool", self.orchestrator.blocked_tools)
        self.mock_learning_agent.add_insight.assert_not_called()

    async def test_react_loop_stops_on_validation_error(self):
        """Test that ReAct cycle immediately fails and stops retrying on ToolValidationError."""
        from ai_assistant.tools.tool_system import ToolValidationError
        self.orchestrator.blocked_tools = {}
        self.orchestrator.failure_counts = {}

        # Mock execute_tool to raise ToolValidationError
        self.mock_tool_system.execute_tool = AsyncMock(side_effect=ToolValidationError("Incorrect arguments"))
        self.mock_tool_system.get_tools_description.return_value = "Tools list"

        tool_call_json = '{"type": "tool_call", "name": "validation_tool", "thought": "Call validation tool", "params": {}}'
        self.mock_gemini.side_effect = [
            f"```json\n{tool_call_json}\n```",
            '{"type": "final_answer", "params": {"message": "done"}}'
        ]

        state = ExecutionState(original_user_prompt="Run validation tool")

        with patch('asyncio.sleep', new_callable=AsyncMock):
            state = await self.orchestrator.process_prompt(state)

        # execute_tool should only be called once, not retried (max_retries is 2)
        self.assertEqual(self.mock_tool_system.execute_tool.call_count, 1)
        self.assertFalse(state.tool_results[0]["success"])
        self.assertIn("Incorrect arguments", state.tool_results[0]["error_message"])

if __name__ == '__main__':
    unittest.main()
