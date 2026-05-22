import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
from ai_assistant.planning.plan_simulator import PlanSimulator

class TestPlanSimulator(unittest.TestCase):
    def setUp(self):
        self.mock_provider = MagicMock()
        self.mock_provider.generate_response = AsyncMock()
        self.simulator = PlanSimulator(initial_files=["main.py", "utils.py"], llm_provider=self.mock_provider)

    def test_simulation_catches_deletion_conflict(self):
        mock_invoke = self.mock_provider.generate_response
        # Setup mock to simulate a sequence of events
        # Step 1: Delete main.py
        # Step 2: Edit main.py (Should fail)

        # We need to return different JSONs for sequential calls
        async def side_effect(prompt, **kwargs):
            if "Delete main.py" in prompt:
                return """
                {
                    "files_added": [],
                    "files_removed": ["main.py"],
                    "files_modified": [],
                    "risk_level": "NONE",
                    "predicted_outcome": "File main.py deleted."
                }
                """
            elif "Edit main.py" in prompt:
                # The prompt should show that main.py is NOT in the current file list
                if "main.py" in prompt and "Current Virtual File System State" in prompt:
                    # Check if main.py is ABSENT from the prompt's file list section
                    # The simulator updates state between steps.
                    pass

                return """
                {
                    "files_added": [],
                    "files_removed": [],
                    "files_modified": [],
                    "risk_level": "HIGH",
                    "risk_reason": "Attempting to edit 'main.py' which does not exist (deleted in previous step).",
                    "predicted_outcome": "Edit failed."
                }
                """
            return "{}"

        mock_invoke.side_effect = side_effect

        plan = [
            {
                "step_id": "1.1",
                "type": "file_operation",
                "description": "Delete main.py",
                "details": {"op": "delete", "file": "main.py"}
            },
            {
                "step_id": "1.2",
                "type": "file_operation",
                "description": "Edit main.py to add imports",
                "details": {"op": "edit", "file": "main.py"}
            }
        ]

        result = asyncio.run(self.simulator.simulate_plan(plan))

        self.assertFalse(result["success"])
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["issues"][0]["step_id"], "1.2")
        self.assertEqual(result["issues"][0]["risk_level"], "HIGH")
        self.assertIn("does not exist", result["issues"][0]["reason"])

    def test_simulation_tracks_file_creation(self):
        mock_invoke = self.mock_provider.generate_response
        # Step 1: Create new_file.py
        # Step 2: Edit new_file.py (Should succeed)

        async def side_effect(prompt, **kwargs):
            if "Create new_file.py" in prompt:
                return """
                {
                    "files_added": ["new_file.py"],
                    "files_removed": [],
                    "files_modified": [],
                    "risk_level": "NONE",
                    "predicted_outcome": "File created."
                }
                """
            elif "Edit new_file.py" in prompt:
                return """
                {
                    "files_added": [],
                    "files_removed": [],
                    "files_modified": ["new_file.py"],
                    "risk_level": "NONE",
                    "predicted_outcome": "File edited."
                }
                """
            return "{}"

        mock_invoke.side_effect = side_effect

        plan = [
            {
                "step_id": "1.1",
                "description": "Create new_file.py",
                "details": {}
            },
            {
                "step_id": "1.2",
                "description": "Edit new_file.py",
                "details": {}
            }
        ]

        result = asyncio.run(self.simulator.simulate_plan(plan))

        self.assertTrue(result["success"])
        self.assertEqual(len(result["issues"]), 0)
        # Verify virtual state updated (internally)
        self.assertIn("new_file.py", self.simulator.virtual_files)

if __name__ == '__main__':
    unittest.main()
