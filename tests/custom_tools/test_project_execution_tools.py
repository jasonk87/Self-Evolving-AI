import unittest
from unittest.mock import patch, MagicMock, call # Added call
import os
import sys
import json

# Add project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path: # pragma: no cover
    sys.path.insert(0, project_root)

from ai_assistant.custom_tools.project_execution_tools import execute_project_plan
# execute_sandboxed_python_script will be mocked where it's called.

class TestExecuteProjectPlan(unittest.TestCase):

    def _create_mock_sandbox_result(self, status="success", stdout="", stderr="", return_code=0, output_files=None, error_message=None):
        return {
            "status": status,
            "stdout": stdout,
            "stderr": stderr,
            "return_code": return_code,
            "output_files": output_files or {},
            "error_message": error_message
        }

    @patch('ai_assistant.custom_tools.project_execution_tools.execute_sandboxed_python_script')
    def test_successful_plan_execution_mixed_steps(self, mock_sandbox_exec):
        mock_sandbox_exec.return_value = self._create_mock_sandbox_result(stdout="Script output")

        project_plan = [
            {"step_id": "1", "type": "informational", "description": "Info step", "details": {"message": "Starting up"}},
            {"step_id": "2", "type": "python_script", "description": "Run script", "details": {"script_content": "print('ok')"}},
            {"step_id": "3", "type": "human_review_gate", "description": "User review", "details": {"prompt_to_user": "Proceed?"}}
        ]
        result = execute_project_plan(project_plan, "TestProject")

        self.assertEqual(result["overall_status"], "success")
        self.assertEqual(result["num_steps_processed"], 3)
        self.assertEqual(result["step_results"][0]["status"], "success")
        self.assertEqual(result["step_results"][0]["output"], "Starting up")
        self.assertEqual(result["step_results"][1]["status"], "success")
        self.assertEqual(result["step_results"][1]["output"]["stdout"], "Script output")
        self.assertEqual(result["step_results"][2]["status"], "simulated_approved")
        mock_sandbox_exec.assert_called_once()

    @patch('ai_assistant.custom_tools.project_execution_tools.execute_sandboxed_python_script')
    def test_plan_with_python_script_success(self, mock_sandbox_exec):
        mock_sandbox_exec.return_value = self._create_mock_sandbox_result(
            stdout="Python success", stderr="Some warning", return_code=0, output_files={"data.txt": "content"}
        )
        project_plan = [{"step_id": "s1", "type": "python_script", "description": "Do stuff", "details": {"script_content": "print('hello')"}}]
        result = execute_project_plan(project_plan)

        self.assertEqual(result["overall_status"], "success")
        self.assertEqual(result["step_results"][0]["status"], "success")
        step_output = result["step_results"][0]["output"]
        self.assertEqual(step_output["stdout"], "Python success")
        self.assertEqual(step_output["stderr"], "Some warning")
        self.assertEqual(step_output["return_code"], 0)
        self.assertEqual(step_output["output_files"], {"data.txt": "content"})
        mock_sandbox_exec.assert_called_once()

    @patch('ai_assistant.custom_tools.project_execution_tools.execute_sandboxed_python_script')
    def test_plan_with_python_script_failure(self, mock_sandbox_exec):
        mock_sandbox_exec.return_value = self._create_mock_sandbox_result(
            status="error", stdout="Trying...", stderr="Syntax Error!", return_code=1, error_message="Syntax Error!"
        )
        project_plan = [{"step_id": "s1", "type": "python_script", "description": "Failing script", "details": {"script_content": "fail please"}}]
        result = execute_project_plan(project_plan)

        self.assertEqual(result["overall_status"], "failed")
        self.assertEqual(result["step_results"][0]["status"], "error")
        step_output = result["step_results"][0]["output"]
        self.assertEqual(step_output["stderr"], "Syntax Error!")
        self.assertEqual(step_output["return_code"], 1)
        self.assertEqual(step_output["error_message_from_sandbox"], "Syntax Error!")
        mock_sandbox_exec.assert_called_once()

    @patch('ai_assistant.custom_tools.project_execution_tools.execute_sandboxed_python_script')
    def test_plan_with_python_script_timeout(self, mock_sandbox_exec):
        mock_sandbox_exec.return_value = self._create_mock_sandbox_result(
            status="timeout", stderr="Timed out", return_code=-1, error_message="Timed out"
        )
        project_plan = [{"step_id": "s1", "type": "python_script", "description": "Timeout script", "details": {"script_content": "time.sleep(100)"}}]
        result = execute_project_plan(project_plan)

        self.assertEqual(result["overall_status"], "failed")
        self.assertEqual(result["step_results"][0]["status"], "timeout")
        step_output = result["step_results"][0]["output"]
        self.assertIn("Timed out", step_output["stderr"]) # Or error_message_from_sandbox
        self.assertEqual(step_output["error_message_from_sandbox"], "Timed out")
        mock_sandbox_exec.assert_called_once()

    @patch('ai_assistant.custom_tools.project_execution_tools.execute_sandboxed_python_script')
    def test_plan_stops_on_script_failure(self, mock_sandbox_exec):
        mock_sandbox_exec.return_value = self._create_mock_sandbox_result(status="error", return_code=1, stderr="Failure")
        project_plan = [
            {"step_id": "1", "type": "python_script", "description": "Failing script", "details": {"script_content": "fail"}},
            {"step_id": "2", "type": "informational", "description": "Should not run", "details": {"message": "Info"}}
        ]
        result = execute_project_plan(project_plan)

        self.assertEqual(result["overall_status"], "failed")
        self.assertEqual(result["num_steps_processed"], 1) # Stops after first failing step
        self.assertEqual(result["step_results"][0]["status"], "error")
        # The second step is not in step_results because the loop breaks
        mock_sandbox_exec.assert_called_once()


    def test_plan_with_unknown_step_type(self):
        project_plan = [{"step_id": "s1", "type": "magical_mystery_tour", "description": "Unknown step"}]
        result = execute_project_plan(project_plan)

        self.assertEqual(result["overall_status"], "failed") # Because unknown type causes failure
        self.assertEqual(result["step_results"][0]["status"], "failed_unknown_type")
        self.assertIn("Unknown step type: magical_mystery_tour", result["step_results"][0]["output"])

    def test_plan_with_missing_script_content(self):
        project_plan = [{"step_id": "s1", "type": "python_script", "description": "No content script", "details": {}}] # Missing script_content
        result = execute_project_plan(project_plan)

        self.assertEqual(result["overall_status"], "failed") # Misconfigured step causes overall failure
        self.assertEqual(result["step_results"][0]["status"], "error_misconfigured")
        self.assertIn("Missing script_content", result["step_results"][0]["output"])

    def test_empty_project_plan(self):
        result = execute_project_plan([])
        self.assertEqual(result["overall_status"], "error") # Changed from success/no_action to error as per implementation
        self.assertEqual(result["num_steps_processed"], 0) # num_steps_processed is not added for empty plan error
        self.assertIn("No project plan provided", result["error_message"])


    def test_plan_with_only_informational_and_review_steps(self):
        project_plan = [
            {"step_id": "1", "type": "informational", "description": "Info 1", "details": {"message": "First message"}},
            {"step_id": "2", "type": "human_review_gate", "description": "Review 1", "details": {"prompt_to_user": "Review this."}},
            {"step_id": "3", "type": "informational", "description": "Info 2", "details": {"message": "Second message"}}
        ]
        result = execute_project_plan(project_plan)

        self.assertEqual(result["overall_status"], "success")
        self.assertEqual(result["num_steps_processed"], 3)
        self.assertEqual(result["step_results"][0]["status"], "success")
        self.assertEqual(result["step_results"][1]["status"], "simulated_approved")
        self.assertEqual(result["step_results"][2]["status"], "success")

if __name__ == '__main__': # pragma: no cover
    unittest.main()
