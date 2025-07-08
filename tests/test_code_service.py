import unittest
from unittest import mock
from unittest.mock import patch, AsyncMock
import asyncio
import os
import sys
import uuid
import datetime
import json # Added for test data
from dataclasses import dataclass, field

try:
    from ai_assistant.code_services.service import CodeService, CodeReviewSeverity
    from ai_assistant.core import self_modification
except ImportError: # pragma: no cover
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.code_services.service import CodeService
    from ai_assistant.core import self_modification


class TestCodeService(unittest.TestCase):

    def setUp(self):
        self.mock_llm_provider = mock.AsyncMock()
        self.mock_self_mod_service = mock.Mock()
        self.mock_task_manager = mock.Mock()
        # Mock the add_task method to return a mock task object with a task_id
        self.mock_task = mock.Mock()
        self.mock_task.task_id = str(uuid.uuid4())
        self.mock_task_manager.add_task.return_value = self.mock_task

        self.mock_notification_manager = mock.Mock()


        self.code_service = CodeService(
            llm_provider=self.mock_llm_provider,
            self_modification_service=self.mock_self_mod_service,
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager
        )
        # Store a version of code_service with None providers for specific tests
        self.code_service_no_llm = CodeService(
            llm_provider=None,
            self_modification_service=self.mock_self_mod_service,
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager
        )
        self.code_service_no_self_mod = CodeService(
            llm_provider=self.mock_llm_provider,
            self_modification_service=None,
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager
        )
        self.code_service_no_task_manager = CodeService(
            llm_provider=self.mock_llm_provider,
            self_modification_service=self.mock_self_mod_service,
            task_manager=None, # No TaskManager
            notification_manager=self.mock_notification_manager
        )


    # --- Tests for generate_code (NEW_TOOL context) ---
    async def test_generate_code_new_tool_success_no_save(self): # RENAMED, target_path=None
        # Reset mocks for this specific test if they are instance-wide and might be affected by other tests
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task # Re-assign after reset

        expected_metadata = {"suggested_function_name": "add_numbers", "suggested_tool_name": "addNumbers", "suggested_description": "Adds two numbers."}
        metadata_json_str = json.dumps(expected_metadata)
        expected_code_content = "def add_numbers(a: int, b: int) -> int:\n    return a + b"
        llm_output = f"# METADATA: {metadata_json_str}\n{expected_code_content}"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_output

        result = await self.code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool to add two numbers.",
            target_path=None # Explicitly no save
        )

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.assertEqual(result["code_string"], expected_code_content)
        self.assertEqual(result["metadata"], expected_metadata)
        self.assertIsNone(result["error"])
        self.assertIsNone(result.get("saved_to_path")) # Verify no save path
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        self.mock_task_manager.add_task.assert_called_once()
        self.mock_task_manager.update_task_status.assert_called_with(
            self.mock_task.task_id,
            mock.ANY, # Status can vary (e.g., GENERATING_CODE, COMPLETED_SUCCESSFULLY)
            reason=mock.ANY, # Reason can vary
            step_desc=mock.ANY # step_desc can vary
        )
        # Check that update_task_status was called at least for 'COMPLETED_SUCCESSFULLY'
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected COMPLETED_SUCCESSFULLY status update was not made."
        )


    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_new_tool_success_and_save(self, mock_write_to_file):
        self.mock_task_manager.reset_mock() # Reset for this test
        self.mock_task_manager.add_task.return_value = self.mock_task


        expected_metadata = {"suggested_function_name": "add_numbers", "suggested_tool_name": "addNumbers", "suggested_description": "Adds two numbers."}
        metadata_json_str = json.dumps(expected_metadata)
        expected_code_content = "def add_numbers(a: int, b: int) -> int:\n    return a + b"
        llm_output = f"# METADATA: {metadata_json_str}\n{expected_code_content}"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_output
        mock_write_to_file.return_value = True

        test_target_path = "generated_tools/new_tool.py"
        result = await self.code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool to add two numbers.",
            target_path=test_target_path
        )

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.assertEqual(result["code_string"], expected_code_content)
        self.assertEqual(result["metadata"], expected_metadata)
        self.assertEqual(result["saved_to_path"], test_target_path)
        self.assertIsNone(result["error"])
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        mock_write_to_file.assert_called_once_with(test_target_path, expected_code_content)
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected COMPLETED_SUCCESSFULLY status update was not made for successful save."
        )

    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_new_tool_save_fails(self, mock_write_to_file):
        self.mock_task_manager.reset_mock() # Reset for this test
        self.mock_task_manager.add_task.return_value = self.mock_task

        expected_metadata = {"suggested_function_name": "add_numbers", "suggested_tool_name": "addNumbers", "suggested_description": "Adds two numbers."}
        metadata_json_str = json.dumps(expected_metadata)
        expected_code_content = "def add_numbers(a: int, b: int) -> int:\n    return a + b"
        llm_output = f"# METADATA: {metadata_json_str}\n{expected_code_content}"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_output
        mock_write_to_file.return_value = False

        test_target_path = "generated_tools/new_tool_fails_save.py"
        result = await self.code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool to add two numbers, save fails.",
            target_path=test_target_path
        )

        self.assertEqual(result["status"], "ERROR_SAVING_CODE")
        self.assertEqual(result["code_string"], expected_code_content)
        self.assertEqual(result["metadata"], expected_metadata)
        self.assertIsNone(result["saved_to_path"])
        self.assertIsNotNone(result["error"])
        self.assertIn("failed to save", result["error"])
        mock_write_to_file.assert_called_once_with(test_target_path, expected_code_content)
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_DURING_APPLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_DURING_APPLY status update was not made for save failure."
        )

    async def test_generate_code_new_tool_llm_no_code(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "" # Empty response

        result = await self.code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool."
        )
        self.assertEqual(result["status"], "ERROR_LLM_NO_CODE")
        self.assertIsNone(result["code_string"])
        self.assertIsNotNone(result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_UNKNOWN status update for LLM_NO_CODE."
        )

    async def test_generate_code_new_tool_missing_metadata_line(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        llm_output = "def my_func(): pass" # No # METADATA: line
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_output

        result = await self.code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool."
        )
        self.assertEqual(result["status"], "ERROR_METADATA_PARSING")
        self.assertEqual(result["code_string"], "def my_func(): pass")
        self.assertIsNone(result["metadata"])
        self.assertIsNotNone(result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_UNKNOWN status update for METADATA_PARSING error."
        )

    async def test_generate_code_new_tool_malformed_metadata_json(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        llm_output = "# METADATA: {this_is_not_json: }\ndef my_func(): pass"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_output

        result = await self.code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool."
        )
        self.assertEqual(result["status"], "ERROR_METADATA_PARSING")
        self.assertEqual(result["code_string"].strip(), "def my_func(): pass")
        self.assertIsNone(result["metadata"])
        self.assertIsNotNone(result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_UNKNOWN status update for malformed METADATA_JSON."
        )

    async def test_generate_code_new_tool_metadata_ok_no_code_block(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        expected_metadata = {"suggested_function_name": "add_numbers", "suggested_tool_name": "addNumbers", "suggested_description": "Adds two numbers."}
        metadata_json_str = json.dumps(expected_metadata)
        llm_output = f"# METADATA: {metadata_json_str}\n   # Only comments, no actual code"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_output

        result = await self.code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool."
        )
        self.assertEqual(result["status"], "ERROR_CODE_EMPTY_POST_METADATA")
        self.assertEqual(result["code_string"], "# Only comments, no actual code")
        self.assertEqual(result["metadata"], expected_metadata)
        self.assertIsNotNone(result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_UNKNOWN status update for CODE_EMPTY_POST_METADATA."
        )

    async def test_generate_code_unsupported_context_for_generate(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        # This test doesn't involve LLM provider, so can use any CS instance
        result = await self.code_service.generate_code(
            context="SELF_FIX_TOOL", # Using a context not valid for generate_code
            prompt_or_description="A test tool."
        )
        self.assertEqual(result["status"], "ERROR_UNSUPPORTED_CONTEXT")
        self.assertIsNone(result["code_string"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_PRE_REVIEW status for UNSUPPORTED_CONTEXT."
        )


    async def test_generate_code_llm_provider_missing(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        result = await self.code_service_no_llm.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool."
        )
        self.assertEqual(result["status"], "ERROR_LLM_PROVIDER_MISSING")
        self.assertIsNone(result["code_string"])
        self.assertIn("LLM provider not configured", result["error"])
        self.mock_task_manager.add_task.assert_called_once() # Task is added before provider check
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_PRE_REVIEW status for LLM_PROVIDER_MISSING."
        )


    # --- Tests for modify_code (focused on SELF_FIX_TOOL context) ---
    async def test_modify_code_self_fix_tool_success(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        self.mock_self_mod_service.get_function_source_code.return_value = "def old_func(a): return a"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "def old_func(a): return a + 1 # Fixed by LLM"

        result = await self.code_service.modify_code(
            context="SELF_FIX_TOOL",
            existing_code=None,
            modification_instruction="Fix the bug in old_func.",
            module_path="dummy.module",
            function_name="old_func"
        )

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.assertEqual(result["modified_code_string"], "def old_func(a): return a + 1 # Fixed by LLM")
        self.mock_self_mod_service.get_function_source_code.assert_called_once_with("dummy.module", "old_func")
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected COMPLETED_SUCCESSFULLY status for SELF_FIX_TOOL success."
        )

    async def test_modify_code_self_fix_tool_no_original_code(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        self.mock_self_mod_service.get_function_source_code.return_value = None

        result = await self.code_service.modify_code(
            context="SELF_FIX_TOOL",
            existing_code=None,
            modification_instruction="Fix the bug.",
            module_path="dummy.module",
            function_name="some_func"
        )
        self.assertEqual(result["status"], "ERROR_NO_ORIGINAL_CODE")
        self.assertIsNone(result["modified_code_string"])
        self.mock_self_mod_service.get_function_source_code.assert_called_once_with("dummy.module", "some_func")
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_PRE_REVIEW status for NO_ORIGINAL_CODE."
        )

    async def test_modify_code_self_fix_tool_llm_no_suggestion(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        # Provide existing_code directly, so get_function_source_code is not called
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "// NO_CODE_SUGGESTION_POSSIBLE"

        result = await self.code_service.modify_code(
            context="SELF_FIX_TOOL",
            existing_code="def old_func(a): return a",
            modification_instruction="Fix it.",
            module_path="dummy.module", # Still required for context
            function_name="old_func"    # Still required for context
        )
        self.assertEqual(result["status"], "ERROR_LLM_NO_SUGGESTION")
        self.assertIsNone(result["modified_code_string"])
        self.mock_self_mod_service.get_function_source_code.assert_not_called() # Because existing_code was provided
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_UNKNOWN status for LLM_NO_SUGGESTION."
        )

    async def test_modify_code_self_fix_tool_llm_empty_response(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "   "

        result = await self.code_service.modify_code(
            context="SELF_FIX_TOOL",
            existing_code="def old_func(a): return a", # Provide existing_code
            modification_instruction="Fix it.",
            module_path="dummy.module",
            function_name="old_func"
        )
        self.assertEqual(result["status"], "ERROR_LLM_NO_SUGGESTION")
        self.assertIsNone(result["modified_code_string"])
        self.mock_self_mod_service.get_function_source_code.assert_not_called()
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_UNKNOWN status for LLM empty response in SELF_FIX_TOOL."
        )

    async def test_modify_code_unsupported_context(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        result = await self.code_service.modify_code(
            context="UNKNOWN_CONTEXT",
            modification_instruction="Do something.",
            existing_code="code"
        )
        self.assertEqual(result["status"], "ERROR_UNSUPPORTED_CONTEXT")
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_PRE_REVIEW status for UNKNOWN_CONTEXT in modify_code."
        )

    async def test_modify_code_missing_details_for_self_fix(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        result = await self.code_service.modify_code(
            context="SELF_FIX_TOOL",
            modification_instruction="Fix it.",
            existing_code="code", # existing_code is provided, so self_mod_service not called for fetch
            module_path=None, # This is the detail that's missing for the prompt
            function_name="some_func"
        )
        self.assertEqual(result["status"], "ERROR_MISSING_DETAILS")
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_PRE_REVIEW status for MISSING_DETAILS in SELF_FIX_TOOL."
        )


    async def test_modify_code_llm_provider_missing(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        # For this test, self_modification_service might be called first if existing_code is None
        self.mock_self_mod_service.get_function_source_code.return_value = "def old_func(): pass"
        result = await self.code_service_no_llm.modify_code(
            context="SELF_FIX_TOOL",
            modification_instruction="Fix it.",
            existing_code=None, # Force attempt to use self_mod_service then llm_provider
            module_path="dummy.module",
            function_name="old_func"
        )
        self.assertEqual(result["status"], "ERROR_LLM_PROVIDER_MISSING")
        self.assertIsNone(result["modified_code_string"])
        self.assertIn("LLM provider not configured", result["error"])
        # Ensure self_mod_service was called as it's configured for code_service_no_llm
        self.mock_self_mod_service.get_function_source_code.assert_called_with("dummy.module", "old_func")
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_PRE_REVIEW for LLM_PROVIDER_MISSING in modify_code."
        )


    async def test_modify_code_self_mod_service_missing(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        result = await self.code_service_no_self_mod.modify_code(
            context="SELF_FIX_TOOL",
            modification_instruction="Fix it.",
            existing_code=None, # This will trigger the need for self_modification_service
            module_path="dummy.module",
            function_name="some_func"
        )
        self.assertEqual(result["status"], "ERROR_SELF_MOD_SERVICE_MISSING")
        self.assertIsNone(result["modified_code_string"])
        self.assertIn("Self modification service not configured", result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_PRE_REVIEW for SELF_MOD_SERVICE_MISSING."
        )

    # --- Tests for generate_code (GENERATE_UNIT_TEST_SCAFFOLD context) ---
    async def test_generate_code_unit_test_scaffold_success_no_save(self): # RENAMED
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code_to_test = "def my_func(x): return x*2"
        expected_scaffold = "import unittest\nfrom your_module_to_test import my_func\n\nclass TestMyFunc(unittest.TestCase):\n    def test_my_func_basic(self):\n        self.fail(\"Test not yet implemented\")"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = expected_scaffold

        result = await self.code_service.generate_code(
            context="GENERATE_UNIT_TEST_SCAFFOLD",
            prompt_or_description=sample_code_to_test,
            additional_context={"module_name_hint": "your_module_to_test"},
            target_path=None # Explicitly no save
        )

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.assertEqual(result["code_string"], expected_scaffold)
        self.assertIsNone(result["metadata"])
        self.assertIsNone(result["error"])
        self.assertIsNone(result.get("saved_to_path"))
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        args, kwargs = self.mock_llm_provider.invoke_ollama_model_async.call_args
        self.assertIn(sample_code_to_test, args[0])
        self.assertIn("module_name_hint='your_module_to_test'", args[0])
        self.mock_task_manager.add_task.assert_called_once()
        # This context currently doesn't have specific COMPLETED_SUCCESSFULLY update in service.py
        # It will fall to the generic generate_code exception handler or implicit success if no error.
        # For now, just check add_task was called. More specific status checks can be added if service logic changes.
        self.assertTrue(self.mock_task_manager.update_task_status.called)


    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_unit_test_scaffold_success_and_save(self, mock_write_to_file):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code_to_test = "def my_func(x): return x*2"
        expected_scaffold = "import unittest\nfrom your_module_to_test import my_func\n\nclass TestMyFunc(unittest.TestCase):\n    def test_my_func_basic(self):\n        self.fail(\"Test not yet implemented\")"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = expected_scaffold
        mock_write_to_file.return_value = True

        test_target_path = "tests/test_my_func_scaffold.py"
        result = await self.code_service.generate_code(
            context="GENERATE_UNIT_TEST_SCAFFOLD",
            prompt_or_description=sample_code_to_test,
            additional_context={"module_name_hint": "your_module_to_test"},
            target_path=test_target_path
        )

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.assertEqual(result["code_string"], expected_scaffold)
        self.assertEqual(result["saved_to_path"], test_target_path)
        self.assertIsNone(result["error"])
        mock_write_to_file.assert_called_once_with(test_target_path, expected_scaffold)
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(self.mock_task_manager.update_task_status.called) # Basic check for now

    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_unit_test_scaffold_save_fails(self, mock_write_to_file):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code_to_test = "def my_func(x): return x*2"
        expected_scaffold = "import unittest\nfrom your_module_to_test import my_func\n\nclass TestMyFunc(unittest.TestCase):\n    def test_my_func_basic(self):\n        self.fail(\"Test not yet implemented\")"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = expected_scaffold
        mock_write_to_file.return_value = False

        test_target_path = "tests/test_my_func_scaffold_fails_save.py"
        result = await self.code_service.generate_code(
            context="GENERATE_UNIT_TEST_SCAFFOLD",
            prompt_or_description=sample_code_to_test,
            additional_context={"module_name_hint": "your_module_to_test"},
            target_path=test_target_path
        )

        self.assertEqual(result["status"], "ERROR_SAVING_CODE")
        self.assertEqual(result["code_string"], expected_scaffold)
        self.assertIsNone(result["saved_to_path"])
        self.assertIsNotNone(result["error"])
        self.assertIn("failed to save", result["error"])
        mock_write_to_file.assert_called_once_with(test_target_path, expected_scaffold)
        self.mock_task_manager.add_task.assert_called_once()
        # This context also doesn't have specific FAILED_DURING_APPLY, relies on general error handling
        self.assertTrue(self.mock_task_manager.update_task_status.called)


    async def test_generate_code_unit_test_scaffold_llm_no_code(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code_to_test = "def my_func(x): return x*2"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "" # Empty response

        result = await self.code_service.generate_code(
            context="GENERATE_UNIT_TEST_SCAFFOLD",
            prompt_or_description=sample_code_to_test,
            additional_context={"module_name_hint": "your_module_to_test"}
        )
        self.assertEqual(result["status"], "ERROR_LLM_NO_CODE")
        self.assertIsNone(result["code_string"])
        self.assertIsNotNone(result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        # Relies on general error handling in generate_code for task update
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN # From top-level except
                for call in self.mock_task_manager.update_task_status.call_args_list
            ) or not self.mock_task_manager.update_task_status.called # If error happens before first update
        )


    async def test_generate_code_unit_test_scaffold_llm_returns_none(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code_to_test = "def my_func(x): return x*2"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = None # None response

        result = await self.code_service.generate_code(
            context="GENERATE_UNIT_TEST_SCAFFOLD",
            prompt_or_description=sample_code_to_test,
            additional_context={"module_name_hint": "your_module_to_test"}
        )
        self.assertEqual(result["status"], "ERROR_LLM_NO_CODE")
        self.assertIsNone(result["code_string"])
        self.assertIsNotNone(result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN
                for call in self.mock_task_manager.update_task_status.call_args_list
            ) or not self.mock_task_manager.update_task_status.called
        )

    async def test_generate_code_unit_test_scaffold_cleaning_applied(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code_to_test = "def my_func(x): return x*2"
        raw_llm_output = "```python\ndef test_scaffold(): pass\n```"
        expected_cleaned_output = "def test_scaffold(): pass"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = raw_llm_output

        result = await self.code_service.generate_code(
            context="GENERATE_UNIT_TEST_SCAFFOLD",
            prompt_or_description=sample_code_to_test
            # Not providing module_name_hint here to ensure defaults work
        )
        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.assertEqual(result["code_string"], expected_cleaned_output)
        # Check that the default module_name_hint was used in the prompt
        args, kwargs = self.mock_llm_provider.invoke_ollama_model_async.call_args
        self.assertIn("module_name_hint='your_module_to_test'", args[0])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(self.mock_task_manager.update_task_status.called)

    # --- Tests for generate_code (EXPERIMENTAL_HIERARCHICAL_OUTLINE context) ---
    async def test_generate_code_hierarchical_outline_success(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        # Use self.mock_llm_provider from setUp
        expected_outline_dict = {"module_name": "test_module.py", "components": [{"type": "function", "name": "main"}]}
        llm_json_output = json.dumps(expected_outline_dict)
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_json_output

        result = await self.code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_OUTLINE",
            prompt_or_description="A simple test module."
        )

        self.assertEqual(result["status"], "SUCCESS_OUTLINE_GENERATED")
        self.assertEqual(result["parsed_outline"], expected_outline_dict)
        self.assertEqual(result["outline_str"], llm_json_output)
        self.assertIsNone(result["code_string"])
        self.assertIsNone(result["error"])
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected COMPLETED_SUCCESSFULLY for hierarchical_outline_success."
        )

    async def test_generate_code_hierarchical_outline_llm_empty(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        self.mock_llm_provider.invoke_ollama_model_async.return_value = ""

        result = await self.code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_OUTLINE",
            prompt_or_description="A simple test module."
        )
        self.assertEqual(result["status"], "ERROR_LLM_NO_OUTLINE")
        self.assertIsNone(result["parsed_outline"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_UNKNOWN for hierarchical_outline_llm_empty."
        )

    async def test_generate_code_hierarchical_outline_bad_json(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "{'bad_json': not_quoted}" # Malformed JSON

        result = await self.code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_OUTLINE",
            prompt_or_description="A simple test module."
        )
        self.assertEqual(result["status"], "ERROR_OUTLINE_PARSING")
        self.assertIsNone(result["parsed_outline"])
        self.assertIsNotNone(result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_UNKNOWN for hierarchical_outline_bad_json."
        )

    # --- Tests for _generate_detail_for_component ---
    # Note: _generate_detail_for_component is a private method and doesn't directly interact with TaskManager.
    # TaskManager interactions are handled by its public callers like EXPERIMENTAL_HIERARCHICAL_FULL_TOOL.
    # So, no direct TaskManager assertions here.
    async def test_generate_detail_for_component_success_function(self):
        component_def = {
            "type": "function", "name": "my_util_func", "signature": "(path: str) -> bool",
            "description": "A utility function.", "body_placeholder": "Return True if path exists."
        }
        full_outline = {
            "module_name": "my_utils.py", "description": "Utility module.",
            "imports": ["os"]
        }
        # LLM is expected to return the full function definition as per current prompt design
        full_expected_code = "def my_util_func(path: str) -> bool:\n    return os.path.exists(path)"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = full_expected_code

        result_code = await self.code_service._generate_detail_for_component(
            component_definition=component_def,
            full_outline=full_outline,
            llm_config=None
        )

        self.assertEqual(result_code, full_expected_code)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        prompt_arg = self.mock_llm_provider.invoke_ollama_model_async.call_args[0][0]
        self.assertIn("my_util_func", prompt_arg) # Check component name
        self.assertIn("Return True if path exists", prompt_arg) # Check body placeholder
        self.assertIn("import os", prompt_arg) # Check module imports
        # Check for sibling function context
        self.assertIn("Other available functions in this module:", prompt_arg)
        self.assertIn("sibling_func(s: str) -> str # A sibling utility.", prompt_arg)
        self.assertIn("Available classes in this module:", prompt_arg)
        self.assertIn("Class HelperClass: Helper class for utils.", prompt_arg)
        self.assertIn("Methods: __init__(self), do_work(self)", prompt_arg)


    async def test_generate_detail_for_component_success_method(self):
        # This component_def is for 'process_alpha'. 'original_name' is crucial.
        component_def_alpha = {
            "type": "method", "name": "MyProcessor.process_alpha", "original_name": "process_alpha",
            "signature": "(self, data: dict) -> None",
            "description": "Processes alpha data.", "body_placeholder": "self.alpha_attr = data.get('alpha_key')"
        }
        # This is a sibling method.
        method_def_beta = {
            "type": "method", "name": "process_beta", "signature": "(self, value: int) -> bool",
            "description": "Processes beta value.", "body_placeholder": "return value > 10"
        }
        full_outline = {
            "module_name": "my_class_module.py",
            "description": "Module containing MyProcessor.",
            "components": [{
                "type": "class", "name": "MyProcessor",
                "attributes": [{"name": "alpha_attr", "type": "Optional[Any]", "description":"Alpha attribute"}],
                "description": "A data processor class.",
                "methods": [component_def_alpha, method_def_beta] # Both methods in the class
            }],
            "imports": ["collections"]
        }
        expected_code_alpha = "def process_alpha(self, data: dict) -> None:\n    self.alpha_attr = data.get('alpha_key')"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = expected_code_alpha

        result_code = await self.code_service._generate_detail_for_component(
            component_definition=component_def_alpha, # Generating for process_alpha
            full_outline=full_outline,
            llm_config=None
        )
        self.assertEqual(result_code, expected_code_alpha)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        prompt_arg = self.mock_llm_provider.invoke_ollama_model_async.call_args[0][0]

        # Check for correct context being passed for process_alpha
        self.assertIn("Implementing method 'process_alpha' for class 'MyProcessor'", prompt_arg)
        self.assertIn("Class Description: A data processor class.", prompt_arg)
        self.assertIn("- alpha_attr: Optional[Any] # Alpha attribute", prompt_arg) # Attribute of MyProcessor
        self.assertIn("Other available methods in this class:", prompt_arg)
        self.assertIn("- process_beta(self, value: int) -> bool # Processes beta value.", prompt_arg) # Sibling method
        self.assertIn("Overall Module Description: Module containing MyProcessor.", prompt_arg)
        self.assertIn("import collections", prompt_arg) # Module import

    async def test_generate_detail_for_component_llm_returns_none(self):
        self.mock_task_manager.reset_mock() # Though not directly used by _generate_detail, good practice for consistency
        self.mock_task_manager.add_task.return_value = self.mock_task
        component_def = {"type": "function", "name": "test_func", "signature": "()", "description": "", "body_placeholder": ""}
        full_outline = {"imports": []}
        self.mock_llm_provider.invoke_ollama_model_async.return_value = None

        result_code = await self.code_service._generate_detail_for_component(component_def, full_outline, None)
        self.assertIsNone(result_code)

    async def test_generate_detail_for_component_llm_returns_error_marker(self):
        component_def = {"type": "function", "name": "test_func", "signature": "()", "description": "", "body_placeholder": ""}
        full_outline = {"imports": []}
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "# IMPLEMENTATION_ERROR: Too complex."

        result_code = await self.code_service._generate_detail_for_component(component_def, full_outline, None)
        self.assertIsNone(result_code)

    async def test_generate_detail_for_component_llm_returns_short_code(self):
        component_def = {"type": "function", "name": "test_func", "signature": "()", "description": "", "body_placeholder": ""}
        full_outline = {"imports": []}
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "pass" # Too short

        result_code = await self.code_service._generate_detail_for_component(component_def, full_outline, None)
        self.assertIsNone(result_code)

    async def test_generate_detail_for_component_code_cleaning(self):
        component_def = {"type": "function", "name": "test_func", "signature": "()", "description": "", "body_placeholder": ""}
        full_outline = {"imports": []}
        raw_code = "```python\ndef test_func():\n    # Escaped newline test \\n    pass\n```"
        # The implementation now replaces "\\n" with "\n" and then all "\n" with actual newlines.
        # So, "\\n" -> "\n" (literal newline char)
        expected_cleaned_code = "def test_func():\n    # Escaped newline test \n    pass"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = raw_code

        result_code = await self.code_service._generate_detail_for_component(component_def, full_outline, None)
        self.assertEqual(result_code, expected_cleaned_code)

    # --- Tests for _assemble_components ---
    def test_assemble_components_only_functions(self):
        outline = {
            "imports": ["os", "sys"],
            "components": [
                {"type": "function", "name": "func1", "signature": "()", "description": "d1", "body_placeholder": "p1"},
                {"type": "function", "name": "func2", "signature": "(x: int)", "description": "d2", "body_placeholder": "p2"}
            ],
            "main_execution_block": "if __name__ == '__main__':\n    func1()"
        }
        details = {
            "func1": "def func1():\n    print('hello')",
            "func2": "def func2(x: int):\n    print(f'x is {x}')"
        }
        result_code = self.code_service._assemble_components(outline, details)

        expected_code = """import os
import sys

def func1():
    print('hello')

def func2(x: int):
    print(f'x is {x}')


if __name__ == '__main__':
    func1()"""
        self.assertEqual(result_code.strip(), expected_code.strip())

    def test_assemble_components_class_with_methods(self):
        outline = {
            "imports": ["math"],
            "components": [{
                "type": "class", "name": "MyCalc", "description": "A calculator.",
                "attributes": [{"name": "pi", "type": "float", "description": "Value of PI"}],
                "methods": [
                    {"type": "method", "name": "__init__", "signature": "(self, val: float)", "description": "ctor", "body_placeholder": "self.val = val"},
                    {"type": "method", "name": "add", "signature": "(self, x: float) -> float", "description": "adds", "body_placeholder": "return self.val + x"}
                ]
            }]
        }
        details = {
            "MyCalc.__init__": "def __init__(self, val: float):\n    self.val = val",
            "MyCalc.add": "def add(self, x: float) -> float:\n    return self.val + x"
        }
        result_code = self.code_service._assemble_components(outline, details)

        expected_code = """import math

class MyCalc:
    """A calculator."""

    # Defined attributes (from outline):
    # pi: float # Value of PI

    def __init__(self, val: float):
        self.val = val

    def add(self, x: float) -> float:
        return self.val + x

"""
        self.assertEqual(result_code.strip(), expected_code.strip())


    def test_assemble_components_missing_detail_uses_placeholder(self):
        outline = {
            "components": [{"type": "function", "name": "func1", "signature": "()", "description": "Test func", "body_placeholder": "pass"}]
        }
        details = {} # func1 detail missing
        result_code = self.code_service._assemble_components(outline, details)

        self.assertIn("# Function 'func1' was planned but not generated.", result_code)
        self.assertIn("def func1():", result_code)
        self.assertIn("Original placeholder: pass", result_code)

    def test_assemble_components_module_docstring(self):
        outline = {"module_docstring": "This is a test module."}
        details = {}
        result_code = self.code_service._assemble_components(outline, details)
        self.assertIn('"""This is a test module."""', result_code)

    # --- Tests for generate_code (EXPERIMENTAL_HIERARCHICAL_FULL_TOOL context) ---
    async def test_generate_code_hierarchical_full_tool_success(self):
        mock_outline = {
            "module_name": "test_tool.py",
            "components": [
                {"type": "function", "name": "func_one", "signature": "()", "description": "d1", "body_placeholder": "p1"},
                {"type": "class", "name": "MyClass", "methods": [
                    {"type": "method", "name": "method_a", "signature": "(self)", "description": "d2", "body_placeholder": "p2"}
                ]}
            ]
        }
        outline_gen_success_return = {
            "status": "SUCCESS_OUTLINE_GENERATED", "parsed_outline": mock_outline,
            "outline_str": json.dumps(mock_outline), "logs": [], "error": None
        }

        detail_for_func_one = "def func_one():\n    pass # func_one_impl"
        # Key for method must be ClassName.MethodName
        detail_for_method_a = "def method_a(self):\n    pass # method_a_impl"

        async def mock_detail_gen(*args, **kwargs):
            component_def = args[0] # component_definition is the first positional argument
            if component_def["name"] == "func_one": # This is a function
                return detail_for_func_one
            # For methods, the 'name' in component_def passed to _generate_detail_for_component
            # is already ClassName.MethodName due to the refactoring in EXPERIMENTAL_HIERARCHICAL_FULL_TOOL
            elif component_def["name"] == "MyClass.method_a":
                return detail_for_method_a
            return None # pragma: no cover

        # Mock _generate_hierarchical_outline directly
        with mock.patch.object(self.code_service, '_generate_hierarchical_outline', return_value=outline_gen_success_return) as mock_outline_call, \
             mock.patch.object(self.code_service, '_generate_detail_for_component', side_effect=mock_detail_gen) as mock_detail_call:

            result = await self.code_service.generate_code(
                context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL",
                prompt_or_description="A complex tool."
            )

            self.assertEqual(result["status"], "SUCCESS_HIERARCHICAL_DETAILS_GENERATED")
            self.assertEqual(result["parsed_outline"], mock_outline)
            self.assertIsNone(result["code_string"]) # This context does not assemble

            expected_component_details = {
                "func_one": detail_for_func_one,
                "MyClass.method_a": detail_for_method_a
            }
            self.assertEqual(result["component_details"], expected_component_details)

            mock_outline_call.assert_called_once_with("A complex tool.", None) # llm_config is None by default

            self.assertEqual(mock_detail_call.call_count, 2)

            # Check calls to _generate_detail_for_component
            # First call for func_one
            call_args_func_one = mock_detail_call.call_args_list[0][0] # First positional arg of first call
            self.assertEqual(call_args_func_one[0]['name'], "func_one")
            self.assertEqual(call_args_func_one[1], mock_outline) # full_outline

            # Second call for MyClass.method_a
            call_args_method_a = mock_detail_call.call_args_list[1][0] # First positional arg of second call
            self.assertEqual(call_args_method_a[0]['name'], "MyClass.method_a") # Name is now Class.Method
            self.assertEqual(call_args_method_a[0]['original_name'], "method_a") # Original name preserved
            self.assertEqual(call_args_method_a[1], mock_outline) # full_outline

            self.mock_task_manager.add_task.assert_called_once()
            self.assertTrue(
                any(
                    call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                    for call in self.mock_task_manager.update_task_status.call_args_list
                ),
                "Expected COMPLETED_SUCCESSFULLY for HIERARCHICAL_FULL_TOOL success."
            )


    async def test_generate_code_hierarchical_full_tool_outline_fails(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        outline_gen_failure_return = {
            "status": "ERROR_OUTLINE_PARSING", "parsed_outline": None,
            "outline_str": "{bad json", "logs": ["Failed parsing"], "error": "JSON error"
        }
        # Mock _generate_hierarchical_outline directly
        with mock.patch.object(self.code_service, '_generate_hierarchical_outline', return_value=outline_gen_failure_return) as mock_outline_call:
            result = await self.code_service.generate_code(
                context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL",
                prompt_or_description="A complex tool."
            )
            self.assertEqual(result["status"], "ERROR_OUTLINE_PARSING") # Status should propagate
            self.assertIsNone(result["component_details"])
            mock_outline_call.assert_called_once_with("A complex tool.", None)
            self.mock_task_manager.add_task.assert_called_once()
            self.assertTrue(
                any(
                    call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                    for call in self.mock_task_manager.update_task_status.call_args_list
                ),
                "Expected FAILED_UNKNOWN for HIERARCHICAL_FULL_TOOL outline failure."
            )


    async def test_generate_code_hierarchical_full_tool_one_detail_fails(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        mock_outline = {
            "module_name": "test_tool.py",
            "components": [
                {"type": "function", "name": "func_one", "signature": "()", "description": "d1", "body_placeholder": "p1"},
                {"type": "function", "name": "func_two", "signature": "()", "description": "d2", "body_placeholder": "p2"}
            ]
        }
        outline_gen_success_return = {"status": "SUCCESS_OUTLINE_GENERATED", "parsed_outline": mock_outline, "logs": [], "error": None}
        detail_for_func_one = "def func_one(): pass"

        async def mock_detail_gen_partial_fail(*args, **kwargs):
            component_def = args[0]
            if component_def["name"] == "func_one":
                return detail_for_func_one
            elif component_def["name"] == "func_two":
                return None # Simulate failure for func_two
            return None # pragma: no cover

        with mock.patch.object(self.code_service, '_generate_hierarchical_outline', return_value=outline_gen_success_return), \
             mock.patch.object(self.code_service, '_generate_detail_for_component', side_effect=mock_detail_gen_partial_fail) as mock_detail_call:

            result = await self.code_service.generate_code(
                context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL",
                prompt_or_description="Tool with two funcs."
            )

            self.assertEqual(result["status"], "PARTIAL_HIERARCHICAL_DETAILS_GENERATED")
            self.assertEqual(result["parsed_outline"], mock_outline)
            expected_component_details = {
                "func_one": detail_for_func_one,
                "func_two": None
            }
            self.assertEqual(result["component_details"], expected_component_details)
            self.assertIsNotNone(result["error"]) # Error should be set for partial failure
            self.assertEqual(mock_detail_call.call_count, 2)
            self.mock_task_manager.add_task.assert_called_once()
            self.assertTrue(
                any(
                    call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                    and "Partial success" in call.kwargs.get("reason", "")
                    for call in self.mock_task_manager.update_task_status.call_args_list
                ),
                "Expected FAILED_UNKNOWN with partial success reason for HIERARCHICAL_FULL_TOOL one detail_fails."
            )

    # --- Tests for _generate_hierarchical_outline (private method) ---
    # This is a private method, TaskManager calls are handled by its public callers.
    async def test_private_generate_hierarchical_outline_success(self):
        expected_outline_dict = {"module_name": "test_module.py", "components": [{"type": "function", "name": "main"}]}
        llm_json_output = json.dumps(expected_outline_dict)
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_json_output

        result = await self.code_service._generate_hierarchical_outline(
            high_level_description="A simple test module.",
            llm_config=None
        )

        self.assertEqual(result["status"], "SUCCESS_OUTLINE_GENERATED")
        self.assertEqual(result["parsed_outline"], expected_outline_dict)
        self.assertEqual(result["outline_str"], llm_json_output)
        self.assertIsNone(result["error"])
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()

    async def test_private_generate_hierarchical_outline_llm_fails(self):
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "" # Empty response

        result = await self.code_service._generate_hierarchical_outline(
            high_level_description="A module that will cause LLM to fail.",
            llm_config=None
        )
        self.assertEqual(result["status"], "ERROR_LLM_NO_OUTLINE")
        self.assertIsNone(result["parsed_outline"])
        self.assertEqual(result["outline_str"], "")
        self.assertIsNotNone(result["error"])

    async def test_private_generate_hierarchical_outline_json_error(self):
        malformed_json_output = "{'this_is_bad_json': true" # Missing closing brace
        self.mock_llm_provider.invoke_ollama_model_async.return_value = malformed_json_output

        result = await self.code_service._generate_hierarchical_outline(
            high_level_description="A module that returns bad JSON.",
            llm_config=None
        )
        self.assertEqual(result["status"], "ERROR_OUTLINE_PARSING")
        self.assertIsNone(result["parsed_outline"])
        self.assertEqual(result["outline_str"], malformed_json_output)
        self.assertIsNotNone(result["error"])
        self.assertIn("Failed to parse LLM JSON outline", result["error"])

    # --- Tests for generate_code (HIERARCHICAL_GEN_COMPLETE_TOOL context) ---
    async def test_generate_code_hierarchical_complete_tool_success_no_save(self):
        mock_outline = {"module_name": "tool.py", "imports": ["os"], "components": [{"type": "function", "name": "my_func"}]}
        detail_for_my_func = "def my_func():\n    print('done')"

        expected_assembled_code = self.code_service._assemble_components(mock_outline, {"my_func": detail_for_my_func})

        # Mock _generate_hierarchical_outline
        self.code_service._generate_hierarchical_outline = AsyncMock(return_value={
            "status": "SUCCESS_OUTLINE_GENERATED", "parsed_outline": mock_outline, "logs": [], "error": None
        })
        # Mock _generate_detail_for_component
        self.code_service._generate_detail_for_component = AsyncMock(return_value=detail_for_my_func)

        result = await self.code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="A complex tool requiring assembly.",
            target_path=None # Explicitly no save
        )

        self.assertEqual(result["status"], "SUCCESS_HIERARCHICAL_ASSEMBLED")
        self.assertEqual(result["code_string"].strip(), expected_assembled_code.strip())
        self.assertEqual(result["parsed_outline"], mock_outline)
        self.assertEqual(result["component_details"], {"my_func": detail_for_my_func})
        self.assertIsNone(result.get("saved_to_path"))

        self.code_service._generate_hierarchical_outline.assert_called_once_with(
            "A complex tool requiring assembly.", None
        )
        self.code_service._generate_detail_for_component.assert_called_once_with(
            component_definition=mock_outline["components"][0],
            full_outline=mock_outline,
            llm_config=None
        )
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected COMPLETED_SUCCESSFULLY for HIERARCHICAL_GEN_COMPLETE_TOOL success no save."
        )


    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_hierarchical_complete_tool_success_and_save(self, mock_write_to_file):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        mock_outline = {"module_name": "tool.py", "imports": ["os"], "components": [{"type": "function", "name": "my_func"}]}
        detail_for_my_func = "def my_func():\n    print('done')"
        expected_assembled_code = self.code_service._assemble_components(mock_outline, {"my_func": detail_for_my_func})
        mock_write_to_file.return_value = True

        self.code_service._generate_hierarchical_outline = AsyncMock(return_value={
            "status": "SUCCESS_OUTLINE_GENERATED", "parsed_outline": mock_outline, "logs": [], "error": None
        })
        self.code_service._generate_detail_for_component = AsyncMock(return_value=detail_for_my_func)

        test_target_path = "output/hierarchical_tool.py"
        result = await self.code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="A complex tool requiring assembly.",
            target_path=test_target_path
        )

        self.assertEqual(result["status"], "SUCCESS_HIERARCHICAL_ASSEMBLED")
        self.assertEqual(result["code_string"].strip(), expected_assembled_code.strip())
        self.assertEqual(result["saved_to_path"], test_target_path)
        mock_write_to_file.assert_called_once_with(test_target_path, expected_assembled_code)
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected COMPLETED_SUCCESSFULLY for HIERARCHICAL_GEN_COMPLETE_TOOL success and save."
        )

    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_hierarchical_complete_tool_save_fails(self, mock_write_to_file):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        mock_outline = {"module_name": "tool.py", "imports": ["os"], "components": [{"type": "function", "name": "my_func"}]}
        detail_for_my_func = "def my_func():\n    print('done')"
        expected_assembled_code = self.code_service._assemble_components(mock_outline, {"my_func": detail_for_my_func})
        mock_write_to_file.return_value = False

        self.code_service._generate_hierarchical_outline = AsyncMock(return_value={
            "status": "SUCCESS_OUTLINE_GENERATED", "parsed_outline": mock_outline, "logs": [], "error": None
        })
        self.code_service._generate_detail_for_component = AsyncMock(return_value=detail_for_my_func)

        test_target_path = "output/hierarchical_tool_fail_save.py"
        result = await self.code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="A complex tool, save fails.",
            target_path=test_target_path
        )

        self.assertEqual(result["status"], "ERROR_SAVING_ASSEMBLED_CODE")
        self.assertEqual(result["code_string"].strip(), expected_assembled_code.strip())
        self.assertIsNone(result["saved_to_path"])
        self.assertIsNotNone(result["error"])
        self.assertIn("failed to save", result["error"])
        mock_write_to_file.assert_called_once_with(test_target_path, expected_assembled_code)
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                and result.get("error") in call.kwargs.get("reason", "")
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_UNKNOWN with error reason for HIERARCHICAL_GEN_COMPLETE_TOOL save failure."
        )

    async def test_generate_code_hierarchical_complete_tool_orchestration_fails(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        # Test when _generate_hierarchical_outline fails
        self.code_service._generate_hierarchical_outline = AsyncMock(return_value={
            "status": "ERROR_OUTLINE_PARSING", "parsed_outline": None, "logs": ["Failed parsing"], "error": "JSON error"
        })
        self.code_service._generate_detail_for_component = AsyncMock() # Should not be called
        self.code_service._assemble_components = mock.Mock() # Should not be called

        result = await self.code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="A complex tool."
        )

        self.assertEqual(result["status"], "ERROR_OUTLINE_PARSING")
        self.assertIsNone(result["code_string"])
        self.code_service._generate_hierarchical_outline.assert_called_once()
        self.code_service._generate_detail_for_component.assert_not_called()
        self.code_service._assemble_components.assert_not_called()
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW and call.args[0] == self.mock_task.task_id
                and "Outline generation failed" in call.kwargs.get("step_desc", "")
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_PRE_REVIEW with outline failure step_desc for HIERARCHICAL_GEN_COMPLETE_TOOL orchestration failure."
        )


    async def test_generate_code_hierarchical_complete_tool_assembly_fails(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        mock_outline = {"components": []} # Minimal valid outline
        self.code_service._generate_hierarchical_outline = AsyncMock(return_value={
            "status": "SUCCESS_OUTLINE_GENERATED", "parsed_outline": mock_outline, "logs": [], "error": None
        })
        # Assume detail generation succeeds (or no components to generate for)
        self.code_service._generate_detail_for_component = AsyncMock(return_value="def some_func(): pass")

        # Mock _assemble_components to raise an exception
        with mock.patch.object(self.code_service, '_assemble_components', side_effect=Exception("Assembly crashed!")) as mock_assemble:
            result = await self.code_service.generate_code(
                context="HIERARCHICAL_GEN_COMPLETE_TOOL",
                prompt_or_description="A complex tool."
            )

            self.assertEqual(result["status"], "ERROR_ASSEMBLY_FAILED")
            self.assertIsNone(result["code_string"])
            self.assertIn("Assembly crashed!", result.get("error", ""))
            mock_assemble.assert_called_once()
            self.mock_task_manager.add_task.assert_called_once()
            self.assertTrue(
                any(
                    call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                    and "Assembly failed" in call.kwargs.get("reason", "")
                    for call in self.mock_task_manager.update_task_status.call_args_list
                ),
                "Expected FAILED_UNKNOWN with assembly failure reason for HIERARCHICAL_GEN_COMPLETE_TOOL assembly failure."
            )

    # --- Tests for modify_code (GRANULAR_CODE_REFACTOR context) ---
    async def test_modify_code_granular_refactor_success_with_existing_code(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        original_code = "def my_func(a):\n    print('old line')\n    return a * 2"
        section_id = "print('old line')"
        instruction = "Replace the print statement with print('new line')"
        expected_modified_code = "def my_func(a):\n    print('new line') # Modified by LLM\n    return a * 2"

        self.mock_llm_provider.invoke_ollama_model_async.return_value = expected_modified_code

        result = await self.code_service.modify_code(
            context="GRANULAR_CODE_REFACTOR",
            modification_instruction=instruction,
            existing_code=original_code,
            module_path="test.py", # Required for prompt
            function_name="my_func",   # Required for prompt
            additional_context={"section_identifier": section_id}
        )

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.assertEqual(result["modified_code_string"], expected_modified_code)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        prompt_arg = self.mock_llm_provider.invoke_ollama_model_async.call_args[0][0]
        self.assertIn(original_code, prompt_arg)
        self.assertIn(section_id, prompt_arg)
        self.assertIn(instruction, prompt_arg)
        self.mock_self_mod_service.get_function_source_code.assert_not_called()
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected COMPLETED_SUCCESSFULLY for GRANULAR_CODE_REFACTOR success."
        )

    async def test_modify_code_granular_refactor_success_fetch_code(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        original_code = "def fetched_func(b):\n    return b - 1"
        section_id = "return b - 1"
        instruction = "Change to return b - 2"
        expected_modified_code = "def fetched_func(b):\n    return b - 2 # Modified by LLM"

        self.mock_self_mod_service.get_function_source_code.return_value = original_code
        self.mock_llm_provider.invoke_ollama_model_async.return_value = expected_modified_code

        result = await self.code_service.modify_code(
            context="GRANULAR_CODE_REFACTOR",
            modification_instruction=instruction,
            existing_code=None, # Trigger fetch
            module_path="fetch_test.py",
            function_name="fetched_func",
            additional_context={"section_identifier": section_id}
        )
        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.assertEqual(result["modified_code_string"], expected_modified_code)
        self.mock_self_mod_service.get_function_source_code.assert_called_once_with("fetch_test.py", "fetched_func")
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected COMPLETED_SUCCESSFULLY for GRANULAR_CODE_REFACTOR fetch_code success."
        )

    async def test_modify_code_granular_refactor_missing_section_identifier(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        result = await self.code_service.modify_code(
            context="GRANULAR_CODE_REFACTOR",
            modification_instruction="Refactor something.",
            existing_code="def test_func(): pass",
            module_path="test.py",
            function_name="test_func",
            additional_context={} # Missing section_identifier
        )
        self.assertEqual(result["status"], "ERROR_MISSING_SECTION_IDENTIFIER")
        self.assertIsNone(result["modified_code_string"])
        self.assertIn("Section identifier not provided", result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_PRE_REVIEW for GRANULAR_CODE_REFACTOR missing_section_identifier."
        )

    async def test_modify_code_granular_refactor_llm_no_suggestion(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "// REFACTORING_SUGGESTION_IMPOSSIBLE"
        result = await self.code_service.modify_code(
            context="GRANULAR_CODE_REFACTOR",
            modification_instruction="An impossible task.",
            existing_code="def test_func(): pass",
            module_path="test.py",
            function_name="test_func",
            additional_context={"section_identifier": "pass"}
        )
        self.assertEqual(result["status"], "ERROR_LLM_NO_SUGGESTION")
        self.assertIsNone(result["modified_code_string"])
        self.assertIn("REFACTORING_SUGGESTION_IMPOSSIBLE", result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_UNKNOWN for GRANULAR_CODE_REFACTOR LLM_NO_SUGGESTION."
        )

    # --- Tests for Linter Integration ---
    # These tests already mock _run_linter, so TaskManager calls related to linting
    # are implicitly part of the success/failure paths of the calling methods (e.g., NEW_TOOL).
    # We'll add resets and basic add_task checks for consistency.
    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_generate_code_new_tool_with_linter_no_issues(self, mock_run_linter_method):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        mock_run_linter_method.return_value = ([], None) # No lint issues, no linter error
        expected_code = "def perfectly_fine_tool():\n    return True"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = f'# METADATA: {json.dumps({"suggested_function_name": "fine_tool"})}\n{expected_code}'

        result = await self.code_service.generate_code(context="NEW_TOOL", prompt_or_description="good code")

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.assertEqual(result["code_string"], expected_code)
        mock_run_linter_method.assert_called_once_with(expected_code)
        self.assertTrue(all("LINT" not in log for log in result.get("logs", [])))
        self.mock_task_manager.add_task.assert_called_once() # From NEW_TOOL context

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_generate_code_new_tool_with_linter_issues_found(self, mock_run_linter_method):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        lint_issue_msg = "LINT (Ruff): E999 SyntaxError at 1:1: Bad syntax here"
        mock_run_linter_method.return_value = ([lint_issue_msg], None)
        buggy_code = "def buggy_tool(:\n    pass" # Syntax error
        self.mock_llm_provider.invoke_ollama_model_async.return_value = f'# METADATA: {json.dumps({"suggested_function_name": "buggy_tool"})}\n{buggy_code}'

        result = await self.code_service.generate_code(context="NEW_TOOL", prompt_or_description="buggy code")

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED") # Status not affected by lint
        self.assertEqual(result["code_string"], buggy_code)
        mock_run_linter_method.assert_called_once_with(buggy_code)
        self.assertIn("Linting issues found:", result.get("logs", []))
        self.assertIn(lint_issue_msg, result.get("logs", []))
        self.mock_task_manager.add_task.assert_called_once()

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_generate_code_new_tool_with_linter_execution_error(self, mock_run_linter_method):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        linter_crash_error = "Ruff crashed unexpectedly"
        mock_run_linter_method.return_value = ([], linter_crash_error)
        some_code = "def some_code_tool():\n    return 42"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = f'# METADATA: {json.dumps({"suggested_function_name": "some_code_tool"})}\n{some_code}'

        result = await self.code_service.generate_code(context="NEW_TOOL", prompt_or_description="code for linter crash")

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED") # Status not affected
        mock_run_linter_method.assert_called_once_with(some_code)
        self.assertIn(f"Linter execution error: {linter_crash_error}", result.get("logs", []))
        self.mock_task_manager.add_task.assert_called_once()

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_generate_code_hierarchical_complete_tool_with_linter_issues(self, mock_run_linter_method):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        mock_outline = {"module_name": "complex_tool.py", "components": [{"type": "function", "name": "main_func"}]}
        assembled_code_with_issues = "import os\n\ndef main_func( ):\n    print('issue here') # Example issue for linter"
        lint_issue_msg = "LINT (Pyflakes): main_func has trailing whitespace on params line"
        mock_run_linter_method.return_value = ([lint_issue_msg], None)

        # Mock the hierarchical generation part
        self.code_service._generate_hierarchical_outline = AsyncMock(return_value={
            "status": "SUCCESS_OUTLINE_GENERATED", "parsed_outline": mock_outline, "logs": [], "error": None
        })
        self.code_service._generate_detail_for_component = AsyncMock(return_value="def main_func( ):\n    print('issue here')")
        # Ensure _assemble_components returns the exact string we want to test linting on
        self.code_service._assemble_components = mock.Mock(return_value=assembled_code_with_issues)


        result = await self.code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="Generate a hierarchical tool with lint issues."
        )

        self.assertEqual(result["status"], "SUCCESS_HIERARCHICAL_ASSEMBLED") # Linting doesn't change status
        self.assertEqual(result["code_string"], assembled_code_with_issues)
        mock_run_linter_method.assert_called_once_with(assembled_code_with_issues)
        self.assertIn("Linting issues found in assembled code:", result.get("logs", []))
        self.assertIn(lint_issue_msg, result.get("logs", []))
        self.mock_task_manager.add_task.assert_called_once() # From HIERARCHICAL_GEN_COMPLETE_TOOL context

    # --- Tests for modify_code (SELF_FIX_AST context) ---
    async def test_modify_code_self_fix_ast_success(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        self.mock_self_mod_service.edit_function_source_code.return_value = True # Assume it returns True on success

        module_path = "my_module.py"
        function_name = "my_function_to_fix_ast"
        new_code = "def my_function_to_fix_ast():\n    return 'fixed by ast'"

        result = await self.code_service.modify_code(
            context="SELF_FIX_AST",
            modification_instruction="N/A for AST fix, but param exists", # Not used by this context directly
            module_path=module_path,
            function_name=function_name,
            additional_context={"new_code_string": new_code}
        )

        self.assertEqual(result["status"], "SUCCESS_CODE_APPLIED_AST")
        self.assertEqual(result["modified_code_string"], new_code)
        self.assertIsNone(result["error"])
        self.mock_self_mod_service.edit_function_source_code.assert_called_once_with(module_path, function_name, new_code)
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected COMPLETED_SUCCESSFULLY for SELF_FIX_AST success."
        )

    async def test_modify_code_self_fix_ast_missing_details(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        new_code = "def test_func(): pass"

        # Test missing module_path
        result_no_module = await self.code_service.modify_code(
            context="SELF_FIX_AST", modification_instruction="", function_name="f", additional_context={"new_code_string": new_code}
        )
        self.assertEqual(result_no_module["status"], "ERROR_MISSING_DETAILS")
        self.assertIn("Missing module_path", result_no_module["error"])
        self.mock_task_manager.add_task.assert_called_once() # Task added before detail check
        self.mock_task_manager.update_task_status.assert_called_with(self.mock_task.task_id, self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW, reason=mock.ANY, step_desc=mock.ANY)

        # Test missing function_name
        self.mock_task_manager.reset_mock() # Reset for next call
        self.mock_task_manager.add_task.return_value = self.mock_task
        result_no_func = await self.code_service.modify_code(
            context="SELF_FIX_AST", modification_instruction="", module_path="m.py", additional_context={"new_code_string": new_code}
        )
        self.assertEqual(result_no_func["status"], "ERROR_MISSING_DETAILS")
        self.assertIn("Missing function_name", result_no_func["error"])

    async def test_modify_code_self_fix_ast_missing_new_code_string(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        result = await self.code_service.modify_code(
            context="SELF_FIX_AST",
            modification_instruction="",
            module_path="m.py",
            function_name="f",
            additional_context={} # Missing new_code_string
        )
        self.assertEqual(result["status"], "ERROR_MISSING_NEW_CODE_STRING")
        self.assertIn("'new_code_string' not provided", result["error"])
        self.mock_task_manager.add_task.assert_called_once()
        self.mock_task_manager.update_task_status.assert_called_with(self.mock_task.task_id, self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW, reason=mock.ANY, step_desc=mock.ANY)


    async def test_modify_code_self_fix_ast_self_mod_service_missing(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        new_code = "def test_func(): pass"
        result = await self.code_service_no_self_mod.modify_code(
            context="SELF_FIX_AST",
            modification_instruction="",
            module_path="m.py",
            function_name="f",
            additional_context={"new_code_string": new_code}
        )
        self.assertEqual(result["status"], "ERROR_SELF_MOD_SERVICE_MISSING")
        self.assertIn("Self modification service not configured", result["error"])
        self.mock_task_manager.add_task.assert_called_once() # TaskManager is part of code_service_no_self_mod
        self.mock_task_manager.update_task_status.assert_called_with(self.mock_task.task_id, self_modification.ActiveTaskStatus.FAILED_PRE_REVIEW, reason=mock.ANY, step_desc=mock.ANY)


    async def test_modify_code_self_fix_ast_apply_raises_exception(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        self.mock_self_mod_service.edit_function_source_code.side_effect = Exception("AST edit failed spectacularly")

        module_path = "my_module.py"
        function_name = "my_function_to_fail_ast"
        new_code = "def my_function_to_fail_ast():\n    return 'this will fail'"

        result = await self.code_service.modify_code(
            context="SELF_FIX_AST",
            modification_instruction="",
            module_path=module_path,
            function_name=function_name,
            additional_context={"new_code_string": new_code}
        )

        self.assertEqual(result["status"], "ERROR_APPLYING_AST_FIX")
        self.assertIsNone(result["modified_code_string"])
        self.assertIn("AST edit failed spectacularly", result["error"])
        self.mock_self_mod_service.edit_function_source_code.assert_called_once_with(module_path, function_name, new_code)
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_DURING_APPLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ),
            "Expected FAILED_DURING_APPLY for SELF_FIX_AST apply exception."
        )


    async def test_generate_code_new_tool_with_llm_config_override(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task

        expected_metadata = {"suggested_function_name": "custom_tool", "suggested_tool_name": "customTool", "suggested_description": "A custom tool."}
        metadata_json_str = json.dumps(expected_metadata)
        expected_code_content = "def custom_tool():\n    pass"
        llm_output = f"# METADATA: {metadata_json_str}\n{expected_code_content}"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = llm_output

        custom_llm_config = {
            "model_name": "custom_model_for_new_tool",
            "temperature": 0.99,
            "max_tokens": 1000
        }

        result = await self.code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool with custom LLM config.",
            llm_config=custom_llm_config
        )

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        _, kwargs = self.mock_llm_provider.invoke_ollama_model_async.call_args
        self.assertEqual(kwargs.get("model_name"), custom_llm_config["model_name"])
        self.assertEqual(kwargs.get("temperature"), custom_llm_config["temperature"])
        self.assertEqual(kwargs.get("max_tokens"), custom_llm_config["max_tokens"])
        self.mock_task_manager.add_task.assert_called_once()

    async def test_modify_code_self_fix_tool_with_llm_config_override(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        self.mock_self_mod_service.get_function_source_code.return_value = "def old_func(a): return a"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "def old_func(a): return a + 1 # Fixed by custom LLM"

        custom_llm_config = {
            "model_name": "custom_model_for_fix",
            "temperature": 0.01,
            "max_tokens": 500
        }

        result = await self.code_service.modify_code(
            context="SELF_FIX_TOOL",
            existing_code=None,
            modification_instruction="Fix the bug with custom LLM config.",
            module_path="dummy.module",
            function_name="old_func",
            llm_config=custom_llm_config
        )

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        _, kwargs = self.mock_llm_provider.invoke_ollama_model_async.call_args
        self.assertEqual(kwargs.get("model_name"), custom_llm_config["model_name"])
        self.assertEqual(kwargs.get("temperature"), custom_llm_config["temperature"])
        self.assertEqual(kwargs.get("max_tokens"), custom_llm_config["max_tokens"])
        self.mock_task_manager.add_task.assert_called_once()

    # --- Tests for review_code ---
    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_success_with_llm_and_linter_findings(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task

        sample_code = "def my_func(x):\n  print(x)\n  return x*2"
        linter_findings = ["LINT: Line 2, unused variable 'y'"]
        mock_run_linter.return_value = (linter_findings, None)

        llm_review_output = {
            "overall_summary": "Code has minor issues.",
            "suggestions": [{"line_start": 1, "line_end": 1, "severity": CodeReviewSeverity.MINOR.value, "comment": "Consider type hints."}]
        }
        self.mock_llm_provider.invoke_ollama_model_async.return_value = json.dumps(llm_review_output)

        result = await self.code_service.review_code(sample_code, review_type="general", review_context="TEST_CONTEXT")

        self.assertEqual(result["status"], "SUCCESS_REVIEW_COMPLETED")
        self.assertEqual(result["review_summary"], llm_review_output["overall_summary"])
        self.assertEqual(len(result["llm_suggestions"]), 1)
        self.assertEqual(result["llm_suggestions"][0]["comment"], llm_review_output["suggestions"][0]["comment"])
        self.assertEqual(result["llm_suggestions"][0]["severity"], CodeReviewSeverity.MINOR.value)
        self.assertEqual(result["linter_findings"], linter_findings)
        self.assertIsNone(result["error"])

        mock_run_linter.assert_called_once_with(sample_code)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        prompt_args, _ = self.mock_llm_provider.invoke_ollama_model_async.call_args
        self.assertIn(sample_code, prompt_args[0])
        self.assertIn("TEST_CONTEXT", prompt_args[0]) # Check review_context in prompt

        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ), "Expected COMPLETED_SUCCESSFULLY for review_code success."
        )

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_security_type_uses_correct_prompt(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code = "def some_secure_code():\n    eval('print(1)') # Obvious issue for security prompt"
        mock_run_linter.return_value = ([], None)
        llm_review_output = {"overall_summary": "Security concerns found.", "suggestions": [{"line_start": 2, "line_end": 2, "severity": CodeReviewSeverity.CRITICAL.value, "comment": "Use of eval is dangerous."}]}
        self.mock_llm_provider.invoke_ollama_model_async.return_value = json.dumps(llm_review_output)

        result = await self.code_service.review_code(sample_code, review_type="security")

        self.assertEqual(result["status"], "SUCCESS_REVIEW_COMPLETED")
        self.assertEqual(result["llm_suggestions"][0]["severity"], CodeReviewSeverity.CRITICAL.value)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        prompt_args, _ = self.mock_llm_provider.invoke_ollama_model_async.call_args
        # Check for a unique phrase from the security prompt
        self.assertIn("specifically for potential security vulnerabilities", prompt_args[0])
        self.assertNotIn("refactoring and improving its structure", prompt_args[0]) # Ensure it's not refactor prompt
        self.assertNotIn("Focus on:\n- Correctness", prompt_args[0]) # Ensure it's not general prompt's detailed list
        self.mock_task_manager.add_task.assert_called_once()


    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_refactoring_type_uses_correct_prompt(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code = "def long_function():\n    # ... many lines ...\n    pass"
        mock_run_linter.return_value = ([], None)
        llm_review_output = {"overall_summary": "Refactoring opportunities present.", "suggestions": [{"line_start": 1, "line_end": 3, "severity": CodeReviewSeverity.MAJOR.value, "comment": "Function is too long."}]} # Using MAJOR for "Medium"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = json.dumps(llm_review_output)

        result = await self.code_service.review_code(sample_code, review_type="refactoring")

        self.assertEqual(result["status"], "SUCCESS_REVIEW_COMPLETED")
        self.assertEqual(result["llm_suggestions"][0]["severity"], CodeReviewSeverity.MAJOR.value)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        prompt_args, _ = self.mock_llm_provider.invoke_ollama_model_async.call_args
        # Check for a unique phrase from the refactoring prompt
        self.assertIn("refactoring and improving its structure", prompt_args[0])
        self.assertNotIn("specifically for potential security vulnerabilities", prompt_args[0]) # Ensure it's not security prompt
        self.mock_task_manager.add_task.assert_called_once()

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_llm_suggestions_no_linter_findings(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code = "def clean_func():\n    return True"
        mock_run_linter.return_value = ([], None) # No linter findings

        llm_review_output = {
            "overall_summary": "One minor suggestion.",
            "suggestions": [{"line_start": 1, "line_end": 1, "severity": CodeReviewSeverity.STYLE.value, "comment": "Add a docstring."}]
        }
        self.mock_llm_provider.invoke_ollama_model_async.return_value = json.dumps(llm_review_output)

        result = await self.code_service.review_code(sample_code)

        self.assertEqual(result["status"], "SUCCESS_REVIEW_COMPLETED")
        self.assertEqual(result["review_summary"], llm_review_output["overall_summary"])
        self.assertEqual(len(result["llm_suggestions"]), 1)
        self.assertEqual(result["llm_suggestions"][0]["severity"], CodeReviewSeverity.STYLE.value)
        self.assertEqual(result["linter_findings"], [])
        self.assertIsNone(result["error"])
        mock_run_linter.assert_called_once_with(sample_code)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        self.mock_task_manager.add_task.assert_called_once()

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_linter_findings_no_llm_suggestions(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code = "x = y + 1 # unused"
        linter_findings = ["LINT: x is unused"]
        mock_run_linter.return_value = (linter_findings, None)

        llm_review_output = {
            "overall_summary": "Code looks generally good.",
            "suggestions": [] # No specific suggestions from LLM, so no severity to check here
        }
        self.mock_llm_provider.invoke_ollama_model_async.return_value = json.dumps(llm_review_output)

        result = await self.code_service.review_code(sample_code)

        self.assertEqual(result["status"], "SUCCESS_REVIEW_COMPLETED")
        self.assertEqual(result["review_summary"], llm_review_output["overall_summary"])
        self.assertEqual(result["llm_suggestions"], [])
        self.assertEqual(result["linter_findings"], linter_findings)
        self.assertIsNone(result["error"])
        mock_run_linter.assert_called_once_with(sample_code)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        self.mock_task_manager.add_task.assert_called_once()

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_llm_returns_non_json(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code = "def my_func(): return 1"
        mock_run_linter.return_value = ([], None) # Linter is fine
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "This is not JSON. Just plain text."

        result = await self.code_service.review_code(sample_code)

        self.assertEqual(result["status"], "ERROR_LLM_REVIEW_FAILED")
        self.assertIsNotNone(result["error"])
        self.assertIn("LLM review output was not valid JSON", result["error"])
        # Check if raw output is stored as a fallback suggestion
        self.assertTrue(len(result["llm_suggestions"]) == 1)
        self.assertIn("LLM Raw Output (JSON Parse Failed)", result["llm_suggestions"][0]["comment"])
        self.assertIn("This is not JSON", result["llm_suggestions"][0]["comment"])
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.FAILED_UNKNOWN and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ), "Expected FAILED_UNKNOWN for LLM non-JSON response."
        )

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_llm_returns_empty_response(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code = "def my_func(): return 1"
        mock_run_linter.return_value = ([], None)
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "" # Empty response

        result = await self.code_service.review_code(sample_code)

        self.assertEqual(result["status"], "ERROR_LLM_REVIEW_FAILED")
        self.assertIsNotNone(result["error"])
        self.assertIn("LLM returned no response for review", result["error"])
        self.assertEqual(result["llm_suggestions"], []) # No fallback suggestion for completely empty
        self.mock_task_manager.add_task.assert_called_once()

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_linter_execution_fails(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code = "def my_func(): return 1"
        linter_error_message = "Ruff crashed!"
        mock_run_linter.return_value = (["Some old message if any"], linter_error_message) # Linter execution error

        # LLM part will still run
        llm_review_output = {"overall_summary": "Code seems fine.", "suggestions": []}
        self.mock_llm_provider.invoke_ollama_model_async.return_value = json.dumps(llm_review_output)

        result = await self.code_service.review_code(sample_code)

        # Even if linter fails, LLM review can proceed. Status depends on LLM part in this case.
        self.assertEqual(result["status"], "SUCCESS_REVIEW_COMPLETED")
        self.assertIn(f"Linter execution error: {linter_error_message}", result["logs"])
        self.assertEqual(result["linter_findings"], ["Some old message if any"]) # Previous messages might still be there
        self.assertEqual(result["review_summary"], llm_review_output["overall_summary"])
        self.mock_task_manager.add_task.assert_called_once()

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_llm_provider_none(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code = "def my_code(): pass"
        linter_findings = ["LINT: Something from linter"]
        mock_run_linter.return_value = (linter_findings, None)

        # Use code_service_no_llm which has llm_provider=None
        result = await self.code_service_no_llm.review_code(sample_code)

        self.assertEqual(result["status"], "SUCCESS_REVIEW_COMPLETED_LINTER_ONLY")
        self.assertEqual(result["linter_findings"], linter_findings)
        self.assertEqual(result["llm_suggestions"], []) # No LLM suggestions
        self.assertIsNone(result["review_summary"])   # No LLM summary
        self.assertIn("LLM provider missing, only linter review performed", result["error"])
        mock_run_linter.assert_called_once_with(sample_code)
        self.mock_llm_provider.invoke_ollama_model_async.assert_not_called() # Ensure LLM not called
        self.mock_task_manager.add_task.assert_called_once()
        self.assertTrue(
            any(
                call.args[1] == self_modification.ActiveTaskStatus.COMPLETED_SUCCESSFULLY and call.args[0] == self.mock_task.task_id
                for call in self.mock_task_manager.update_task_status.call_args_list
            ), "Expected COMPLETED_SUCCESSFULLY for LINTER_ONLY review."
        )

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_with_llm_config_override(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code = "def my_func(): pass"
        mock_run_linter.return_value = ([], None)
        llm_review_output = {"overall_summary": "Custom review.", "suggestions": []}
        self.mock_llm_provider.invoke_ollama_model_async.return_value = json.dumps(llm_review_output)

        custom_llm_config = {
            "model_name": "custom_review_model",
            "temperature": 0.88,
            "max_tokens": 1500
        }
        result = await self.code_service.review_code(sample_code, llm_config=custom_llm_config)

        self.assertEqual(result["status"], "SUCCESS_REVIEW_COMPLETED")
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        _, kwargs = self.mock_llm_provider.invoke_ollama_model_async.call_args
        self.assertEqual(kwargs.get("model_name"), custom_llm_config["model_name"])
        self.assertEqual(kwargs.get("temperature"), custom_llm_config["temperature"])
        self.assertEqual(kwargs.get("max_tokens"), custom_llm_config["max_tokens"])
        self.mock_task_manager.add_task.assert_called_once()

    @patch.object(CodeService, '_run_linter', new_callable=AsyncMock)
    async def test_review_code_refactoring_with_suggested_replacement(self, mock_run_linter):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task
        sample_code = "def func_name_too_long():\n    x=1 # simple var rename candidate"
        mock_run_linter.return_value = ([], None)

        llm_review_output = {
            "overall_summary": "Minor refactoring suggestions.",
            "suggestions": [
                {
                    "line_start": 2, "line_end": 2,
                    "severity": CodeReviewSeverity.STYLE.value,
                    "comment": "Variable 'x' could be 'item_count'.",
                    "suggested_replacement_code": "item_count = 1"
                },
                {
                    "line_start": 1, "line_end": 1,
                    "severity": CodeReviewSeverity.MINOR.value,
                    "comment": "Function name is a bit verbose."
                    # No suggested_replacement_code for this one
                }
            ]
        }
        self.mock_llm_provider.invoke_ollama_model_async.return_value = json.dumps(llm_review_output)

        result = await self.code_service.review_code(sample_code, review_type="refactoring")

        self.assertEqual(result["status"], "SUCCESS_REVIEW_COMPLETED")
        self.assertEqual(len(result["llm_suggestions"]), 2)

        suggestion1 = result["llm_suggestions"][0]
        self.assertEqual(suggestion1["comment"], "Variable 'x' could be 'item_count'.")
        self.assertEqual(suggestion1["suggested_replacement_code"], "item_count = 1")
        self.assertEqual(suggestion1["severity"], CodeReviewSeverity.STYLE.value)

        suggestion2 = result["llm_suggestions"][1]
        self.assertEqual(suggestion2["comment"], "Function name is a bit verbose.")
        self.assertNotIn("suggested_replacement_code", suggestion2) # Check it's absent
        self.assertEqual(suggestion2["severity"], CodeReviewSeverity.MINOR.value)

        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        prompt_args, _ = self.mock_llm_provider.invoke_ollama_model_async.call_args
        self.assertIn("refactoring and improving its structure", prompt_args[0]) # Correct prompt
        self.mock_task_manager.add_task.assert_called_once()

    async def test_modify_code_granular_refactor_direct_replacement_instruction(self):
        self.mock_task_manager.reset_mock()
        self.mock_task_manager.add_task.return_value = self.mock_task

        original_code = "def my_func(a):\n    print('old line')\n    # some other code\n    return a * 2"
        section_to_replace_str = "print('old line')" # This is what section_identifier would be
        suggested_replacement_snippet = "print('new line by direct instruction')"

        # This instruction mimics what the self-correction loop would generate
        prescriptive_instruction = f"Replace the identified code section ('{section_to_replace_str[:20]}...') with the following code: ```\n{suggested_replacement_snippet}\n```"

        # Mock LLM to return the original function but with the section_to_replace_str replaced by suggested_replacement_snippet
        expected_modified_full_code = original_code.replace(section_to_replace_str, suggested_replacement_snippet)
        self.mock_llm_provider.invoke_ollama_model_async.return_value = expected_modified_full_code

        result = await self.code_service.modify_code(
            context="GRANULAR_CODE_REFACTOR",
            modification_instruction=prescriptive_instruction,
            existing_code=original_code,
            module_path="test_direct_replace.py",
            function_name="my_func",
            additional_context={"section_identifier": section_to_replace_str}
        )

        self.assertEqual(result["status"], "SUCCESS_CODE_GENERATED")
        self.assertEqual(result["modified_code_string"], expected_modified_full_code)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        prompt_arg = self.mock_llm_provider.invoke_ollama_model_async.call_args[0][0]
        self.assertIn(original_code, prompt_arg)
        self.assertIn(section_to_replace_str, prompt_arg) # Check section_identifier is in prompt
        self.assertIn("Replace the identified code section", prompt_arg) # Check prescriptive instruction part
        self.assertIn(suggested_replacement_snippet, prompt_arg) # Check replacement code is in prompt
        self.mock_task_manager.add_task.assert_called_once()


    def test_assemble_components_empty_outline(self):
        outline = {}
        details = {}
        result_code = self.code_service._assemble_components(outline, details)
        self.assertEqual(result_code.strip(), "")

    def test_assemble_components_outline_with_only_imports(self):
        outline = {"imports": ["os", "sys"]}
        details = {}
        result_code = self.code_service._assemble_components(outline, details)
        expected_code = "import os\nimport sys"
        self.assertEqual(result_code.strip(), expected_code.strip())

    def test_assemble_components_outline_with_only_main_block(self):
        outline = {"main_execution_block": "if __name__ == '__main__':\n    print('Hello')"}
        details = {}
        result_code = self.code_service._assemble_components(outline, details)
        expected_code = "if __name__ == '__main__':\n    print('Hello')"
        self.assertEqual(result_code.strip(), expected_code.strip())

    def test_assemble_components_class_with_no_methods_or_attributes(self):
        outline = {
            "components": [{
                "type": "class", "name": "EmptyClass", "description": "An empty class."
            }]
        }
        details = {}
        result_code = self.code_service._assemble_components(outline, details)
        expected_code = """class EmptyClass:
    \"\"\"An empty class.\"\"\"
    pass"""
        self.assertEqual(result_code.strip(), expected_code.strip())

    def test_assemble_components_class_attributes_no_init(self):
        outline = {
            "components": [{
                "type": "class", "name": "AttrsNoInit",
                "attributes": [
                    {"name": "attr1", "type": "int", "description": "First attribute."},
                    {"name": "attr2", "type": "str"}
                ],
                "methods": [] # No __init__ method
            }]
        }
        details = {}
        result_code = self.code_service._assemble_components(outline, details)
        expected_code = """class AttrsNoInit:
    # Defined attributes (from outline):
    # attr1: int # First attribute.
    # attr2: str
    pass""" # Pass is added if class body would be empty
        self.assertEqual(result_code.strip(), expected_code.strip())


if __name__ == '__main__': # pragma: no cover
    # This custom runner will execute sync tests via standard unittest mechanisms
    # and then gather and run async tests using an asyncio event loop.
    suite_sync = unittest.TestSuite()
    sync_tests_found = False
    async_test_methods_names = []

    for name in dir(TestCodeService):
        if name.startswith("test_"):
            method = getattr(TestCodeService, name)
            if asyncio.iscoroutinefunction(method):
                async_test_methods_names.append(name)
            else:
                suite_sync.addTest(TestCodeService(name))
                sync_tests_found = True

    if sync_tests_found:
        print("Running synchronous tests...")
        runner_sync = unittest.TextTestRunner()
        runner_sync.run(suite_sync)

    if async_test_methods_names:
        print("\nRunning asynchronous tests...")
        loop = asyncio.get_event_loop_policy().new_event_loop()
        asyncio.set_event_loop(loop)

        async def run_specific_async_tests():
            test_instance = TestCodeService()
            # Call setUp for the instance
            if hasattr(test_instance, 'setUp'):
                 test_instance.setUp()

            tasks = []
            for name in async_test_methods_names:
                method_to_run = getattr(test_instance, name)
                # Check if the method is already bound or needs binding
                # For instance methods, getattr directly gives a bound method
                if asyncio.iscoroutinefunction(method_to_run):
                     tasks.append(method_to_run())
                else: # Should not happen with this filtering
                     print(f"Warning: {name} is not a coroutine function, skipping.")

            await asyncio.gather(*tasks)

            # Call tearDown for the instance
            if hasattr(test_instance, 'tearDown'):
                test_instance.tearDown()

        try:
            loop.run_until_complete(run_specific_async_tests())
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    if not sync_tests_found and not async_test_methods_names:
        print("No tests found.")
