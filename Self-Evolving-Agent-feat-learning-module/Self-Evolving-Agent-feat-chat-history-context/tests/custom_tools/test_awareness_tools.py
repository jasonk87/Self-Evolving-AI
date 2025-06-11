import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional # For type hints if needed inside test functions

# Attempt to import from the project structure
try:
    from ai_assistant.custom_tools.awareness_tools import get_system_status_summary
    from ai_assistant.core.task_manager import TaskManager, ActiveTask, ActiveTaskStatus, ActiveTaskType
    from ai_assistant.core.notification_manager import NotificationManager, Notification, NotificationStatus, NotificationType
except ImportError: # pragma: no cover
    # Fallback for local execution if PYTHONPATH isn't set up for the tests directory
    import sys
    import os
    # Add the project root to sys.path
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.custom_tools.awareness_tools import get_system_status_summary
    from ai_assistant.core.task_manager import TaskManager, ActiveTask, ActiveTaskStatus, ActiveTaskType
    from ai_assistant.core.notification_manager import NotificationManager, Notification, NotificationStatus, NotificationType

class TestAwarenessTools(unittest.TestCase):

    def setUp(self):
        self.mock_task_manager = MagicMock(spec=TaskManager)
        self.mock_notification_manager = MagicMock(spec=NotificationManager)

    def _create_mock_task(self, task_id: str, description: str, task_type: ActiveTaskType, status: ActiveTaskStatus, related_item_id: Optional[str] = None, status_reason: Optional[str] = None, current_step: Optional[str] = None) -> MagicMock:
        mock_task = MagicMock(spec=ActiveTask)
        mock_task.task_id = task_id
        mock_task.description = description
        mock_task.task_type = MagicMock(spec=ActiveTaskType)
        mock_task.task_type.name = task_type.name
        mock_task.status = MagicMock(spec=ActiveTaskStatus)
        mock_task.status.name = status.name
        mock_task.related_item_id = related_item_id
        mock_task.status_reason = status_reason
        mock_task.current_step_description = current_step
        mock_task.created_at = datetime.now(timezone.utc)
        mock_task.last_updated_at = datetime.now(timezone.utc)
        return mock_task

    def _create_mock_notification(self, notif_id: str, event_type: NotificationType, summary: str, status: NotificationStatus = NotificationStatus.UNREAD) -> MagicMock:
        mock_notif = MagicMock(spec=Notification)
        mock_notif.notification_id = notif_id
        mock_notif.timestamp = datetime.now(timezone.utc)
        mock_notif.event_type = MagicMock(spec=NotificationType)
        mock_notif.event_type.name = event_type.name
        mock_notif.summary_message = summary
        mock_notif.status = MagicMock(spec=NotificationStatus)
        mock_notif.status.name = status.name # For display in _print_notifications_list if it uses .name
        # Add other fields if get_system_status_summary formats them
        mock_notif.related_item_id = f"rel_{notif_id}"
        mock_notif.related_item_type = "test_item"
        return mock_notif

    def test_get_system_status_summary_with_tasks_and_notifications(self):
        active_task1 = self._create_mock_task("task_active1", "Active Task 1 Description", ActiveTaskType.AGENT_TOOL_CREATION, ActiveTaskStatus.PLANNING, current_step="Step 1")
        archived_task1 = self._create_mock_task("task_arch1", "Archived Task 1 Description", ActiveTaskType.LEARNING_NEW_FACT, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, status_reason="Fact learned")

        self.mock_task_manager.list_active_tasks.return_value = [active_task1]
        self.mock_task_manager.list_archived_tasks.return_value = [archived_task1]

        unread_notif1 = self._create_mock_notification("n_unread1", NotificationType.TASK_COMPLETED_SUCCESSFULLY, "Unread notification 1 summary")
        unread_notif2 = self._create_mock_notification("n_unread2", NotificationType.WARNING, "Unread critical warning")
        self.mock_notification_manager.get_notifications.return_value = [unread_notif1, unread_notif2]

        summary = get_system_status_summary(
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager,
            active_limit=1,
            archived_limit=1,
            unread_notifications_limit=2
        )

        self.mock_task_manager.list_active_tasks.assert_called_once()
        self.mock_task_manager.list_archived_tasks.assert_called_once_with(limit=1)
        self.mock_notification_manager.get_notifications.assert_called_once_with(
            status_filter=NotificationStatus.UNREAD,
            limit=2
        )

        self.assertIn("Active Task 1 Description", summary)
        self.assertIn("Archived Task 1 Description", summary)
        self.assertIn("Unread Notifications (2 shown, up to 2 displayed):", summary)
        self.assertIn("Unread notification 1 summary", summary)
        self.assertIn("Unread critical warning", summary)

    def test_get_system_status_summary_no_tasks_no_notifications(self):
        self.mock_task_manager.list_active_tasks.return_value = []
        self.mock_task_manager.list_archived_tasks.return_value = []
        self.mock_notification_manager.get_notifications.return_value = []

        summary = get_system_status_summary(
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager
        )
        self.assertIn("No active tasks currently.", summary)
        self.assertIn("No recently archived tasks.", summary)
        self.assertIn("No unread notifications.", summary)

    def test_get_system_status_summary_only_tasks(self):
        active_task1 = self._create_mock_task("task_active_only", "Active Task Only", ActiveTaskType.MISC_CODE_GENERATION, ActiveTaskStatus.GENERATING_CODE)
        self.mock_task_manager.list_active_tasks.return_value = [active_task1]
        self.mock_task_manager.list_archived_tasks.return_value = []
        self.mock_notification_manager.get_notifications.return_value = []

        summary = get_system_status_summary(
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager
        )
        self.assertIn("Active Task Only", summary)
        self.assertIn("No unread notifications.", summary)
        self.mock_notification_manager.get_notifications.assert_called_once_with(
            status_filter=NotificationStatus.UNREAD,
            limit=3 # Default limit
        )

    def test_get_system_status_summary_only_notifications(self):
        self.mock_task_manager.list_active_tasks.return_value = []
        self.mock_task_manager.list_archived_tasks.return_value = []
        unread_notif1 = self._create_mock_notification("n_only1", NotificationType.ERROR, "Only notification test")
        self.mock_notification_manager.get_notifications.return_value = [unread_notif1]

        summary = get_system_status_summary(
            task_manager=self.mock_task_manager,
            notification_manager=self.mock_notification_manager,
            unread_notifications_limit=1
        )
        self.assertIn("No active tasks currently.", summary)
        self.assertIn("Only notification test", summary)
        self.assertIn("Unread Notifications (1 shown, up to 1 displayed):", summary)
        self.mock_task_manager.list_active_tasks.assert_called_once()
        self.mock_task_manager.list_archived_tasks.assert_called_once()


    def test_get_system_status_summary_handles_missing_managers(self):
        summary_no_tm = get_system_status_summary(task_manager=None, notification_manager=self.mock_notification_manager)
        self.assertIn("TaskManager not available.", summary_no_tm)
        self.assertIn("Unread Notifications", summary_no_tm) # Notification part should still run

        summary_no_nm = get_system_status_summary(task_manager=self.mock_task_manager, notification_manager=None)
        self.assertIn("Active Tasks", summary_no_nm) # Task part should still run
        self.assertIn("NotificationManager not available.", summary_no_nm)

        summary_no_managers = get_system_status_summary(task_manager=None, notification_manager=None)
        self.assertIn("TaskManager not available.", summary_no_managers)
        self.assertIn("NotificationManager not available.", summary_no_managers)

if __name__ == '__main__': # pragma: no cover
    unittest.main()
