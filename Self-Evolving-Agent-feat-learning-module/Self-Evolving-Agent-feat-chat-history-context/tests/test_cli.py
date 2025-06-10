import unittest
from unittest import mock
import asyncio
import os # For path manipulation if needed in tests
import sys
import json # For json.dumps in test data if needed, and for CodeService metadata

# Add project root to sys.path to allow importing ai_assistant modules
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path: # pragma: no cover
    sys.path.insert(0, project_root)

from ai_assistant.communication import cli # Module to test
# from ai_assistant.code_services.service import CodeService # Will be mocked via cli.CodeService
# from ai_assistant.core.fs_utils import write_to_file # Will be mocked via cli.write_to_file
# from ai_assistant.tools import tool_system # For _perform_tool_registration, will be mocked via cli._perform_tool_registration
# from ai_assistant.core.reflection import global_reflection_log # Will be mocked via cli.global_reflection_log


class TestCliToolGenerationFlow(unittest.TestCase):

    @mock.patch('ai_assistant.communication.cli.global_reflection_log.log_execution') # Mock logging
    @mock.patch('ai_assistant.communication.cli._perform_tool_registration') # Mock tool registration
    @mock.patch('ai_assistant.communication.cli.write_to_file') # Mock file saving (fs_utils)
    @mock.patch('builtins.input') # Mock user input
    @mock.patch('ai_assistant.communication.cli.CodeService') # Mock CodeService class
    @mock.patch('ai_assistant.communication.cli.tool_system_instance.execute_tool', new_callable=mock.AsyncMock) # Mock code review tool
    async def test_handle_code_generation_triggers_scaffold_success(
        self, mock_execute_review_tool, MockCodeService, mock_input,
        mock_cli_write_to_file, mock_perform_registration, mock_log_execution
    ):
        # --- Setup Mocks ---

        # 1. CodeService().generate_code (called twice)
        mock_cs_instance = MockCodeService.return_value

        new_tool_metadata = {"suggested_function_name": "my_new_tool_func", "suggested_tool_name": "myNewTool", "suggested_description": "A new tool."}
        new_tool_code = "def my_new_tool_func():\n    pass"

        test_scaffold_code = "import unittest\nclass TestMyNewTool(unittest.TestCase): pass"
        expected_test_target_path = os.path.join("tests", "custom_tools", "test_my_new_tool_func.py")

        # Configure side_effect for sequential calls to generate_code
        generate_code_results = [
            {
                "status": "SUCCESS_CODE_GENERATED", "code_string": new_tool_code,
                "metadata": new_tool_metadata, "logs": [], "error": None
            },
            {
                "status": "SUCCESS_CODE_GENERATED", "code_string": test_scaffold_code,
                "metadata": None, "logs": [], "error": None, "saved_to_path": expected_test_target_path
            }
        ]

        # Use a callable for side_effect to inspect arguments if necessary, or just return futures
        async def generate_code_side_effect(*args, **kwargs):
            if kwargs.get('context') == "NEW_TOOL":
                return generate_code_results[0]
            elif kwargs.get('context') == "GENERATE_UNIT_TEST_SCAFFOLD":
                return generate_code_results[1]
            # Fallback for any other calls, though not expected in this test
            return {"status": "ERROR_UNEXPECTED_CONTEXT_IN_MOCK"} # pragma: no cover

        mock_cs_instance.generate_code = mock.AsyncMock(side_effect=generate_code_side_effect)


        # 2. User input (mock_input)
        mock_input.side_effect = ['y'] # Confirm use of suggested metadata

        # 3. fs_utils.write_to_file (mock_cli_write_to_file) - for saving the *tool* code
        mock_cli_write_to_file.return_value = True # Tool saving success

        # 4. _perform_tool_registration (mock_perform_registration)
        mock_perform_registration.return_value = (True, "Tool registered.") # Registration success

        # 5. Mock for code review tool (if called) - assuming it's short enough to skip review
        # If code review is triggered (len(cleaned_code.splitlines()) > 3), this needs to be set.
        # new_tool_code is short, so review is skipped. If testing review, make new_tool_code longer.
        mock_execute_review_tool.return_value = {"status": "approved", "comments": "Looks good."}


        # --- Call the function under test ---
        test_description = "a brand new awesome tool"
        await cli._handle_code_generation_and_registration(test_description)

        # --- Assertions ---

        # Check calls to CodeService().generate_code
        self.assertEqual(mock_cs_instance.generate_code.call_count, 2)

        # Call 1 (NEW_TOOL)
        mock_cs_instance.generate_code.assert_any_call(
            context="NEW_TOOL",
            prompt_or_description=test_description,
            target_path=None
        )

        # Call 2 (GENERATE_UNIT_TEST_SCAFFOLD)
        expected_module_path_for_hint = "ai_assistant.custom_tools.my_new_tool_func"
        mock_cs_instance.generate_code.assert_any_call(
            context="GENERATE_UNIT_TEST_SCAFFOLD",
            prompt_or_description=new_tool_code,
            additional_context={"module_name_hint": expected_module_path_for_hint},
            target_path=expected_test_target_path
        )

        # User input for metadata confirmation was called
        mock_input.assert_called_with(mock.ANY)

        # write_to_file called for the new tool's code by CLI
        expected_tool_filepath = os.path.join("ai_assistant", "custom_tools", "my_new_tool_func.py")
        mock_cli_write_to_file.assert_called_once_with(expected_tool_filepath, new_tool_code)

        # _perform_tool_registration called
        mock_perform_registration.assert_called_once_with(
            expected_module_path_for_hint,
            new_tool_metadata["suggested_function_name"],
            new_tool_metadata["suggested_tool_name"],
            new_tool_metadata["suggested_description"]
        )

        # Verify reflection logging (optional, but good for completeness)
        # Check for scaffold success log
        scaffold_log_found = False
        for call_args in mock_log_execution.call_args_list:
            if call_args.kwargs.get("status_override") == "SCAFFOLD_GEN_SAVE_SUCCESS":
                scaffold_log_found = True
                self.assertIn(expected_test_target_path, call_args.kwargs.get("execution_results")[0])
                break
        self.assertTrue(scaffold_log_found, "Scaffold success log not found.")


if __name__ == '__main__': # pragma: no cover
    unittest.main()
