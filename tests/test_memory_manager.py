import unittest
import os
import shutil
import tempfile
from unittest.mock import patch
from ai_assistant.core.memory_manager import MemoryManager

class TestMemoryManager(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for test data
        self.test_dir = tempfile.mkdtemp()
        self.facts_file = os.path.join(self.test_dir, "learned_facts.json")
        self.insights_file = os.path.join(self.test_dir, "actionable_insights.json")

        # Patch the file paths in persistent_memory.
        # We need to patch where they are used.
        # Since `persistent_memory` defines `LEARNED_FACTS_FILEPATH` globally, we might need to patch the functions.
        # The `MemoryManager` imports `load_learned_facts` and `save_learned_facts`.
        # We can patch those imported functions in `ai_assistant.core.memory_manager`.

        self.manager = MemoryManager()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_add_and_get_fact(self):
        # We mock the load/save functions to use our temp file
        with patch('ai_assistant.memory.persistent_memory.LEARNED_FACTS_FILEPATH', self.facts_file):
             # Since the default arg is evaluated at definition time, we might need to pass the path explicitly
             # or patch the functions if they don't accept path injection easily from MemoryManager (which calls them without args).
             # MemoryManager calls `load_learned_facts()`. `load_learned_facts` uses default arg.
             # So patching the default arg variable in the module might not work if it's already bound?
             # Actually, `load_learned_facts` is imported into `memory_manager`.
             # The best way is to patch `ai_assistant.core.memory_manager.load_learned_facts` and `save_learned_facts`?
             # No, because `MemoryManager` calls them.
             # Wait, `load_learned_facts` in `persistent_memory.py` is defined as:
             # def load_learned_facts(filepath: str = LEARNED_FACTS_FILEPATH) -> ...
             # So if we patch `ai_assistant.memory.persistent_memory.LEARNED_FACTS_FILEPATH`, does it affect the default arg?
             # No, default args are evaluated at definition time.

             # We should patch the functions `load_learned_facts` and `save_learned_facts` in `ai_assistant.core.memory_manager`.
             # But we want them to actually work, just with a different file.

             # Better approach: Monkey patch the functions in `ai_assistant.memory.persistent_memory` to use our file,
             # OR ensure `MemoryManager` can pass the path.
             # `MemoryManager` methods are hardcoded to call `load_learned_facts()`.

             # Let's use `side_effect` to redirect the call.

             from ai_assistant.memory.persistent_memory import load_learned_facts as real_load, save_learned_facts as real_save

             def mock_load_facts(filepath=None):
                 return real_load(filepath=self.facts_file)

             def mock_save_facts(facts, filepath=None):
                 return real_save(facts, filepath=self.facts_file)

             with patch('ai_assistant.core.memory_manager.load_learned_facts', side_effect=mock_load_facts) as mock_load, \
                  patch('ai_assistant.core.memory_manager.save_learned_facts', side_effect=mock_save_facts) as mock_save:

                fact = self.manager.add_fact("The sky is blue.")
                self.assertIsNotNone(fact)
                self.assertEqual(fact["text"], "The sky is blue.")

                # Check file existence
                self.assertTrue(os.path.exists(self.facts_file))

                facts = self.manager.get_all_facts()
                self.assertEqual(len(facts), 1)
                self.assertEqual(facts[0]["text"], "The sky is blue.")

    def test_update_fact(self):
        from ai_assistant.memory.persistent_memory import load_learned_facts as real_load, save_learned_facts as real_save
        def mock_load_facts(filepath=None): return real_load(filepath=self.facts_file)
        def mock_save_facts(facts, filepath=None): return real_save(facts, filepath=self.facts_file)

        with patch('ai_assistant.core.memory_manager.load_learned_facts', side_effect=mock_load_facts), \
             patch('ai_assistant.core.memory_manager.save_learned_facts', side_effect=mock_save_facts):

            fact = self.manager.add_fact("The sky is green.")
            fact_id = fact["fact_id"]

            updated = self.manager.update_fact(fact_id, "The sky is blue.")
            self.assertEqual(updated["text"], "The sky is blue.")

            facts = self.manager.get_all_facts()
            self.assertEqual(facts[0]["text"], "The sky is blue.")

    def test_delete_fact(self):
        from ai_assistant.memory.persistent_memory import load_learned_facts as real_load, save_learned_facts as real_save
        def mock_load_facts(filepath=None): return real_load(filepath=self.facts_file)
        def mock_save_facts(facts, filepath=None): return real_save(facts, filepath=self.facts_file)

        with patch('ai_assistant.core.memory_manager.load_learned_facts', side_effect=mock_load_facts), \
             patch('ai_assistant.core.memory_manager.save_learned_facts', side_effect=mock_save_facts):

            fact = self.manager.add_fact("Temporary fact.")
            fact_id = fact["fact_id"]

            result = self.manager.delete_fact(fact_id)
            self.assertTrue(result)

            facts = self.manager.get_all_facts()
            self.assertEqual(len(facts), 0)

if __name__ == '__main__':
    unittest.main()
