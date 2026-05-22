import unittest
import os
import json
import shutil
import tempfile
import datetime
from unittest.mock import patch, MagicMock
from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner
from ai_assistant.core.llm.gemini_provider import GeminiProvider

class TestMemoryAugmentedPlanning(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for test data
        self.test_dir = tempfile.mkdtemp()
        self.facts_file = os.path.join(self.test_dir, "learned_facts.json")

        # Patch the facts file path
        self.path_patcher = patch('ai_assistant.memory.persistent_memory.LEARNED_FACTS_FILEPATH', self.facts_file)
        self.mock_facts_path = self.path_patcher.start()

        # Patch load_learned_facts in hierarchical_planner module to ensure it picks up the patched path logic
        # OR simply rely on the fact that persistent_memory imports are used.
        # Since hierarchical_planner imports `load_learned_facts` from `persistent_memory`,
        # and `load_learned_facts` uses `LEARNED_FACTS_FILEPATH` as default arg,
        # patching the constant *before* module load is tricky, but patching the function `load_learned_facts` in `hierarchical_planner` namespace is better.

        # However, `load_learned_facts` is defined in `persistent_memory`.
        # Let's write the facts file first.
        self.facts_data = [
            {"fact_id": "1", "text": "User prefers concise Python code.", "category": "user_preference", "created_at": "2023-01-01"},
            {"fact_id": "2", "text": "Project 'SnakeGame' must be command-line only.", "category": "project_context", "created_at": "2023-01-01"},
            {"fact_id": "3", "text": "The sky is blue.", "category": "general_knowledge", "created_at": "2023-01-01"}
        ]
        with open(self.facts_file, 'w') as f:
            json.dump(self.facts_data, f)

    def tearDown(self):
        self.path_patcher.stop()
        shutil.rmtree(self.test_dir)

    async def async_test_facts_injection(self):
        # Mock LLM provider to capture the prompt
        mock_llm = MagicMock(spec=GeminiProvider)
        captured_prompt = []

        async def mock_invoke(prompt, model_name, **kwargs):
            captured_prompt.append(prompt)
            # Return a valid response to avoid crashes
            return """
            - Step 1
            - Step 2
            """

        mock_llm.generate_response = mock_invoke

        # Create planner
        planner = HierarchicalPlanner(llm_provider=mock_llm)

        # We need to ensure `load_learned_facts` reads from our temp file.
        # Since `HierarchicalPlanner` calls `load_learned_facts()`, we need to mock it to return our data
        # because patching the constant might not propagate if the default arg was bound at import time.

        with patch('ai_assistant.planning.hierarchical_planner.load_learned_facts', return_value=self.facts_data):
            # Run generation
            user_goal = "Build a SnakeGame feature."
            await planner.generate_high_level_outline(user_goal)

            # Assertions
            prompt = captured_prompt[0]
            print(f"Captured Prompt:\n{prompt}")

            self.assertIn("Relevant Learned Facts (Context):", prompt)
            self.assertIn("- User prefers concise Python code.", prompt)
            self.assertIn("- Project 'SnakeGame' must be command-line only.", prompt)
            # "The sky is blue" might be excluded if query filtering works well, or included if not.
            # Our query is "Build a SnakeGame feature."
            # "SnakeGame" matches fact 2. "User" (preference) logic might include fact 1.
            # "Sky" is likely excluded.

            # Let's verify that non-relevant facts are likely filtered if the logic implies it,
            # OR if we are returning top 10, check they are there.
            # The logic implemented: "if query_keywords & fact_words or 'prefer' in fact_text or 'always' in fact_text"
            # Fact 1 has "prefer". Fact 2 has "SnakeGame". Fact 3 has "sky", "blue".
            # Query has "Build", "SnakeGame", "feature".

            self.assertIn("- User prefers concise Python code.", prompt) # Should be there due to "prefer"
            self.assertIn("- Project 'SnakeGame' must be command-line only.", prompt) # Should be there due to keyword

            # Fact 3 might be there if list < 10.
            # "elif len(all_facts) < 10: ... append"
            # So yes, it should be there.
            self.assertIn("- The sky is blue.", prompt)

    def test_run_async(self):
        import asyncio
        asyncio.run(self.async_test_facts_injection())

if __name__ == '__main__':
    unittest.main()
