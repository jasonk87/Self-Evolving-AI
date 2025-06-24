import unittest
from unittest.mock import patch, mock_open, MagicMock, AsyncMock
import asyncio
import os
import sys
import ast # Import ast for creating AST nodes if needed for more advanced mocking

# Ensure the 'ai_assistant' module can be imported
try:
    from ai_assistant.core.self_modification import edit_function_source_code, get_function_source_code
    # Import other necessary items from self_modification if they are directly used by tests
except ImportError: # pragma: no cover
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core.self_modification import edit_function_source_code, get_function_source_code

class TestSelfModificationWithReview(unittest.TestCase):
    def setUp(self):
        self.module_path = "ai_assistant.dummy_module"
        self.function_name = "dummy_function"
        self.new_code_string = "def dummy_function():\n    print('new version')"
        # Use a more realistic project_root_path for tests if files are actually written
        # For fully mocked tests, this can be a placeholder.
        # If using tempfile, create a temp dir here.
        self.project_root_path = "/fake/project/root"
        self.file_path = os.path.join(self.project_root_path, *self.module_path.split('.'), ".py")
        self.change_description = "Test change: Updated print statement."
        self.original_code = "def dummy_function():\n    print('old version')"
        self.full_original_file_content = f"{self.original_code}\n\ndef another_function():\n    pass"

    # Patch order is important: from bottom up or use specific `patch` targets.
    @patch('ai_assistant.core.self_modification.asyncio.run') # Innermost call mocked by asyncio.run
    @patch('ai_assistant.core.self_modification.CriticalReviewCoordinator.request_critical_review', new_callable=AsyncMock) # This is now directly on the class
    @patch('ai_assistant.core.self_modification.generate_diff')
    @patch('ai_assistant.core.self_modification.get_function_source_code')
    @patch('builtins.open', new_callable=mock_open) # Mocks open for read and write
    @patch('ai_assistant.core.self_modification.shutil.copy2')
    @patch('ai_assistant.core.self_modification.os.path.exists')
    @patch('ai_assistant.core.self_modification.ast.parse') # To control AST parsing
    @patch('ai_assistant.core.self_modification.ast.unparse') # To control AST unparsing
    def test_edit_function_approved(self, mock_ast_unparse, mock_ast_parse, mock_os_exists, mock_shutil_copy,
                                    mock_file_open_builtin, mock_get_func_code, mock_generate_diff,
                                    mock_request_review, mock_asyncio_run):

        mock_os_exists.return_value = True # Assume file exists
        mock_get_func_code.return_value = self.original_code
        mock_generate_diff.return_value = "--- a/...\n+++ b/..." # Non-empty diff

        # Mock what asyncio.run(coordinator.request_critical_review(...)) would return
        mock_asyncio_run.return_value = (True, [{"status": "approved", "comments": "LGTM!"}])

        # Mock the reading of the full original file for AST modification
        mock_file_open_builtin.return_value.read.return_value = self.full_original_file_content

        # Mock AST parsing and unparsing
        # Create a dummy AST node for the original parsed file
        mock_original_ast_obj = MagicMock(spec=ast.Module)
        mock_original_ast_obj.body = [MagicMock(spec=ast.FunctionDef, name=self.function_name)] # Simplified
        mock_ast_parse.side_effect = [
            mock_original_ast_obj, # First call for original_source
            MagicMock(spec=ast.Module, body=[MagicMock(spec=ast.FunctionDef, name=self.function_name)]) # Call for new_code_string
        ]
        mock_ast_unparse.return_value = self.new_code_string # What the final file content will be

        result = edit_function_source_code(
            self.module_path, self.function_name, self.new_code_string,
            self.project_root_path, self.change_description
        )

        mock_get_func_code.assert_called_once_with(self.module_path, self.function_name)
        mock_generate_diff.assert_called_once_with(self.original_code, self.new_code_string, file_name=f"{self.module_path}/{self.function_name}")
        mock_asyncio_run.assert_called_once() # asyncio.run was called for the review

        # Check that the coordinator's method was called within asyncio.run
        # The first argument to asyncio.run is the coroutine, so we check its details
        self.assertTrue(mock_asyncio_run.call_args[0][0].__qualname__.endswith('CriticalReviewCoordinator.request_critical_review'))

        mock_shutil_copy.assert_called_once() # Backup created
        # Check for file read and then file write
        mock_file_open_builtin.assert_any_call(self.file_path, 'r', encoding='utf-8')
        mock_file_open_builtin.assert_any_call(self.file_path, 'w', encoding='utf-8')
        # Ensure the mocked ast.unparse result was written
        mock_file_open_builtin().write.assert_called_once_with(self.new_code_string)
        self.assertIn("success", result.lower())

    @patch('ai_assistant.core.self_modification.os.path.exists')
    @patch('ai_assistant.core.self_modification.shutil.copy2')
    @patch('builtins.open', new_callable=mock_open, read_data="original content") # For reading original_source
    @patch('ai_assistant.core.self_modification.get_function_source_code')
    @patch('ai_assistant.core.self_modification.generate_diff')
    @patch('ai_assistant.core.self_modification.CriticalReviewCoordinator.request_critical_review', new_callable=AsyncMock)
    @patch('ai_assistant.core.self_modification.asyncio.run')
    def test_edit_function_rejected(self, mock_asyncio_run, mock_request_review_method,
                                   mock_generate_diff, mock_get_func_code,
                                   mock_file_open_builtin, mock_shutil_copy, mock_os_exists):
        mock_os_exists.return_value = True
        mock_get_func_code.return_value = self.original_code
        mock_generate_diff.return_value = "--- a/...\n+++ b/..."
        mock_asyncio_run.return_value = (False, [{"status": "rejected", "comments": "Needs major rework"}])

        result = edit_function_source_code(
            self.module_path, self.function_name, self.new_code_string,
            self.project_root_path, self.change_description
        )

        mock_get_func_code.assert_called_once()
        mock_generate_diff.assert_called_once()
        mock_asyncio_run.assert_called_once()
        mock_shutil_copy.assert_not_called() # No backup if review fails

        # Check that file was not opened for writing
        write_called = any(
            call_args[0][0] == self.file_path and call_args[0][1] == 'w'
            for call_args in mock_file_open_builtin.call_args_list
        )
        self.assertFalse(write_called, "File should not have been opened for writing on review rejection.")
        self.assertIn("rejected by critical review", result.lower())

    @patch('ai_assistant.core.self_modification.get_function_source_code')
    @patch('ai_assistant.core.self_modification.generate_diff')
    @patch('ai_assistant.core.self_modification.asyncio.run') # Mock asyncio.run itself
    def test_edit_function_no_diff(self, mock_asyncio_run, mock_generate_diff, mock_get_func_code):
        mock_get_func_code.return_value = self.new_code_string # Original is same as new
        mock_generate_diff.return_value = "" # No diff

        result = edit_function_source_code(
            self.module_path, self.function_name, self.new_code_string,
            self.project_root_path, self.change_description
        )

        mock_get_func_code.assert_called_once()
        mock_generate_diff.assert_called_once()
        mock_asyncio_run.assert_not_called() # Review process (via asyncio.run) should not be called
        self.assertIn("no changes detected", result.lower())

    @patch('ai_assistant.core.self_modification.get_function_source_code')
    def test_edit_function_original_code_not_found(self, mock_get_func_code):
        mock_get_func_code.return_value = None # Simulate function not found

        result = edit_function_source_code(
            self.module_path, "non_existent_function", self.new_code_string,
            self.project_root_path, "Trying to edit non-existent function"
        )
        self.assertIn("could not retrieve original source code", result.lower())
        mock_get_func_code.assert_called_once_with(self.module_path, "non_existent_function")

# Basic runner for unittest.TestCase with async methods
# This is a simplified runner. For more complex scenarios, consider pytest-asyncio or similar.
def run_async_tests(test_case_class): # pragma: no cover
    loop = asyncio.get_event_loop_policy().new_event_loop()
    asyncio.set_event_loop(loop)
    suite = unittest.TestSuite()

    # Collect async test methods
    async_tests_found = False
    for name in dir(test_case_class):
        if name.startswith("test_") and asyncio.iscoroutinefunction(getattr(test_case_class, name)):
            suite.addTest(test_case_class(name)) # This will need a special runner or approach
            async_tests_found = True

    if not async_tests_found:
        print(f"No async tests found in {test_case_class.__name__}")
        return

    # This simplified runner doesn't properly handle async setUp/tearDown with TestSuite.
    # For proper execution, each async test is often run individually or with a specialized runner.
    # The __main__ block below demonstrates a more direct way for this specific file.

if __name__ == '__main__': # pragma: no cover
    # This is a more direct way to run async tests within a unittest.TestCase
    # for this specific file, if run directly.

    # Discover and run synchronous tests first
    suite_sync = unittest.TestSuite()
    sync_tests_found = False
    async_test_methods_names = []

    for name in dir(TestSelfModificationWithReview):
        if name.startswith("test_"):
            method = getattr(TestSelfModificationWithReview, name)
            if asyncio.iscoroutinefunction(method):
                async_test_methods_names.append(name)
            else:
                suite_sync.addTest(TestSelfModificationWithReview(name))
                sync_tests_found = True

    if sync_tests_found:
        print("Running synchronous tests for TestSelfModificationWithReview...")
        runner_sync = unittest.TextTestRunner()
        runner_sync.run(suite_sync)

    if async_test_methods_names:
        print("\nRunning asynchronous tests for TestSelfModificationWithReview...")
        loop = asyncio.get_event_loop_policy().new_event_loop()
        asyncio.set_event_loop(loop)
        test_instance = TestSelfModificationWithReview()

        async def run_all_async_on_instance():
            if hasattr(test_instance, 'setUp'):
                test_instance.setUp()
            try:
                for name in async_test_methods_names:
                    print(f"Running async test: {name}")
                    await getattr(test_instance, name)()
            finally:
                if hasattr(test_instance, 'tearDown'):
                    test_instance.tearDown()

        try:
            loop.run_until_complete(run_all_async_on_instance())
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    if not sync_tests_found and not async_test_methods_names:
        print("No tests found for TestSelfModificationWithReview.")
    else:
        print("\nFinished TestSelfModificationWithReview tests.")
