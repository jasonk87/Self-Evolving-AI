import unittest
import os
import json
from unittest.mock import patch, MagicMock

from ai_assistant.tools.tool_system import ToolSystem, DEFAULT_TOOLS_FILE_DIR, DEFAULT_TOOL_REGISTRY_FILE
from ai_assistant.config import get_data_dir # To ensure consistency if used by ToolSystem

# Ensure DEFAULT_TOOLS_FILE_DIR is set up for tests (can be a temporary directory)
# For simplicity, we'll let ToolSystem create its default if it doesn't exist,
# but we'll use a specific test file name to avoid conflict.
TEST_TOOL_REGISTRY_FILE = os.path.join(get_data_dir(), "test_tool_registry.json")

class TestToolSystem(unittest.TestCase):

    def setUp(self):
        # Ensure a clean slate for each test by removing the test registry file if it exists
        if os.path.exists(TEST_TOOL_REGISTRY_FILE):
            os.remove(TEST_TOOL_REGISTRY_FILE)

        # Patch is_debug_mode to False to reduce console output during tests, unless testing debug features
        self.patch_is_debug_mode = patch('ai_assistant.tools.tool_system.is_debug_mode', MagicMock(return_value=False))
        self.mock_is_debug_mode = self.patch_is_debug_mode.start()

        # Initialize ToolSystem with our test-specific registry file
        # This also implicitly tests load_persisted_tools (with a non-existent file) and save_registered_tools (if any tools are auto-registered)
        self.ts = ToolSystem(tool_registry_file=TEST_TOOL_REGISTRY_FILE)

        # Register a dummy tool for testing updates
        self.dummy_tool_name = "dummy_tool_for_test"
        self.ts.register_tool(
            tool_name=self.dummy_tool_name,
            description="Initial description",
            module_path="test.module",
            function_name_in_module="dummy_func",
            tool_type="test_type"
        )
        # Ensure it's saved so update can work on a persisted-like state
        self.ts.save_registered_tools()


    def tearDown(self):
        # Clean up the test registry file
        if os.path.exists(TEST_TOOL_REGISTRY_FILE):
            os.remove(TEST_TOOL_REGISTRY_FILE)
        self.patch_is_debug_mode.stop()

    def test_update_tool_description_success(self):
        new_desc = "Updated description for dummy tool."
        result = self.ts.update_tool_description(self.dummy_tool_name, new_desc)
        self.assertTrue(result, "update_tool_description should return True on success.")

        updated_tool_info = self.ts.get_tool(self.dummy_tool_name)
        self.assertIsNotNone(updated_tool_info)
        self.assertEqual(updated_tool_info['description'], new_desc)

        # Verify persistence by reloading and checking
        ts_reloaded = ToolSystem(tool_registry_file=TEST_TOOL_REGISTRY_FILE)
        reloaded_tool_info = ts_reloaded.get_tool(self.dummy_tool_name)
        self.assertIsNotNone(reloaded_tool_info)
        self.assertEqual(reloaded_tool_info['description'], new_desc)

    def test_update_tool_description_tool_not_found(self):
        result = self.ts.update_tool_description("non_existent_tool", "some description")
        self.assertFalse(result, "update_tool_description should return False if tool not found.")

    def test_update_tool_description_same_description(self):
        initial_desc = self.ts.get_tool(self.dummy_tool_name)['description']
        # Mock save_registered_tools to check if it's called unnecessarily
        with patch.object(self.ts, 'save_registered_tools', wraps=self.ts.save_registered_tools) as mock_save:
            result = self.ts.update_tool_description(self.dummy_tool_name, initial_desc)
            self.assertTrue(result, "update_tool_description should return True even if description is the same.")
            updated_tool_info = self.ts.get_tool(self.dummy_tool_name)
            self.assertEqual(updated_tool_info['description'], initial_desc)
            # _system_update_tool_metadata_impl checks if desc changed before setting 'updated = True'
            # so save should not be called if description is identical.
            mock_save.assert_not_called()


    def test_update_tool_description_invalid_inputs(self):
        with patch.object(self.ts, '_system_update_tool_metadata_impl') as mock_internal_update:
            # Test non-string tool_name
            result_non_string_name = self.ts.update_tool_description(123, "valid desc")
            self.assertFalse(result_non_string_name)
            mock_internal_update.assert_not_called() # Internal method should not be reached

            # Test empty tool_name
            result_empty_name = self.ts.update_tool_description("", "valid desc")
            self.assertFalse(result_empty_name)
            mock_internal_update.assert_not_called()

            # Test non-string new_description
            result_non_string_desc = self.ts.update_tool_description(self.dummy_tool_name, 123)
            self.assertFalse(result_non_string_desc)
            mock_internal_update.assert_not_called()

            # Check original description is unchanged
            original_tool_info = self.ts.get_tool(self.dummy_tool_name)
            self.assertEqual(original_tool_info['description'], "Initial description")


if __name__ == '__main__':
    unittest.main()
