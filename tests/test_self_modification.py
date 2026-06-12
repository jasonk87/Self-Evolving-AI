import unittest
from unittest.mock import patch, mock_open, AsyncMock
import asyncio
import os
import sys

# Ensure the 'ai_assistant' module can be imported
try:
    from ai_assistant.core.self_modification import edit_function_source_code
except ImportError: # pragma: no cover
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core.self_modification import edit_function_source_code

class TestSelfModificationWithReview(unittest.TestCase):
    def setUp(self):
        self.module_path = "ai_assistant.custom_tools.dummy_module"
        self.function_name = "dummy_function"
        self.new_code_string = "def dummy_function():\n    print('new version')"
        self.project_root_path = os.path.abspath("/fake/project/root")
        self.file_path = os.path.join(
            self.project_root_path,
            *self.module_path.split('.'),
        ) + ".py"
        self.change_description = "Test change: Updated print statement."
        self.original_code = "def dummy_function():\n    print('old version')"
        self.full_original_file_content = f"{self.original_code}\n\ndef another_function():\n    pass"

    @patch('ai_assistant.core.self_modification._run_pylint_check') # Mock pylint to avoid subprocess
    @patch('ai_assistant.core.self_modification.SandboxManager.execute_test')
    @patch('ai_assistant.core.self_modification.invoke_ollama_model_async', new_callable=AsyncMock)
    @patch('ai_assistant.core.self_modification.RefinementAgent')
    @patch('ai_assistant.core.self_modification._resolve_file_path_robust')
    @patch('ai_assistant.core.self_modification.CriticalReviewCoordinator.request_critical_review', new_callable=AsyncMock)
    @patch('ai_assistant.core.self_modification.generate_diff')
    @patch('ai_assistant.core.self_modification.get_function_source_code')
    @patch('builtins.open', new_callable=mock_open)
    @patch('ai_assistant.core.self_modification.shutil.copy2')
    @patch('ai_assistant.core.self_modification.os.path.exists')
    def test_edit_function_approved(self, mock_os_exists, mock_shutil_copy,
                                    mock_file_open_builtin, mock_get_func_code, mock_generate_diff,
                                    mock_request_review, mock_resolve_file_path, mock_refinement_agent,
                                    mock_generate_sandbox_test, mock_execute_sandbox,
                                    mock_run_pylint):

        # Setup Mocks
        mock_resolve_file_path.return_value = self.file_path
        mock_os_exists.return_value = True
        mock_get_func_code.return_value = self.original_code
        mock_generate_diff.return_value = "--- a/...\n+++ b/..."

        # Pylint passes
        mock_run_pylint.return_value = None

        mock_request_review.return_value = (True, [{"status": "approved", "comments": "LGTM!"}])
        mock_generate_sandbox_test.return_value = "def test_generated():\n    assert True"
        mock_execute_sandbox.return_value = (True, "passed", "")
        mock_file_open_builtin.return_value.read.return_value = self.full_original_file_content

        # Config RefinementAgent (even if not used on approved path, good practice)
        mock_refinement_agent.return_value.refine_code = AsyncMock()

        # Execute
        result = asyncio.run(edit_function_source_code(
            self.module_path, self.function_name, self.new_code_string,
            self.project_root_path, self.change_description
        ))

        # Assertions
        mock_get_func_code.assert_called_once_with(self.module_path, self.function_name)
        mock_request_review.assert_called_once()
        mock_shutil_copy.assert_called_once()
        self.assertIn("success", result.lower())

    @patch('ai_assistant.core.self_modification._run_pylint_check')
    @patch('ai_assistant.core.self_modification.RefinementAgent')
    @patch('ai_assistant.core.self_modification._resolve_file_path_robust')
    @patch('ai_assistant.core.self_modification.os.path.exists')
    @patch('ai_assistant.core.self_modification.shutil.copy2')
    @patch('builtins.open', new_callable=mock_open, read_data="original content")
    @patch('ai_assistant.core.self_modification.get_function_source_code')
    @patch('ai_assistant.core.self_modification.generate_diff')
    @patch('ai_assistant.core.self_modification.CriticalReviewCoordinator.request_critical_review', new_callable=AsyncMock)
    def test_edit_function_rejected(self, mock_request_review_method, mock_generate_diff,
                                   mock_get_func_code, mock_file_open_builtin,
                                   mock_shutil_copy, mock_os_exists, mock_resolve_file_path,
                                   mock_refinement_agent, mock_run_pylint):

        mock_resolve_file_path.return_value = self.file_path
        mock_os_exists.return_value = True
        mock_get_func_code.return_value = self.original_code
        mock_generate_diff.return_value = "--- a/...\n+++ b/..."
        # Initial review rejects it
        mock_request_review_method.return_value = (False, [{"status": "rejected", "comments": "Needs major rework"}])

        # Pylint passes
        mock_run_pylint.return_value = None

        # Provide valid python content for AST parse
        mock_file_open_builtin.return_value.read.return_value = self.full_original_file_content

        # Mock RefinementAgent
        mock_refiner_instance = mock_refinement_agent.return_value
        # Refinement returns None or empty to stop the loop
        mock_refiner_instance.refine_code = AsyncMock(return_value=None)

        result = asyncio.run(edit_function_source_code(
            self.module_path, self.function_name, self.new_code_string,
            self.project_root_path, self.change_description
        ))

        mock_get_func_code.assert_called_once()
        mock_request_review_method.assert_called()
        mock_shutil_copy.assert_not_called()
        self.assertIn("rejected", result.lower())

    @patch('ai_assistant.core.self_modification._resolve_file_path_robust')
    @patch('ai_assistant.core.self_modification.os.path.exists')
    @patch('ai_assistant.core.self_modification.get_function_source_code')
    @patch('ai_assistant.core.self_modification.generate_diff')
    @patch('builtins.open', new_callable=mock_open, read_data="original content")
    def test_edit_function_no_diff(self, mock_file_open, mock_generate_diff, mock_get_func_code, mock_os_exists, mock_resolve_file_path):
        mock_resolve_file_path.return_value = self.file_path
        mock_os_exists.return_value = True
        mock_get_func_code.return_value = self.new_code_string
        mock_generate_diff.return_value = ""
        mock_file_open.return_value.read.return_value = self.full_original_file_content

        result = asyncio.run(edit_function_source_code(
            self.module_path, self.function_name, self.new_code_string,
            self.project_root_path, self.change_description
        ))

        mock_get_func_code.assert_called_once()
        mock_generate_diff.assert_called_once()
        self.assertIn("no changes detected", result.lower())

    @patch('ai_assistant.core.self_modification._resolve_file_path_robust')
    @patch('ai_assistant.core.self_modification.os.path.exists')
    @patch('ai_assistant.core.self_modification.get_function_source_code')
    @patch('builtins.open', new_callable=mock_open, read_data="original content")
    def test_edit_function_original_code_not_found(self, mock_file_open, mock_get_func_code, mock_os_exists, mock_resolve_file_path):
        mock_resolve_file_path.return_value = self.file_path
        mock_os_exists.return_value = True
        mock_get_func_code.return_value = None
        mock_file_open.return_value.read.return_value = self.full_original_file_content

        result = asyncio.run(edit_function_source_code(
            self.module_path, "non_existent_function", self.new_code_string,
            self.project_root_path, "Trying to edit non-existent function"
        ))
        self.assertIn("could not retrieve original source code", result.lower())
        mock_get_func_code.assert_called_once_with(self.module_path, "non_existent_function")

    @patch('ai_assistant.core.self_modification._resolve_file_path_robust')
    @patch('builtins.open', new_callable=mock_open)
    def test_core_source_requires_human_approval(self, mock_file_open, mock_resolve_file_path):
        core_module = "ai_assistant.core.self_modification"
        core_file = os.path.join(
            self.project_root_path,
            "ai_assistant",
            "core",
            "self_modification.py",
        )
        mock_resolve_file_path.return_value = core_file

        result = asyncio.run(edit_function_source_code(
            core_module,
            "edit_function_source_code",
            "def edit_function_source_code():\n    pass",
            self.project_root_path,
            "Autonomous core source change should be blocked.",
        ))

        self.assertIn("human approval required", result.lower())
        mock_file_open.assert_not_called()

if __name__ == '__main__':
    unittest.main()
