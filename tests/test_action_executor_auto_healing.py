import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from ai_assistant.execution.action_executor import ActionExecutor

class TestActionExecutorAutoHealing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.executor = ActionExecutor(MagicMock())
        self.executor.code_service = AsyncMock()
        self.executor.task_manager = MagicMock() # Use MagicMock for synchronous method update_task_status

    @patch('ai_assistant.execution.action_executor.global_reflection_log.log_execution')
    @patch('ai_assistant.execution.action_executor.self_modification.edit_function_source_code', new_callable=AsyncMock)
    @patch('ai_assistant.core.critical_reviewer.CriticalReviewCoordinator.execute_council_debate', new_callable=AsyncMock)
    async def test_iterative_auto_healing_success(self, mock_council, mock_edit_code, mock_log_execution):
        mock_council.return_value = (True, "Approved")

        mock_edit_code.side_effect = [
            "success 1",
            "success 2"
        ]

        self.executor._run_post_modification_test = AsyncMock(side_effect=[
            (False, "Test failed first time"),
            (True, "Test passed second time")
        ])

        self.executor.code_service.modify_code = AsyncMock(return_value={
            "status": "SUCCESS_CODE_GENERATED",
            "modified_code_string": "def test_func(): pass # fixed"
        })

        action_details = {
            "module_path": "test_module.py", "function_name": "test_func", "tool_name": "test_tool",
            "suggested_code_change": "def test_func(): pass # buggy",
            "original_reflection_entry_id": "dummy_orig_ref_id",
            "suggested_change_description": "Initial change"
        }

        # Call the method directly for isolated testing
        success, notes, fail_reason = await self.executor._apply_test_and_revert_code(
            module_path=action_details["module_path"],
            function_name=action_details["function_name"],
            code_to_apply=action_details["suggested_code_change"],
            original_description=action_details["suggested_change_description"],
            source_insight_id="insight_1",
            action_task_id="task_1",
            original_reflection_id_for_test=action_details["original_reflection_entry_id"]
        )

        self.assertTrue(success)
        self.assertEqual(mock_edit_code.call_count, 2) # Initial apply + 1 auto-heal apply
        self.executor.code_service.modify_code.assert_called_once() # Called once to fix
        self.executor._run_post_modification_test.assert_called() # Called twice
        self.assertEqual(self.executor._run_post_modification_test.call_count, 2)

if __name__ == '__main__':
    unittest.main()
