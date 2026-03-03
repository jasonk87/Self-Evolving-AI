import unittest
from unittest.mock import patch, MagicMock, mock_open
import subprocess # Required for subprocess.CompletedProcess and subprocess.TimeoutExpired
import os
import sys

# Add project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path: # pragma: no cover
    sys.path.insert(0, project_root)

from ai_assistant.custom_tools.code_execution_tools import execute_sandboxed_python_script

class TestExecuteSandboxedPythonScript(unittest.TestCase):

    @patch('subprocess.run')
    @patch('tempfile.TemporaryDirectory')
    def test_successful_execution(self, mock_temp_dir, mock_subprocess_run):
        # Mock TemporaryDirectory to control the path
        mock_temp_dir_path = "/tmp/test_exec_dir"
        mock_temp_dir.return_value.__enter__.return_value = mock_temp_dir_path

        mock_subprocess_run.return_value = subprocess.CompletedProcess(
            args=['python', '-I', '-s', '-S', 'main_script.py'],
            returncode=0,
            stdout="Hello from script",
            stderr=""
        )

        script_content = "print('Hello from script')"
        result = execute_sandboxed_python_script(script_content)

        self.assertEqual(result['status'], "success")
        self.assertEqual(result['return_code'], 0)
        self.assertEqual(result['stdout'], "Hello from script")
        self.assertEqual(result['stderr'], "")
        self.assertIsNone(result['error_message'])
        # Check that subprocess.run was called with the script in the temp dir
        expected_script_path = os.path.join(mock_temp_dir_path, "main_script.py")
        mock_subprocess_run.assert_called_once_with(
            ['python', '-I', '-s', '-S', 'main_script.py'],
            capture_output=True, text=True, timeout=10, cwd=mock_temp_dir_path, check=False
        )
        self.assertEqual(result['executed_script_path'], expected_script_path)


    @patch('subprocess.run')
    @patch('tempfile.TemporaryDirectory')
    def test_execution_with_error_return_code(self, mock_temp_dir, mock_subprocess_run):
        mock_temp_dir.return_value.__enter__.return_value = "/tmp/test_exec_dir_error"
        mock_subprocess_run.return_value = subprocess.CompletedProcess(
            args=['python', '-I', '-s', '-S', 'main_script.py'],
            returncode=1,
            stdout="Output before error",
            stderr="Script error occurred"
        )

        script_content = "import sys; sys.exit(1)"
        result = execute_sandboxed_python_script(script_content)

        self.assertEqual(result['status'], "error")
        self.assertEqual(result['return_code'], 1)
        self.assertEqual(result['stdout'], "Output before error")
        self.assertEqual(result['stderr'], "Script error occurred")
        self.assertEqual(result['error_message'], "Script error occurred")

    @patch('subprocess.run')
    @patch('tempfile.TemporaryDirectory')
    def test_execution_timeout(self, mock_temp_dir, mock_subprocess_run):
        mock_temp_dir.return_value.__enter__.return_value = "/tmp/test_exec_dir_timeout"
        mock_subprocess_run.side_effect = subprocess.TimeoutExpired(cmd="python main_script.py", timeout=5)

        script_content = "import time; time.sleep(10)"
        result = execute_sandboxed_python_script(script_content, timeout_seconds=5)

        self.assertEqual(result['status'], "timeout")
        self.assertEqual(result['return_code'], -1)
        self.assertEqual(result['stdout'], "")
        self.assertIn("timed out after 5 seconds", result['stderr'])
        self.assertIn("timed out after 5 seconds", result['error_message'])

    @patch('subprocess.run')
    @patch('tempfile.TemporaryDirectory')
    def test_python_interpreter_not_found(self, mock_temp_dir, mock_subprocess_run):
        mock_temp_dir.return_value.__enter__.return_value = "/tmp/test_exec_dir_notfound"
        mock_subprocess_run.side_effect = FileNotFoundError("python_custom_path not found")

        script_content = "print('test')"
        result = execute_sandboxed_python_script(script_content, python_executable="python_custom_path")

        self.assertEqual(result['status'], "error")
        self.assertEqual(result['return_code'], -1)
        self.assertIn("Python interpreter 'python_custom_path' not found", result['stderr'])
        self.assertIn("Python interpreter 'python_custom_path' not found", result['error_message'])

    def test_no_script_content(self):
        result = execute_sandboxed_python_script("")
        self.assertEqual(result['status'], "error")
        self.assertEqual(result['error_message'], "No script content provided.")
        self.assertEqual(result['return_code'], -1)

    def test_invalid_input_filename_path_traversal(self):
        script_content = "print('attempting path traversal')"
        input_files = {"../sensitive_file.txt": "content"}
        result = execute_sandboxed_python_script(script_content, input_files=input_files)
        self.assertEqual(result['status'], "error")
        self.assertIn("Invalid input filename", result['error_message'])

    @patch('builtins.open', new_callable=mock_open) # Mock open for file operations
    @patch('os.path.exists', return_value=True)
    @patch('os.path.isfile', return_value=True)
    @patch('subprocess.run')
    @patch('tempfile.TemporaryDirectory')
    def test_output_file_handling(self, mock_temp_dir, mock_subprocess_run, mock_isfile, mock_exists, mock_file_open):
        mock_temp_dir_path = "/tmp/test_output_files"
        mock_temp_dir.return_value.__enter__.return_value = mock_temp_dir_path

        # Simulate subprocess run successfully
        mock_subprocess_run.return_value = subprocess.CompletedProcess(
            args=['python', '-I', '-s', '-S', 'main_script.py'], returncode=0, stdout="Script ran", stderr=""
        )

        # Simulate content of the output file
        mock_file_open.return_value.read.return_value = "Content of output_file1.txt"

        script_content = "print('testing output files')"
        output_filenames = ["output_file1.txt", "non_existent.txt"]

        result = execute_sandboxed_python_script(
            script_content,
            output_filenames=output_filenames
        )

        self.assertEqual(result['status'], "success")
        self.assertIn("output_file1.txt", result['output_files'])
        self.assertEqual(result['output_files']['output_file1.txt'], "Content of output_file1.txt")

        # Check that open was called for the expected output file path
        expected_output_file_path = os.path.join(mock_temp_dir_path, "output_file1.txt")
        # This assertion needs to be more robust if other open calls happen (e.g. for the script itself)
        # For simplicity here, we assume it's one of the calls.
        # mock_file_open.assert_any_call(expected_output_file_path, 'r', encoding='utf-8')

        # Check warning for non-existent file in stderr
        self.assertIn("Requested output file 'non_existent.txt' not found", result['stderr'])

    @patch('subprocess.run')
    @patch('tempfile.TemporaryDirectory')
    def test_error_message_when_stderr_is_empty_but_return_code_is_not_zero(self, mock_temp_dir, mock_subprocess_run):
        mock_temp_dir.return_value.__enter__.return_value = "/tmp/test_exec_dir_no_stderr"
        mock_subprocess_run.return_value = subprocess.CompletedProcess(
            args=['python', '-I', '-s', '-S', 'main_script.py'],
            returncode=5, # Non-zero return code
            stdout="Process finished",
            stderr="" # Empty stderr
        )

        script_content = "import sys; sys.exit(5)"
        result = execute_sandboxed_python_script(script_content)

        self.assertEqual(result['status'], "error")
        self.assertEqual(result['return_code'], 5)
        self.assertEqual(result['stdout'], "Process finished")
        self.assertEqual(result['stderr'], "") # Stderr is indeed empty
        self.assertEqual(result['error_message'], "Script exited with code 5 but no stderr.")


if __name__ == '__main__': # pragma: no cover
    unittest.main()
