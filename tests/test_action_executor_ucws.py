import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio
import sys
import os

# Ensure the project root is in sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.code_synthesis.service import CodeSynthesisService
from ai_assistant.code_synthesis.data_structures import CodeTaskResult, CodeTaskStatus

class TestActionExecutorUCWS(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Mock dependencies for ActionExecutor
        self.mock_learning_agent = MagicMock()
        self.mock_task_manager = MagicMock()
        self.mock_notification_manager = MagicMock()

        # We need to patch the imports inside ActionExecutor.__init__ or just let them be,
        # but the critical part is patching the CodeSynthesisService usage.

        # However, CodeSynthesisService is not yet used in ActionExecutor.
        # This test is to verify the integration I am ABOUT to implement.
        pass

    @patch('ai_assistant.execution.action_executor.CodeSynthesisService')
    @patch('ai_assistant.execution.action_executor.self_modification')
    @patch('ai_assistant.execution.action_executor.global_reflection_log')
    async def test_propose_tool_modification_uses_ucws(self, mock_reflection_log, mock_self_modification, MockCodeSynthesisService):
        # Setup
        executor = ActionExecutor(
            learning_agent=self.mock_learning_agent,
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager
        )

        # Verify CodeSynthesisService was initialized
        MockCodeSynthesisService.assert_called_once()
        mock_ucws_service = MockCodeSynthesisService.return_value
        executor.code_synthesis_service = mock_ucws_service # In case I name it differently, but plan is code_synthesis_service

        # Mock UCWS response
        mock_code = "def fixed_function():\n    pass"
        mock_result = CodeTaskResult(
            request_id="test_req",
            status=CodeTaskStatus.SUCCESS,
            generated_code=mock_code
        )
        mock_ucws_service.submit_task = AsyncMock(return_value=mock_result)

        # Mock self_modification and post_mod_test
        mock_self_modification.edit_function_source_code = AsyncMock(return_value="success")
        mock_self_modification.get_backup_function_source_code = MagicMock(return_value="def original(): pass")
        executor._run_post_modification_test = AsyncMock(return_value=(True, "Test passed"))

        # Action data
        action_details = {
            "module_path": "some.module",
            "function_name": "target_function",
            "tool_name": "targetTool",
            "suggested_change_description": "Fix the bug",
            # suggested_code_change is MISSING, triggering UCWS
            "original_reflection_entry_id": "some_entry_id"
        }
        proposed_action = {
            "action_type": "PROPOSE_TOOL_MODIFICATION",
            "source_insight_id": "insight_123",
            "details": action_details
        }

        # Execute
        result = await executor.execute_action(proposed_action)

        # Verify
        self.assertTrue(result)

        # Check if UCWS was called
        mock_ucws_service.submit_task.assert_called_once()
        call_args = mock_ucws_service.submit_task.call_args[0][0] # The CodeTaskRequest object
        self.assertEqual(call_args.task_type.name, "EXISTING_TOOL_SELF_FIX_LLM")
        self.assertEqual(call_args.context_data["module_path"], "some.module")
        self.assertEqual(call_args.context_data["function_name"], "target_function")
        self.assertEqual(call_args.context_data["problem_description"], "Fix the bug")

        # Check if modification was applied using the code from UCWS
        mock_self_modification.edit_function_source_code.assert_called_with(
            module_path="some.module",
            function_name="target_function",
            new_code_string=mock_code,
            project_root_path=unittest.mock.ANY,
            change_description="Fix the bug",
            task_manager=self.mock_task_manager,
            parent_task_id=unittest.mock.ANY
        )

if __name__ == '__main__':
    unittest.main()
