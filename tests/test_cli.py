import unittest
from unittest import mock
import asyncio
import os
import sys
import json

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path: # pragma: no cover
    sys.path.insert(0, project_root)

from ai_assistant.communication import cli
from ai_assistant.code_synthesis import CodeTaskResult, CodeTaskStatus, CodeTaskType

class TestCliToolGenerationFlow(unittest.IsolatedAsyncioTestCase):

    @mock.patch('ai_assistant.communication.cli.global_reflection_log.log_execution')
    @mock.patch('ai_assistant.communication.cli._perform_tool_registration')
    @mock.patch('ai_assistant.communication.cli.write_to_file')
    @mock.patch('builtins.input')
    @mock.patch('ai_assistant.communication.cli.CodeSynthesisService') # Corrected mock target
    @mock.patch('ai_assistant.communication.cli.tool_system_instance.execute_tool', new_callable=mock.AsyncMock)
    @mock.patch('ai_assistant.communication.cli.OllamaProvider') # Mock OllamaProvider for scaffold generation call
    async def test_handle_code_generation_triggers_scaffold_success(
        self, MockOllamaProvider, mock_execute_review_tool, MockCodeSynthesisService, mock_input,
        mock_cli_write_to_file, mock_perform_registration, mock_log_execution
    ):
        # --- Setup Mocks ---

        # 1. CodeSynthesisService().submit_task
        mock_css_instance = MockCodeSynthesisService.return_value

        new_tool_metadata = {"suggested_function_name": "my_new_tool_func", "suggested_tool_name": "myNewTool", "suggested_description": "A new tool."}
        new_tool_code = "def my_new_tool_func():\n    pass"

        # Mock result for NEW_TOOL_CREATION_LLM
        mock_css_instance.submit_task = mock.AsyncMock(return_value=CodeTaskResult(
            request_id="test_req_id",
            status=CodeTaskStatus.SUCCESS,
            generated_code=new_tool_code,
            metadata={"parsed_tool_metadata": new_tool_metadata}
        ))

        # Mock OllamaProvider for scaffold generation
        mock_llm_provider_instance = MockOllamaProvider.return_value
        test_scaffold_code = "import unittest\nclass TestMyNewTool(unittest.TestCase): pass"
        mock_llm_provider_instance.generate_code_async = mock.AsyncMock(return_value=test_scaffold_code)

        expected_test_target_path = os.path.join("tests", "custom_tools", "test_my_new_tool_func.py")

        # 2. User input (mock_input)
        # Sequence:
        # 1. Confirm suggested details (if logic uses suggestions) -> 'y'
        # 2. (If not using suggestions, other prompts... but we assume suggestions work)
        # 3. Save choice? -> CLI logic: if use_suggested_details, assumes save intent or proceeds.
        # Let's check CLI logic: "Use these details to save and register? (y/n): "
        mock_input.side_effect = ['y']

        # 3. fs_utils.write_to_file (mock_cli_write_to_file) - for saving the *tool* code AND scaffold
        mock_cli_write_to_file.return_value = True

        # 4. _perform_tool_registration (mock_perform_registration)
        mock_perform_registration.return_value = (True, "Tool registered.")

        # 5. Mock for code review tool
        mock_execute_review_tool.return_value = {"status": "approved", "comments": "Looks good."}


        # --- Call the function under test ---
        test_description = "a brand new awesome tool"
        await cli._handle_code_generation_and_registration(test_description, None, None)

        # --- Assertions ---

        # Check call to CodeSynthesisService().submit_task
        mock_css_instance.submit_task.assert_called_once()
        call_args = mock_css_instance.submit_task.call_args
        request_arg = call_args[0][0]
        self.assertEqual(request_arg.task_type, CodeTaskType.NEW_TOOL_CREATION_LLM)
        self.assertEqual(request_arg.context_data["description"], test_description)

        # User input for metadata confirmation was called
        mock_input.assert_called_with(mock.ANY)

        # write_to_file called for the new tool's code
        expected_tool_filepath = os.path.join("ai_assistant", "custom_tools", "my_new_tool_func.py")
        mock_cli_write_to_file.assert_any_call(expected_tool_filepath, new_tool_code)

        # _perform_tool_registration called
        expected_module_path = "ai_assistant.custom_tools.my_new_tool_func"
        mock_perform_registration.assert_called_once_with(
            expected_module_path,
            new_tool_metadata["suggested_function_name"],
            new_tool_metadata["suggested_tool_name"],
            new_tool_metadata["suggested_description"]
        )

        # Check for scaffold generation via OllamaProvider
        mock_llm_provider_instance.generate_code_async.assert_called_once()

        # Check for scaffold save
        mock_cli_write_to_file.assert_any_call(expected_test_target_path, test_scaffold_code)

        # Verify reflection logging for scaffold
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
        # Note: ActionExecutor now uses CodeSynthesisService but has a property code_service for compat
        # We need to mock that property or the structure CLI expects
        self.mock_orchestrator.action_executor.code_service = mock.Mock()
        self.mock_orchestrator.action_executor.code_service.llm_provider = mock.AsyncMock()

        self.results_queue = asyncio.Queue()

        self.orchestrator_patcher = mock.patch('ai_assistant.communication.cli._orchestrator', self.mock_orchestrator)
        self.queue_patcher = mock.patch('ai_assistant.communication.cli._results_queue', self.results_queue)

        self.mock_cli_orchestrator = self.orchestrator_patcher.start()
        self.mock_cli_results_queue = self.queue_patcher.start()

        self.log_event_patcher = mock.patch('ai_assistant.communication.cli.log_event')
        self.mock_log_event = self.log_event_patcher.start()

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

        mock_rephrase_error.assert_called_once_with(
            technical_error_message=technical_error_msg_for_llm,
            original_user_query=original_prompt,
            llm_provider=self.mock_cli_orchestrator.action_executor.code_service.llm_provider
        )

        status_update_item = await self.mock_cli_results_queue.get()
        self.assertEqual(status_update_item["type"], "status_update")
        self.assertIn(rephrased_message, status_update_item["message"].lower())
        self.assertIn(f"error processing '{original_prompt}'", status_update_item["message"].lower())

        command_result_item = await self.mock_cli_results_queue.get()
        self.assertEqual(command_result_item["type"], "command_result")
        self.assertFalse(command_result_item["success"])
        self.assertEqual(command_result_item["response"], rephrased_message)

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
        mock_rephrase_error.return_value = None

        await cli._process_command_wrapper(original_prompt, self.mock_cli_orchestrator, self.mock_cli_results_queue)

        mock_rephrase_error.assert_called_once()

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

        self.mock_cli_orchestrator.action_executor.code_service.llm_provider = None
        self.mock_cli_orchestrator.process_prompt.side_effect = technical_error

        with mock.patch('builtins.print') as mock_print:
            await cli._process_command_wrapper(original_prompt, self.mock_cli_orchestrator, self.mock_cli_results_queue)

        mock_rephrase_error.assert_not_called()

        provider_unavailable_msg_found = False
        for call_arg in mock_print.call_args_list:
            if "LLM provider not available" in str(call_arg):
                provider_unavailable_msg_found = True
                break
        self.assertTrue(provider_unavailable_msg_found, "Debug message for unavailable LLM provider not found.")

        status_update_item = await self.mock_cli_results_queue.get()
        self.assertIn(technical_error_msg_for_llm.lower(), status_update_item["message"].lower())
        command_result_item = await self.mock_cli_results_queue.get()
        self.assertEqual(command_result_item["response"], technical_error_msg_for_llm)

        self.mock_log_event.assert_called_once()
