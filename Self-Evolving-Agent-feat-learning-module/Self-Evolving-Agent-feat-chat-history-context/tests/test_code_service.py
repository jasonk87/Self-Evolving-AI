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
    from ai_assistant.code_services.service import CodeService
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

        self.code_service = CodeService(
            llm_provider=self.mock_llm_provider,
            self_modification_service=self.mock_self_mod_service
        )
        # Store a version of code_service with None providers for specific tests
        self.code_service_no_llm = CodeService(llm_provider=None, self_modification_service=self.mock_self_mod_service)
        self.code_service_no_self_mod = CodeService(llm_provider=self.mock_llm_provider, self_modification_service=None)


    # --- Tests for generate_code (NEW_TOOL context) ---
    async def test_generate_code_new_tool_success_no_save(self): # RENAMED, target_path=None
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

    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_new_tool_success_and_save(self, mock_write_to_file):
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

    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_new_tool_save_fails(self, mock_write_to_file):
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

    async def test_generate_code_new_tool_llm_no_code(self):
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "" # Empty response

        result = await self.code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool."
        )
        self.assertEqual(result["status"], "ERROR_LLM_NO_CODE")
        self.assertIsNone(result["code_string"])
        self.assertIsNotNone(result["error"])

    async def test_generate_code_new_tool_missing_metadata_line(self):
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

    async def test_generate_code_new_tool_malformed_metadata_json(self):
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

    async def test_generate_code_new_tool_metadata_ok_no_code_block(self):
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

    async def test_generate_code_unsupported_context_for_generate(self):
        # This test doesn't involve LLM provider, so can use any CS instance
        result = await self.code_service.generate_code(
            context="SELF_FIX_TOOL",
            prompt_or_description="A test tool."
        )
        self.assertEqual(result["status"], "ERROR_UNSUPPORTED_CONTEXT")
        self.assertIsNone(result["code_string"])

    async def test_generate_code_llm_provider_missing(self):
        result = await self.code_service_no_llm.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A tool."
        )
        self.assertEqual(result["status"], "ERROR_LLM_PROVIDER_MISSING")
        self.assertIsNone(result["code_string"])
        self.assertIn("LLM provider not configured", result["error"])

    # --- Tests for modify_code (focused on SELF_FIX_TOOL context) ---
    async def test_modify_code_self_fix_tool_success(self):
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

    async def test_modify_code_self_fix_tool_no_original_code(self):
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

    async def test_modify_code_self_fix_tool_llm_no_suggestion(self):
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

    async def test_modify_code_self_fix_tool_llm_empty_response(self):
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

    async def test_modify_code_unsupported_context(self):
        result = await self.code_service.modify_code(
            context="UNKNOWN_CONTEXT",
            modification_instruction="Do something.",
            existing_code="code"
        )
        self.assertEqual(result["status"], "ERROR_UNSUPPORTED_CONTEXT")

    async def test_modify_code_missing_details_for_self_fix(self):
        result = await self.code_service.modify_code(
            context="SELF_FIX_TOOL",
            modification_instruction="Fix it.",
            existing_code="code",
            module_path=None,
            function_name="some_func"
        )
        self.assertEqual(result["status"], "ERROR_MISSING_DETAILS")

    async def test_modify_code_llm_provider_missing(self):
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


    async def test_modify_code_self_mod_service_missing(self):
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

    # --- Tests for generate_code (GENERATE_UNIT_TEST_SCAFFOLD context) ---
    async def test_generate_code_unit_test_scaffold_success_no_save(self): # RENAMED
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

    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_unit_test_scaffold_success_and_save(self, mock_write_to_file):
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

    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_unit_test_scaffold_save_fails(self, mock_write_to_file):
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

    async def test_generate_code_unit_test_scaffold_llm_no_code(self):
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

    async def test_generate_code_unit_test_scaffold_llm_returns_none(self):
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

    async def test_generate_code_unit_test_scaffold_cleaning_applied(self):
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

    # --- Tests for generate_code (EXPERIMENTAL_HIERARCHICAL_OUTLINE context) ---
    async def test_generate_code_hierarchical_outline_success(self):
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

    async def test_generate_code_hierarchical_outline_llm_empty(self):
        self.mock_llm_provider.invoke_ollama_model_async.return_value = ""

        result = await self.code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_OUTLINE",
            prompt_or_description="A simple test module."
        )
        self.assertEqual(result["status"], "ERROR_LLM_NO_OUTLINE")
        self.assertIsNone(result["parsed_outline"])

    async def test_generate_code_hierarchical_outline_bad_json(self):
        self.mock_llm_provider.invoke_ollama_model_async.return_value = "{'bad_json': not_quoted}" # Malformed JSON

        result = await self.code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_OUTLINE",
            prompt_or_description="A simple test module."
        )
        self.assertEqual(result["status"], "ERROR_OUTLINE_PARSING")
        self.assertIsNone(result["parsed_outline"])
        self.assertIsNotNone(result["error"])

    # --- Tests for _generate_detail_for_component ---
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
        self.assertIn("my_util_func", prompt_arg)
        self.assertIn("Return True if path exists", prompt_arg)
        self.assertIn("import os", prompt_arg)

    async def test_generate_detail_for_component_success_method(self):
        component_def = {
            "type": "method", "name": "process", "signature": "(self, data: dict) -> None",
            "description": "Processes data.", "body_placeholder": "self.some_attr = data.get('key')"
        }
        full_outline = {
            "module_name": "my_class_module.py",
            "components": [{
                "type": "class", "name": "MyProcessor",
                "attributes": [{"name": "some_attr", "type": "Optional[Any]"}],
                "description": "A data processor class.",
                "methods": [component_def]
            }],
            "imports": []
        }
        expected_code = "def process(self, data: dict) -> None:\n    self.some_attr = data.get('key')"
        self.mock_llm_provider.invoke_ollama_model_async.return_value = expected_code

        result_code = await self.code_service._generate_detail_for_component(
            component_definition=component_def,
            full_outline=full_outline,
            llm_config=None
        )
        self.assertEqual(result_code, expected_code)
        self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        prompt_arg = self.mock_llm_provider.invoke_ollama_model_async.call_args[0][0]
        self.assertIn("class 'MyProcessor'", prompt_arg)
        self.assertIn("self.some_attr = data.get('key')", prompt_arg)

    async def test_generate_detail_for_component_llm_returns_none(self):
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
        detail_for_method_a = "def method_a(self):\n    pass # method_a_impl"

        async def mock_recursive_generate_code(*args, **kwargs):
            if kwargs.get('context') == "EXPERIMENTAL_HIERARCHICAL_OUTLINE":
                return outline_gen_success_return
            return {"status": "ERROR_UNEXPECTED_RECURSIVE_CALL"} # pragma: no cover

        async def mock_detail_gen(*args, **kwargs):
            component_def = args[0]
            if component_def["name"] == "func_one":
                return detail_for_func_one
            elif component_def["name"] == "method_a":
                return detail_for_method_a
            return None # pragma: no cover

        with mock.patch.object(self.code_service, 'generate_code', side_effect=mock_recursive_generate_code) as mock_outline_call, \
             mock.patch.object(self.code_service, '_generate_detail_for_component', side_effect=mock_detail_gen) as mock_detail_call:

            result = await self.code_service.generate_code(
                context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL",
                prompt_or_description="A complex tool."
            )

            self.assertEqual(result["status"], "SUCCESS_HIERARCHICAL_DETAILS_GENERATED")
            self.assertEqual(result["parsed_outline"], mock_outline)
            self.assertIsNone(result["code_string"])

            expected_component_details = {
                "func_one": detail_for_func_one,
                "MyClass.method_a": detail_for_method_a
            }
            self.assertEqual(result["component_details"], expected_component_details)

            mock_outline_call.assert_called_once_with(
                context="EXPERIMENTAL_HIERARCHICAL_OUTLINE",
                prompt_or_description="A complex tool.",
                language="python",
                llm_config=None,
                additional_context=None
            )

            self.assertEqual(mock_detail_call.call_count, 2)
            # Ensure component definitions are passed correctly
            # Note: Direct comparison of dicts passed to mocks can be tricky if order or extra keys differ.
            # It's often better to assert specific key-value pairs or types if full dict match is problematic.
            # For this test, we assume the component_def dictionaries are passed as is from mock_outline.
            mock_detail_call.assert_any_call(mock_outline["components"][0], mock_outline, None) # func_one
            mock_detail_call.assert_any_call(mock_outline["components"][1]["methods"][0], mock_outline, None) # MyClass.method_a


    async def test_generate_code_hierarchical_full_tool_outline_fails(self):
        outline_gen_failure_return = {
            "status": "ERROR_OUTLINE_PARSING", "parsed_outline": None,
            "outline_str": "{bad json", "logs": ["Failed parsing"], "error": "JSON error"
        }

        async def mock_recursive_generate_code_fail_outline(*args, **kwargs):
            if kwargs.get('context') == "EXPERIMENTAL_HIERARCHICAL_OUTLINE":
                return outline_gen_failure_return
            return {"status": "ERROR_UNEXPECTED_RECURSIVE_CALL"} # pragma: no cover

        with mock.patch.object(self.code_service, 'generate_code', side_effect=mock_recursive_generate_code_fail_outline) as mock_outline_call:
            result = await self.code_service.generate_code(
                context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL",
                prompt_or_description="A complex tool."
            )
            self.assertEqual(result["status"], "ERROR_OUTLINE_PARSING")
            self.assertIsNone(result["component_details"])
            mock_outline_call.assert_called_once()


    async def test_generate_code_hierarchical_full_tool_one_detail_fails(self):
        mock_outline = { # Simplified outline for this test
            "module_name": "test_tool.py",
            "components": [
                {"type": "function", "name": "func_one", "signature": "()", "description": "d1", "body_placeholder": "p1"},
                {"type": "function", "name": "func_two", "signature": "()", "description": "d2", "body_placeholder": "p2"}
            ]
        }
        outline_gen_success_return = {"status": "SUCCESS_OUTLINE_GENERATED", "parsed_outline": mock_outline, "logs": []}

        detail_for_func_one = "def func_one(): pass"

        async def mock_recursive_generate_code_for_partial(*args, **kwargs):
            if kwargs.get('context') == "EXPERIMENTAL_HIERARCHICAL_OUTLINE":
                return outline_gen_success_return
            return {"status": "ERROR_UNEXPECTED_RECURSIVE_CALL"} # pragma: no cover

        async def mock_detail_gen_partial_fail(*args, **kwargs):
            component_def = args[0]
            if component_def["name"] == "func_one":
                return detail_for_func_one
            elif component_def["name"] == "func_two":
                return None
            return None # pragma: no cover

        with mock.patch.object(self.code_service, 'generate_code', side_effect=mock_recursive_generate_code_for_partial), \
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
            self.assertIsNotNone(result["error"])
            self.assertEqual(mock_detail_call.call_count, 2)

    # --- Tests for generate_code (HIERARCHICAL_GEN_COMPLETE_TOOL context) ---
    async def test_generate_code_hierarchical_complete_tool_success_no_save(self): # RENAMED
        mock_outline = {"module_name": "tool.py", "imports": ["os"], "components": [{"type": "function", "name": "my_func"}]}
        mock_component_details = {"my_func": "def my_func():\n    print('done')"}
        # This expected code should be what _assemble_components produces based on the mocks above
        expected_assembled_code = self.code_service._assemble_components(mock_outline, mock_component_details)


        orchestration_success_return = {
            "status": "SUCCESS_HIERARCHICAL_DETAILS_GENERATED",
            "parsed_outline": mock_outline,
            "component_details": mock_component_details,
            "logs": [], "error": None
        }

        async def mock_recursive_generate_code(*args, **kwargs):
            if kwargs.get('context') == "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL":
                return orchestration_success_return
            raise AssertionError(f"Unexpected recursive call to generate_code with context: {kwargs.get('context')}") # pragma: no cover

        # For this test, we let _assemble_components run, so no need to mock it.
        with mock.patch.object(self.code_service, 'generate_code', side_effect=mock_recursive_generate_code) as mock_inner_gen_code_call:
            result = await self.code_service.generate_code(
                context="HIERARCHICAL_GEN_COMPLETE_TOOL",
                prompt_or_description="A complex tool requiring assembly.",
                target_path=None # Explicitly no save
            )

            self.assertEqual(result["status"], "SUCCESS_HIERARCHICAL_ASSEMBLED")
            self.assertEqual(result["code_string"].strip(), expected_assembled_code.strip()) # Compare stripped
            self.assertEqual(result["parsed_outline"], mock_outline)
            self.assertEqual(result["component_details"], mock_component_details)
            self.assertIsNone(result.get("saved_to_path"))

            mock_inner_gen_code_call.assert_called_once_with(
                context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL",
                prompt_or_description="A complex tool requiring assembly.",
                language="python",
                llm_config=None,
                additional_context=None
            )

    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_hierarchical_complete_tool_success_and_save(self, mock_write_to_file):
        mock_outline = {"module_name": "tool.py", "imports": ["os"], "components": [{"type": "function", "name": "my_func"}]}
        mock_component_details = {"my_func": "def my_func():\n    print('done')"}
        # Let _assemble_components run to get the code that would be saved
        expected_assembled_code = self.code_service._assemble_components(mock_outline, mock_component_details)
        mock_write_to_file.return_value = True

        orchestration_success_return = {
            "status": "SUCCESS_HIERARCHICAL_DETAILS_GENERATED",
            "parsed_outline": mock_outline,
            "component_details": mock_component_details,
            "logs": [], "error": None
        }

        async def mock_recursive_generate_code(*args, **kwargs):
            if kwargs.get('context') == "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL":
                return orchestration_success_return
            raise AssertionError(f"Unexpected recursive call to generate_code with context: {kwargs.get('context')}") # pragma: no cover

        with mock.patch.object(self.code_service, 'generate_code', side_effect=mock_recursive_generate_code) as mock_inner_gen_code_call:
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

    @mock.patch('ai_assistant.code_services.service.write_to_file')
    async def test_generate_code_hierarchical_complete_tool_save_fails(self, mock_write_to_file):
        mock_outline = {"module_name": "tool.py", "imports": ["os"], "components": [{"type": "function", "name": "my_func"}]}
        mock_component_details = {"my_func": "def my_func():\n    print('done')"}
        expected_assembled_code = self.code_service._assemble_components(mock_outline, mock_component_details) # Let it run
        mock_write_to_file.return_value = False # Simulate save failure

        orchestration_success_return = {
            "status": "SUCCESS_HIERARCHICAL_DETAILS_GENERATED",
            "parsed_outline": mock_outline,
            "component_details": mock_component_details,
            "logs": [], "error": None
        }

        async def mock_recursive_generate_code(*args, **kwargs):
            if kwargs.get('context') == "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL":
                return orchestration_success_return
            raise AssertionError(f"Unexpected recursive call to generate_code with context: {kwargs.get('context')}") # pragma: no cover

        with mock.patch.object(self.code_service, 'generate_code', side_effect=mock_recursive_generate_code) as mock_inner_gen_code_call:
            test_target_path = "output/hierarchical_tool_fail_save.py"
            result = await self.code_service.generate_code(
                context="HIERARCHICAL_GEN_COMPLETE_TOOL",
                prompt_or_description="A complex tool, save fails.",
                target_path=test_target_path
            )

            self.assertEqual(result["status"], "ERROR_SAVING_ASSEMBLED_CODE")
            self.assertEqual(result["code_string"].strip(), expected_assembled_code.strip()) # Code still generated
            self.assertIsNone(result["saved_to_path"])
            self.assertIsNotNone(result["error"])
            self.assertIn("failed to save", result["error"])
            mock_write_to_file.assert_called_once_with(test_target_path, expected_assembled_code)

    async def test_generate_code_hierarchical_complete_tool_orchestration_fails(self):
        orchestration_failure_return = {
            "status": "ERROR_OUTLINE_PARSING", "parsed_outline": None,
            "component_details": None, "logs": ["Failed parsing"], "error": "JSON error"
        }

        async def mock_recursive_generate_code_fail(*args, **kwargs):
            if kwargs.get('context') == "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL":
                return orchestration_failure_return
            raise AssertionError("Unexpected recursive call") # pragma: no cover

        with mock.patch.object(self.code_service, 'generate_code', side_effect=mock_recursive_generate_code_fail) as mock_inner_gen_code_call, \
             mock.patch.object(self.code_service, '_assemble_components') as mock_assemble:

            result = await self.code_service.generate_code(
                context="HIERARCHICAL_GEN_COMPLETE_TOOL",
                prompt_or_description="A complex tool."
            )

            self.assertEqual(result["status"], "ERROR_OUTLINE_PARSING")
            self.assertIsNone(result["code_string"])
            mock_inner_gen_code_call.assert_called_once()
            mock_assemble.assert_not_called()

    async def test_generate_code_hierarchical_complete_tool_assembly_fails(self):
        mock_outline = {"components": []}
        mock_component_details = {}
        orchestration_success_return = {
            "status": "SUCCESS_HIERARCHICAL_DETAILS_GENERATED",
            "parsed_outline": mock_outline,
            "component_details": mock_component_details,
            "logs": [], "error": None # Ensure error is None for this path
        }

        async def mock_recursive_generate_code_for_assembly_fail(*args, **kwargs):
            if kwargs.get('context') == "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL":
                return orchestration_success_return
            raise AssertionError("Unexpected recursive call") # pragma: no cover

        with mock.patch.object(self.code_service, 'generate_code', side_effect=mock_recursive_generate_code_for_assembly_fail), \
             mock.patch.object(self.code_service, '_assemble_components', side_effect=Exception("Assembly crashed!")) as mock_assemble:

            result = await self.code_service.generate_code(
                context="HIERARCHICAL_GEN_COMPLETE_TOOL",
                prompt_or_description="A complex tool."
            )

            self.assertEqual(result["status"], "ERROR_ASSEMBLY_FAILED")
            self.assertIsNone(result["code_string"])
            self.assertIn("Assembly crashed!", result.get("error", ""))
            mock_assemble.assert_called_once()


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
