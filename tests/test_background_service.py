import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock, call
import time

from ai_assistant.core.background_service import (
    start_background_services,
    stop_background_services,
    is_background_service_active
)
# We will patch constants directly where they are used or imported if needed.

class TestBackgroundService(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Stop any existing services first to ensure a clean state
        if is_background_service_active():
            await stop_background_services()

        # Reset/mock any global state within background_service if necessary,
        # for now, relying on start/stop and patching.

        # Default mock intervals for other tasks to be very long,
        # so they don't interfere with testing the autonomous action task primarily.
        self.patch_config = patch.multiple(
            'ai_assistant.config',
            REFLECTION_INTERVAL_SECONDS=10000,
            FACT_CURATION_INTERVAL_SECONDS=10000,
            PROJECT_EXECUTION_INTERVAL_SECONDS=10000,
            AUTONOMOUS_ACTION_INTERVAL_SECONDS=0.1  # Short for testing this feature
        )
        self.mock_config = self.patch_config.start()

        # Mock tool system for reflection part
        self.patch_tool_system = patch('ai_assistant.tools.tool_system.tool_system_instance.list_tools', MagicMock(return_value={"mock_tool": "desc"}))
        self.mock_tool_system = self.patch_tool_system.start()

        # Mock other background tasks
        self.patch_run_reflection = patch('ai_assistant.core.autonomous_reflection.run_self_reflection_cycle', AsyncMock(return_value=[]))
        self.mock_run_reflection = self.patch_run_reflection.start()

        self.patch_run_curation = patch('ai_assistant.custom_tools.knowledge_tools.run_periodic_fact_store_curation_async', AsyncMock(return_value=True))
        self.mock_run_curation = self.patch_run_curation.start()

        self.patch_exec_project = patch('ai_assistant.custom_tools.project_execution_tools.execute_project_coding_plan', AsyncMock(return_value="Project executed"))
        self.mock_exec_project = self.patch_exec_project.start()

        # Mock os.path.isdir for project execution part to avoid file system interactions
        self.patch_os_path_isdir = patch('os.path.isdir', MagicMock(return_value=False)) # Assume no projects dir for these tests
        self.mock_os_path_isdir = self.patch_os_path_isdir.start()


    async def asyncTearDown(self):
        if is_background_service_active():
            await stop_background_services()
        self.patch_config.stop()
        if hasattr(self, 'mock_tool_system') and self.mock_tool_system.is_started: # Ensure it was started
            self.patch_tool_system.stop()
        if hasattr(self, 'mock_run_reflection') and self.mock_run_reflection.is_started:
            self.patch_run_reflection.stop()
        if hasattr(self, 'mock_run_curation') and self.mock_run_curation.is_started:
            self.patch_run_curation.stop()
        if hasattr(self, 'mock_exec_project') and self.mock_exec_project.is_started:
            self.patch_exec_project.stop()
        if hasattr(self, 'mock_os_path_isdir') and self.mock_os_path_isdir.is_started:
            self.patch_os_path_isdir.stop()


    @patch('ai_assistant.core.background_service.suggestion_manager_module.get_suggestions', new_callable=AsyncMock)
    @patch('ai_assistant.core.background_service.select_suggestion_for_autonomous_action', new_callable=AsyncMock)
    async def test_autonomous_action_selection_triggered_periodically(
        self, mock_select_suggestion, mock_get_suggestions
    ):
        mock_get_suggestions.return_value = [] # No suggestions
        mock_select_suggestion.return_value = None # No action selected

        start_background_services()
        self.assertTrue(is_background_service_active())

        await asyncio.sleep(0.5)  # Run for a bit longer than 3*interval

        self.assertGreaterEqual(mock_get_suggestions.call_count, 3)
        # select_suggestion_for_autonomous_action is called if get_suggestions returns a non-empty list.
        # If get_suggestions returns [], it might not be called based on current BG service logic.
        # Let's refine: if get_suggestions returns [], select_suggestion is not called.
        # If get_suggestions returns suggestions, select_suggestion is called.

        # To test select_suggestion call, make get_suggestions return something
        mock_get_suggestions.return_value = [{'id': 's1', 'description': 'A suggestion'}]
        await asyncio.sleep(0.3) # Another few cycles

        self.assertGreaterEqual(mock_select_suggestion.call_count, 2) # Should be called a few times now

        await stop_background_services()

    @patch('ai_assistant.core.background_service.logger')
    @patch('ai_assistant.core.background_service.suggestion_manager_module.get_suggestions', new_callable=AsyncMock)
    @patch('ai_assistant.core.background_service.select_suggestion_for_autonomous_action', new_callable=AsyncMock)
    async def test_action_selected_and_logged(
        self, mock_select_suggestion, mock_get_suggestions, mock_logger
    ):
        mock_suggestions_list = [{'suggestion_id': 's1', 'action_type': 'MODIFY_TOOL_CODE', 'description': 'Fix tool'}]
        mock_get_suggestions.return_value = mock_suggestions_list

        mock_selected_action = {
            'suggestion_id': 's1',
            'action_type': 'MODIFY_TOOL_CODE',
            '_action_result': {'status': 'SUCCESS', 'message': 'Tool modified successfully.'}
        }
        mock_select_suggestion.return_value = mock_selected_action

        start_background_services(notification_manager_instance=MagicMock()) # Pass mock NM
        await asyncio.sleep(0.2) # Allow one cycle

        mock_get_suggestions.assert_called()
        mock_select_suggestion.assert_called_with(
            suggestions=mock_suggestions_list,
            notification_manager=unittest.mock.ANY # or the specific mock NM instance
        )

        # Check for the specific log message
        log_found = False
        for log_call in mock_logger.info.call_args_list:
            args, _ = log_call
            if "Autonomous action attempted for suggestion s1 (MODIFY_TOOL_CODE). Result: SUCCESS - Tool modified successfully." in args[0]:
                log_found = True
                break
        self.assertTrue(log_found, "Expected log message for successful action not found.")

        await stop_background_services()

    @patch('ai_assistant.core.background_service.logger')
    @patch('ai_assistant.core.background_service.suggestion_manager_module.get_suggestions', new_callable=AsyncMock)
    @patch('ai_assistant.core.background_service.select_suggestion_for_autonomous_action', new_callable=AsyncMock)
    async def test_no_action_selected_logged(
        self, mock_select_suggestion, mock_get_suggestions, mock_logger
    ):
        mock_suggestions_list = [{'suggestion_id': 's1', 'description': 'A suggestion'}]
        mock_get_suggestions.return_value = mock_suggestions_list
        mock_select_suggestion.return_value = None # No action selected

        start_background_services()
        await asyncio.sleep(0.2)

        log_found = False
        for log_call in mock_logger.info.call_args_list:
            args, _ = log_call
            if "No suitable suggestion was selected for autonomous action in this cycle." in args[0]:
                log_found = True
                break
        self.assertTrue(log_found, "Expected log message for no action selected not found.")

        await stop_background_services()

    @patch('ai_assistant.core.background_service.logger')
    @patch('ai_assistant.core.background_service.suggestion_manager_module.get_suggestions', new_callable=AsyncMock)
    @patch('ai_assistant.core.background_service.select_suggestion_for_autonomous_action', new_callable=AsyncMock)
    async def test_no_suggestions_available_logged(
        self, mock_select_suggestion, mock_get_suggestions, mock_logger
    ):
        mock_get_suggestions.return_value = [] # No suggestions available

        start_background_services()
        await asyncio.sleep(0.2)

        # select_suggestion_for_autonomous_action should not be called if no suggestions
        # based on the current implementation of the background service loop:
        # `if all_suggestions:` before calling `select_suggestion_for_autonomous_action`
        mock_select_suggestion.assert_not_called()

        log_found = False
        for log_call in mock_logger.info.call_args_list:
            args, _ = log_call
            if "No suggestions found for autonomous action." in args[0]: # This is logged if all_suggestions is empty
                log_found = True
                break
        self.assertTrue(log_found, "Expected log message for no suggestions available not found.")

        await stop_background_services()

    @patch('ai_assistant.core.background_service.logger')
    @patch('ai_assistant.core.background_service.suggestion_manager_module.get_suggestions', new_callable=AsyncMock)
    @patch('ai_assistant.core.background_service.select_suggestion_for_autonomous_action', new_callable=AsyncMock)
    async def test_error_during_selection_is_handled(
        self, mock_select_suggestion, mock_get_suggestions, mock_logger
    ):
        mock_suggestions_list = [{'suggestion_id': 's1', 'description': 'A suggestion'}]
        mock_get_suggestions.return_value = mock_suggestions_list
        mock_select_suggestion.side_effect = Exception("Test selection error")

        start_background_services()
        await asyncio.sleep(0.2) # First attempt

        log_found = False
        for log_call in mock_logger.error.call_args_list:
            args, _ = log_call
            if "Error during autonomous action selection/execution: Test selection error" in args[0]:
                log_found = True
                break
        self.assertTrue(log_found, "Expected error log message not found.")

        # Ensure it tries again
        mock_select_suggestion.reset_mock(side_effect=True) # Reset side_effect to allow subsequent calls without error
        mock_select_suggestion.return_value = None # Make it behave normally for the next call

        call_count_before_second_try = mock_select_suggestion.call_count
        await asyncio.sleep(0.2) # Allow for another cycle

        self.assertGreater(mock_select_suggestion.call_count, call_count_before_second_try, "Service should have retried after error.")

        await stop_background_services()

    @patch('ai_assistant.core.background_service.logger')
    @patch('ai_assistant.core.background_service.tool_system_instance.update_tool_description', new_callable=MagicMock) # Sync mock
    @patch('ai_assistant.core.background_service.suggestion_manager_module.update_suggestion_status', new_callable=MagicMock) # Sync mock
    @patch('ai_assistant.core.background_service.global_reflection_log.log_execution', new_callable=MagicMock)
    @patch('ai_assistant.core.background_service.select_suggestion_for_autonomous_action', new_callable=AsyncMock)
    @patch('ai_assistant.core.background_service.suggestion_manager_module.get_suggestions', new_callable=AsyncMock)
    async def test_handle_update_tool_description_action_success(
        self, mock_get_suggestions, mock_select_suggestion, mock_log_reflection,
        mock_update_suggestion_status, mock_ts_update_desc, mock_logger
    ):
        suggestion_id = "SUG_UTD001"
        tool_name = "test_tool"
        new_description = "This is a new description."

        mock_selected_suggestion = {
            'suggestion_id': suggestion_id,
            'action_type': 'UPDATE_TOOL_DESCRIPTION',
            'action_details': {'tool_name': tool_name, 'new_description': new_description},
            '_action_result': {'status': 'PENDING_EXECUTION', 'message': 'Pending update.'}
        }
        mock_get_suggestions.return_value = [mock_selected_suggestion] # Ensure it's a list
        mock_select_suggestion.return_value = mock_selected_suggestion

        mock_ts_update_desc.return_value = True # Simulate ToolSystem successfully updating

        start_background_services()
        await asyncio.sleep(0.2) # Allow one cycle to process

        # Verify _handle_update_tool_description was effectively called via select_suggestion
        # and that it called the ToolSystem's update method
        mock_ts_update_desc.assert_called_once_with(tool_name, new_description)

        # Verify suggestion status was updated to ACTIONED
        mock_update_suggestion_status.assert_called_once_with(suggestion_id, "ACTIONED", unittest.mock.ANY)

        # Verify reflection log was called
        mock_log_reflection.assert_called_once()
        log_args = mock_log_reflection.call_args[1]
        self.assertEqual(log_args['source_suggestion_id'], suggestion_id)
        self.assertEqual(log_args['modification_type'], "UPDATE_TOOL_DESCRIPTION")
        self.assertTrue(log_args['overall_success'])
        self.assertEqual(log_args['modification_details']['tool_name'], tool_name)

        # Verify logger info for successful handling within _handle_update_tool_description
        log_found = any(
            f"BG_HANDLER: Tool '{tool_name}' description updated successfully." in str(call_args)
            for call_args in mock_logger.info.call_args_list
        )
        self.assertTrue(log_found, "Expected success log from _handle_update_tool_description not found.")

        await stop_background_services()


    @patch('ai_assistant.core.background_service.logger')
    @patch('ai_assistant.core.background_service.tool_system_instance.update_tool_description', new_callable=MagicMock) # Sync mock
    @patch('ai_assistant.core.background_service.suggestion_manager_module.update_suggestion_status', new_callable=MagicMock) # Sync mock
    @patch('ai_assistant.core.background_service.global_reflection_log.log_execution', new_callable=MagicMock)
    @patch('ai_assistant.core.background_service.select_suggestion_for_autonomous_action', new_callable=AsyncMock)
    @patch('ai_assistant.core.background_service.suggestion_manager_module.get_suggestions', new_callable=AsyncMock)
    async def test_handle_update_tool_description_action_failure_from_toolsystem(
        self, mock_get_suggestions, mock_select_suggestion, mock_log_reflection,
        mock_update_suggestion_status, mock_ts_update_desc, mock_logger
    ):
        suggestion_id = "SUG_UTD002"
        tool_name = "non_existent_tool"
        new_description = "A description that won't be applied."

        mock_selected_suggestion = {
            'suggestion_id': suggestion_id,
            'action_type': 'UPDATE_TOOL_DESCRIPTION',
            'action_details': {'tool_name': tool_name, 'new_description': new_description},
            '_action_result': {'status': 'PENDING_EXECUTION', 'message': 'Pending update.'}
        }
        mock_get_suggestions.return_value = [mock_selected_suggestion]
        mock_select_suggestion.return_value = mock_selected_suggestion

        mock_ts_update_desc.return_value = False # Simulate ToolSystem failing to update (e.g., tool not found)

        start_background_services()
        await asyncio.sleep(0.2)

        mock_ts_update_desc.assert_called_once_with(tool_name, new_description)
        mock_update_suggestion_status.assert_called_once_with(suggestion_id, "ACTION_FAILED", unittest.mock.ANY)

        mock_log_reflection.assert_called_once()
        log_args = mock_log_reflection.call_args[1]
        self.assertEqual(log_args['source_suggestion_id'], suggestion_id)
        self.assertFalse(log_args['overall_success'])

        log_found = any(
            f"BG_HANDLER: Tool '{tool_name}' not found or update failed in ToolSystem." in str(call_args)
            for call_args in mock_logger.warning.call_args_list
        )
        self.assertTrue(log_found, "Expected warning log from _handle_update_tool_description for ToolSystem failure not found.")

        await stop_background_services()

    @patch('ai_assistant.core.background_service.logger')
    @patch('ai_assistant.core.background_service.suggestion_manager_module.update_suggestion_status', new_callable=MagicMock)
    @patch('ai_assistant.core.background_service.select_suggestion_for_autonomous_action', new_callable=AsyncMock)
    @patch('ai_assistant.core.background_service.suggestion_manager_module.get_suggestions', new_callable=AsyncMock)
    async def test_handle_update_tool_description_missing_details(
        self, mock_get_suggestions, mock_select_suggestion, mock_update_suggestion_status, mock_logger
    ):
        suggestion_id = "SUG_UTD003"
        mock_selected_suggestion = {
            'suggestion_id': suggestion_id,
            'action_type': 'UPDATE_TOOL_DESCRIPTION',
            'action_details': {'tool_name': 'some_tool'}, # Missing 'new_description'
            '_action_result': {'status': 'PENDING_EXECUTION', 'message': 'Pending update.'}
        }
        mock_get_suggestions.return_value = [mock_selected_suggestion]
        mock_select_suggestion.return_value = mock_selected_suggestion

        start_background_services()
        await asyncio.sleep(0.2)

        # ToolSystem's update_tool_description should NOT be called if details are missing
        # This is handled before calling _handle_update_tool_description by the calling logic in _background_loop_async
        # So, we check that update_suggestion_status was called with ACTION_FAILED by the loop itself.
        mock_update_suggestion_status.assert_called_once_with(suggestion_id, "ACTION_FAILED", "Missing tool_name or new_description")

        log_found = any(
            f"Cannot 'UPDATE_TOOL_DESCRIPTION' for suggestion '{suggestion_id}'. Missing 'tool_name' or 'new_description'" in str(call_args)
            for call_args in mock_logger.warning.call_args_list
        )
        self.assertTrue(log_found, "Expected warning log for missing action_details not found.")

        await stop_background_services()

if __name__ == '__main__':
    unittest.main()
