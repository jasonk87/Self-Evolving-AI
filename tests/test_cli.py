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


class TestCliCommandProcessing(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_orchestrator = mock.AsyncMock()
        # Mock the path to the llm_provider used by rephrase_error_message_conversationally
        self.mock_orchestrator.action_executor = mock.Mock()
        self.mock_orchestrator.action_executor.code_service = mock.Mock()
        self.mock_orchestrator.action_executor.code_service.llm_provider = mock.AsyncMock()

        self.results_queue = asyncio.Queue()

        # Patch the global _orchestrator and _results_queue in cli.py
        self.orchestrator_patcher = mock.patch('ai_assistant.communication.cli._orchestrator', self.mock_orchestrator)
        self.queue_patcher = mock.patch('ai_assistant.communication.cli._results_queue', self.results_queue)

        self.mock_cli_orchestrator = self.orchestrator_patcher.start()
        self.mock_cli_results_queue = self.queue_patcher.start()

        # Patch log_event as it's called in the error handler
        self.log_event_patcher = mock.patch('ai_assistant.communication.cli.log_event')
        self.mock_log_event = self.log_event_patcher.start()

        # Patch AUTONOMOUS_LEARNING_ENABLED to False to simplify tests by default
        self.autolearn_patcher = mock.patch('ai_assistant.communication.cli.AUTONOMOUS_LEARNING_ENABLED', False)
        self.mock_autolearn = self.autolearn_patcher.start()


    def tearDown(self):
        self.orchestrator_patcher.stop()
        self.queue_patcher.stop()
        self.log_event_patcher.stop()
        self.autolearn_patcher.stop()

    @mock.patch('ai_assistant.communication.cli.rephrase_error_message_conversationally', new_callable=mock.AsyncMock)
    async def test_process_command_wrapper_exception_rephrased_succeeds(self, mock_rephrase_error):
        original_prompt = "test prompt that causes error"
        technical_error = ValueError("Something went wrong in orchestrator")
        technical_error_msg_for_llm = f"{type(technical_error).__name__}: {str(technical_error)}"
        rephrased_message = "Oops! It seems there was a hiccup trying to understand that."

        self.mock_cli_orchestrator.process_prompt.side_effect = technical_error
        mock_rephrase_error.return_value = rephrased_message

        await cli._process_command_wrapper(original_prompt, self.mock_cli_orchestrator, self.mock_cli_results_queue)

        # Check that rephraser was called correctly
        mock_rephrase_error.assert_called_once_with(
            technical_error_message=technical_error_msg_for_llm,
            original_user_query=original_prompt,
            llm_provider=self.mock_cli_orchestrator.action_executor.code_service.llm_provider
        )

        # Check items put on the queue
        # First item is status_update with the rephrased error
        status_update_item = await self.mock_cli_results_queue.get()
        self.assertEqual(status_update_item["type"], "status_update")
        self.assertIn(rephrased_message, status_update_item["message"].lower()) # Message is formatted, check substring
        self.assertIn(f"error processing '{original_prompt}'", status_update_item["message"].lower())


        # Second item is command_result with the rephrased error
        command_result_item = await self.mock_cli_results_queue.get()
        self.assertEqual(command_result_item["type"], "command_result")
        self.assertFalse(command_result_item["success"])
        self.assertEqual(command_result_item["response"], rephrased_message)

        # Assert log_event was called with technical details
        self.mock_log_event.assert_called_once()
        log_args, log_kwargs = self.mock_log_event.call_args
        self.assertEqual(log_kwargs.get("event_type"), "CLI_WRAPPER_ERROR")
        self.assertIn(str(technical_error), log_kwargs.get("description"))


    @mock.patch('ai_assistant.communication.cli.rephrase_error_message_conversationally', new_callable=mock.AsyncMock)
    async def test_process_command_wrapper_exception_rephraser_fails(self, mock_rephrase_error):
        original_prompt = "another test prompt"
        technical_error = TypeError("A type related issue")
        technical_error_msg_for_llm = f"{type(technical_error).__name__}: {str(technical_error)}"

        self.mock_cli_orchestrator.process_prompt.side_effect = technical_error
        mock_rephrase_error.return_value = None # Simulate rephraser failing

        await cli._process_command_wrapper(original_prompt, self.mock_cli_orchestrator, self.mock_cli_results_queue)

        mock_rephrase_error.assert_called_once()

        # Check items on queue - should contain the original technical error
        status_update_item = await self.mock_cli_results_queue.get()
        self.assertEqual(status_update_item["type"], "status_update")
        self.assertIn(technical_error_msg_for_llm.lower(), status_update_item["message"].lower())

        command_result_item = await self.mock_cli_results_queue.get()
        self.assertEqual(command_result_item["type"], "command_result")
        self.assertFalse(command_result_item["success"])
        self.assertEqual(command_result_item["response"], technical_error_msg_for_llm)

        self.mock_log_event.assert_called_once()


    @mock.patch('ai_assistant.communication.cli.rephrase_error_message_conversationally', new_callable=mock.AsyncMock)
    async def test_process_command_wrapper_llm_provider_unavailable_for_rephrasing(self, mock_rephrase_error):
        original_prompt = "prompt with no llm for rephrase"
        technical_error = ConnectionError("Network down")
        technical_error_msg_for_llm = f"{type(technical_error).__name__}: {str(technical_error)}"

        # Simulate LLM provider being None
        self.mock_cli_orchestrator.action_executor.code_service.llm_provider = None
        self.mock_cli_orchestrator.process_prompt.side_effect = technical_error

        # We need to capture print output for the debug message when provider is None
        with mock.patch('builtins.print') as mock_print:
            await cli._process_command_wrapper(original_prompt, self.mock_cli_orchestrator, self.mock_cli_results_queue)

        mock_rephrase_error.assert_not_called() # Rephraser should not be called

        # Check for the debug print message
        provider_unavailable_msg_found = False
        for call_arg in mock_print.call_args_list:
            if "LLM provider not available" in str(call_arg):
                provider_unavailable_msg_found = True
                break
        self.assertTrue(provider_unavailable_msg_found, "Debug message for unavailable LLM provider not found.")

        # Check items on queue - should contain the original technical error
        status_update_item = await self.mock_cli_results_queue.get()
        self.assertIn(technical_error_msg_for_llm.lower(), status_update_item["message"].lower())
        command_result_item = await self.mock_cli_results_queue.get()
        self.assertEqual(command_result_item["response"], technical_error_msg_for_llm)

        self.mock_log_event.assert_called_once()
