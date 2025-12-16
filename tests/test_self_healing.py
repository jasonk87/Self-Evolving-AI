import unittest
import os
import json
import shutil
import tempfile
import datetime
from unittest.mock import patch, MagicMock, AsyncMock
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.learning.learning import LearningAgent, ActionableInsight, InsightType
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager
from ai_assistant.core import suggestion_manager

class TestSelfHealingLoop(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.insights_file = os.path.join(self.test_dir, "actionable_insights.json")
        self.suggestions_file = os.path.join(self.test_dir, "suggestions.json")

        # Patch suggestion file path
        self.sugg_patcher = patch('ai_assistant.core.suggestion_manager.get_suggestions_file_path', return_value=self.suggestions_file)
        self.sugg_patcher.start()

        self.nm = NotificationManager()
        self.tm = TaskManager(notification_manager=self.nm)

        # Mock ActionExecutor methods to avoid real code execution/LLM calls
        self.mock_executor = MagicMock(spec=ActionExecutor)
        # Setup execute_action to simulate success when staging_mode is True
        # We need to simulate the return value of execute_action(action)

        # But wait, we want to test that LearningAgent CALLS execute_action correctly.
        # And we want to test that ActionExecutor handles staging_mode correctly (partially verified by unit/integration tests).

        # Let's test the flow in LearningAgent first.
        self.agent = LearningAgent(
            insights_filepath=self.insights_file,
            task_manager=self.tm,
            notification_manager=self.nm
        )
        self.agent.action_executor = self.mock_executor
        self.mock_executor.execute_action = AsyncMock(return_value=True)

    def tearDown(self):
        self.sugg_patcher.stop()
        shutil.rmtree(self.test_dir)

    async def async_test_self_healing_trigger(self):
        # Create a BUG insight
        insight = ActionableInsight(
            type=InsightType.TOOL_BUG_SUSPECTED,
            description="Bug in tool X",
            source_reflection_entry_ids=["ref1"],
            related_tool_name="tool_X",
            status="NEW",
            metadata={"module_path": "mod", "function_name": "func"}
        )
        self.agent.insights.append(insight)
        self.agent._save_insights()

        # Trigger self-healing
        count = await self.agent.process_self_healing_insights()

        self.assertEqual(count, 1)
        self.mock_executor.execute_action.assert_called_once()

        # Verify call arguments
        call_args = self.mock_executor.execute_action.call_args[0][0]
        self.assertEqual(call_args["action_type"], "PROPOSE_TOOL_MODIFICATION")
        self.assertTrue(call_args["details"]["staging_mode"])

        # Verify status update (in memory, persistence mocked/handled)
        # Note: execute_action mocked to return True, so status should be SELF_HEALING_PROPOSED
        # Re-load or check object
        self.assertEqual(insight.status, "SELF_HEALING_PROPOSED")

    def test_run_async(self):
        import asyncio
        asyncio.run(self.async_test_self_healing_trigger())

if __name__ == '__main__':
    unittest.main()
