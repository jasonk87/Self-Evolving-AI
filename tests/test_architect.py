import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import json
import os

from ai_assistant.core.agency.architect import SystemArchitect
from ai_assistant.core.safety.judge import SafetyVerdict

class TestSystemArchitect(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Patch external dependencies
        self.episodic_patcher = patch('ai_assistant.core.agency.architect.EpisodicMemoryManager')
        self.MockEpisodic = self.episodic_patcher.start()

        self.llm_patcher = patch('ai_assistant.core.agency.architect.invoke_gemini_model_async', new_callable=AsyncMock)
        self.mock_llm = self.llm_patcher.start()

        self.judge_patcher = patch('ai_assistant.core.agency.architect.judge')
        self.mock_judge = self.judge_patcher.start()

        self.create_goal_patcher = patch('ai_assistant.core.agency.architect.create_goal')
        self.mock_create_goal = self.create_goal_patcher.start()

        self.save_goals_patcher = patch('ai_assistant.core.agency.architect.save_current_goals')
        self.mock_save_goals = self.save_goals_patcher.start()

        # Instantiate architect
        self.architect = SystemArchitect()

    async def asyncTearDown(self):
        self.episodic_patcher.stop()
        self.llm_patcher.stop()
        self.judge_patcher.stop()
        self.create_goal_patcher.stop()
        self.save_goals_patcher.stop()

    @patch('builtins.open', new_callable=unittest.mock.mock_open, read_data='[{"level": "ERROR", "message": "Test Error"}]')
    @patch('os.path.exists', return_value=True)
    async def test_scan_logs_finds_issues(self, mock_exists, mock_open):
        # Setup episodic memory mock
        self.architect.episodic_memory.recall_failures = AsyncMock(return_value="Past failure warning")

        issues = await self.architect.scan_logs()

        self.assertTrue(any("Frequent Error" in i for i in issues))
        self.assertTrue(any("Recurring Failures" in i for i in issues))

    async def test_propose_goals(self):
        issues = ["Issue 1"]
        expected_goals = [{"title": "Goal 1", "description": "Desc 1", "priority": "HIGH"}]

        # Mock LLM response
        self.mock_llm.return_value = json.dumps(expected_goals)

        goals = await self.architect.propose_goals(issues)

        self.assertEqual(goals, expected_goals)
        self.mock_llm.assert_called_once()

    async def test_vet_goals(self):
        goals = [
            {"title": "Safe Goal", "description": "Safe"},
            {"title": "Unsafe Goal", "description": "Unsafe"}
        ]

        # Mock judge responses
        def side_effect(action_desc, code=None):
            if "Unsafe" in action_desc:
                return SafetyVerdict(status="BLOCKED", reason="Unsafe")
            return SafetyVerdict(status="APPROVED", reason="Safe")

        self.mock_judge.evaluate_action.side_effect = side_effect

        approved = await self.architect.vet_goals(goals)

        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["title"], "Safe Goal")

    async def test_run_cycle_end_to_end(self):
        # Mock scan_logs
        self.architect.scan_logs = AsyncMock(return_value=["Error 1"])

        # Mock propose_goals
        self.architect.propose_goals = AsyncMock(return_value=[{"title": "Fix 1", "description": "Fix it", "priority": "HIGH"}])

        # Mock vet_goals
        self.architect.vet_goals = AsyncMock(return_value=[{"title": "Fix 1", "description": "Fix it", "priority": "HIGH"}])

        # Mock save success
        self.mock_save_goals.return_value = True

        result = await self.architect.run_cycle()

        self.assertIn("identified 1 new goals", result)
        self.mock_create_goal.assert_called_with(title="Fix 1", description="Fix it", priority="HIGH")
        self.mock_save_goals.assert_called_once()

if __name__ == '__main__':
    unittest.main()
