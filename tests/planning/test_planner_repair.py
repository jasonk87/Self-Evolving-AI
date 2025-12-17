import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import json
from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner
from ai_assistant.llm_interface.ollama_client import OllamaProvider

class TestPlannerRepair(unittest.TestCase):
    def setUp(self):
        self.mock_provider = MagicMock(spec=OllamaProvider)
        self.mock_provider.invoke_ollama_model_async = AsyncMock()

        # Patch config to return dummy model names
        self.config_patcher = patch('ai_assistant.config.get_model_for_task', return_value="mock_model")
        self.config_patcher.start()

        self.planner = HierarchicalPlanner(llm_provider=self.mock_provider)

    def tearDown(self):
        self.config_patcher.stop()

    @patch('ai_assistant.planning.hierarchical_planner.PlanSimulator')
    @patch('ai_assistant.planning.hierarchical_planner.os.walk')
    def test_plan_repair_loop_succeeds(self, mock_walk, MockSimulatorClass):
        # 1. Setup Mock Simulator
        mock_simulator_instance = MockSimulatorClass.return_value

        # Scenario:
        # Attempt 0: Simulator finds an issue (Risk: HIGH)
        # Attempt 1: Simulator passes (Risk: NONE)

        # We need to simulate the async simulate_plan method
        async def mock_simulate_plan(plan):
            # Check if this is the original bad plan or the fixed plan
            # We can identify by checking the plan content or just use side_effect counter
            if not hasattr(mock_simulate_plan, "call_count"):
                mock_simulate_plan.call_count = 0

            mock_simulate_plan.call_count += 1

            if mock_simulate_plan.call_count == 1:
                return {
                    "success": False,
                    "issues": [{
                        "step_id": "1.1",
                        "risk_level": "HIGH",
                        "reason": "Deleting file that does not exist."
                    }]
                }
            else:
                return {
                    "success": True,
                    "issues": []
                }

        mock_simulator_instance.simulate_plan = AsyncMock(side_effect=mock_simulate_plan)

        # 2. Setup Mock LLM for Plan Generation (first 3 calls) and Repair (4th call)
        # We need the planner to generate a "Bad Plan" initially, then a "Fixed Plan" during repair.
        # calls:
        # 1. generate_high_level_outline -> ["Phase 1"]
        # 2. generate_detailed_tasks -> ["Task 1"]
        # 3. step_elaboration -> { "type": "python_script", "details": ... } (THE BAD PLAN)
        # 4. _repair_plan_with_llm -> Fixed Plan JSON

        async def mock_llm_invoke(prompt, **kwargs):
            if "high-level functional components" in prompt:
                return "- Phase 1"
            elif "specific, actionable sub-tasks" in prompt:
                return "- Task 1"
            elif "convert the above detailed task" in prompt:
                return json.dumps({
                    "type": "python_script",
                    "details": {"script_content_prompt": "Bad script"}
                })
            elif "REPAIR the plan" in prompt:
                # Return the "Fixed" plan structure
                return json.dumps([
                    {
                        "step_id": "1.1",
                        "description": "Task 1 (Fixed)",
                        "type": "python_script",
                        "details": {"script_content_prompt": "Good script"}
                    }
                ])
            return ""

        self.mock_provider.invoke_ollama_model_async.side_effect = mock_llm_invoke

        # 3. Run the Planner
        final_plan = asyncio.run(self.planner.generate_full_project_plan("Test Goal"))

        # 4. Assertions
        # Should have called simulator twice (fail then pass)
        self.assertEqual(mock_simulator_instance.simulate_plan.call_count, 2)

        # Should have called LLM for repair
        # We can check if "REPAIR the plan" was in one of the calls
        repair_called = False
        for call_args in self.mock_provider.invoke_ollama_model_async.call_args_list:
            if "REPAIR the plan" in call_args[0][0]:
                repair_called = True
                break
        self.assertTrue(repair_called, "LLM should have been called for plan repair.")

        # The final plan should be the one returned by the repair (Task 1 Fixed)
        self.assertEqual(len(final_plan), 1)
        self.assertEqual(final_plan[0]["description"], "Task 1 (Fixed)")

    @patch('ai_assistant.planning.hierarchical_planner.PlanSimulator')
    @patch('ai_assistant.planning.hierarchical_planner.os.walk')
    def test_plan_repair_fails_after_max_retries(self, mock_walk, MockSimulatorClass):
        mock_simulator_instance = MockSimulatorClass.return_value

        # Always fail
        mock_simulator_instance.simulate_plan = AsyncMock(return_value={
            "success": False,
            "issues": [{"step_id": "1.1", "risk_level": "HIGH", "reason": "Persistent Error"}]
        })

        # LLM always returns a "Fixed" plan (but simulator rejects it)
        async def mock_llm_invoke(prompt, **kwargs):
            if "REPAIR the plan" in prompt:
                return json.dumps([{"step_id": "1.1", "description": "Still Bad", "type": "python_script", "details": {}}])
            # ... standard generation mocks ...
            if "high-level functional components" in prompt: return "- Phase 1"
            if "specific, actionable sub-tasks" in prompt: return "- Task 1"
            if "convert the above detailed task" in prompt: return json.dumps({"type": "python_script", "details": {}})
            return ""

        self.mock_provider.invoke_ollama_model_async.side_effect = mock_llm_invoke

        final_plan = asyncio.run(self.planner.generate_full_project_plan("Test Goal"))

        # Should have tried 4 times (0 + 3 retries)
        self.assertEqual(mock_simulator_instance.simulate_plan.call_count, 4)

        # Final plan should have the warning step inserted at the beginning
        self.assertEqual(final_plan[0]["step_id"], "0.0")
        self.assertIn("Shadow Mode Simulation Warning", final_plan[0]["description"])

if __name__ == '__main__':
    unittest.main()
