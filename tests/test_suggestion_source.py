import unittest
import os
import json
import shutil
import tempfile
from unittest.mock import patch, MagicMock
from ai_assistant.core import suggestion_manager
from ai_assistant.custom_tools import awareness_tools

class TestSuggestionSource(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for test data
        self.test_dir = tempfile.mkdtemp()
        self.suggestions_file = os.path.join(self.test_dir, "suggestions.json")

        # Patch the file path in suggestion_manager
        self.path_patcher = patch('ai_assistant.core.suggestion_manager.get_suggestions_file_path', return_value=self.suggestions_file)
        self.mock_get_path = self.path_patcher.start()

        # Reset internal cache or ensure file is empty
        if os.path.exists(self.suggestions_file):
            os.remove(self.suggestions_file)

    def tearDown(self):
        self.path_patcher.stop()
        shutil.rmtree(self.test_dir)

    def test_suggestion_source_tracking(self):
        # Add suggestion from AI
        sugg_ai = suggestion_manager.add_new_suggestion(
            type="improvement",
            description="AI generated suggestion",
            source="AI"
        )
        self.assertIsNotNone(sugg_ai)
        self.assertEqual(sugg_ai["source"], "AI")

        # Add suggestion from USER
        sugg_user = suggestion_manager.add_new_suggestion(
            type="feature",
            description="User requested feature",
            source="USER"
        )
        self.assertIsNotNone(sugg_user)
        self.assertEqual(sugg_user["source"], "USER")

        # Verify via awareness_tools.list_formatted_suggestions
        # We need to mock list_suggestions in awareness_tools or let it call the real one?
        # awareness_tools imports list_suggestions from suggestion_manager.
        # Since we are running in the same process and patched get_suggestions_file_path globally (well, in the module),
        # real calls should work if imports align.
        # But we need to make sure awareness_tools uses the patched logic.
        # awareness_tools imports: from ai_assistant.core.suggestion_manager import find_suggestion, list_suggestions
        # If we patch the function in suggestion_manager, awareness_tools might still hold a reference to the original?
        # No, because we patched `get_suggestions_file_path` inside `suggestion_manager`, and `list_suggestions` calls `_load_suggestions` which calls `get_suggestions_file_path`.

        formatted_suggestions = awareness_tools.list_formatted_suggestions(status_filter="all")

        # Find our suggestions
        found_ai = next((s for s in formatted_suggestions if s["suggestion_id"] == sugg_ai["suggestion_id"]), None)
        found_user = next((s for s in formatted_suggestions if s["suggestion_id"] == sugg_user["suggestion_id"]), None)

        self.assertIsNotNone(found_ai)
        self.assertEqual(found_ai.get("source"), "AI")

        self.assertIsNotNone(found_user)
        self.assertEqual(found_user.get("source"), "USER")

    def test_suggestion_details_source(self):
        sugg = suggestion_manager.add_new_suggestion(
            type="bugfix",
            description="Fix a bug",
            source="SYSTEM"
        )

        details = awareness_tools.get_item_details_by_id(sugg["suggestion_id"], "suggestion")
        self.assertIsNotNone(details)
        self.assertEqual(details.get("source"), "SYSTEM")

if __name__ == '__main__':
    unittest.main()
