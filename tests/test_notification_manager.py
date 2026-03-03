import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys
import json
import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

# Adjust path to import from the ai_assistant directory
# This assumes 'tests' is at the same level as 'ai_assistant'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ai_assistant.core.notification_manager import (
    NotificationManager,
    Notification,
    NotificationStatus,
    NotificationType,
    NOTIFICATIONS_FILE_NAME
)

# If get_data_dir is used by the module, we might need to mock it too,
# or ensure the test environment can handle its expected behavior.
# For now, assuming NotificationManager's fallback or direct path usage is testable.

class TestNotificationManager(unittest.TestCase):

    def setUp(self):
        self.test_data_dir = os.path.join("test_data_dir_notifications")
        self.test_filepath = os.path.join(self.test_data_dir, NOTIFICATIONS_FILE_NAME)

        # Patch 'get_data_dir' used within notification_manager.py
        self.get_data_dir_patcher = patch('ai_assistant.core.notification_manager.get_data_dir', return_value=self.test_data_dir)
        self.mock_get_data_dir = self.get_data_dir_patcher.start()

        self.mock_exists = patch('ai_assistant.core.notification_manager.os.path.exists').start()
        self.mock_makedirs = patch('ai_assistant.core.notification_manager.os.makedirs').start()
        self.mock_file_open = patch('builtins.open', new_callable=mock_open)
        self.mocked_open_function = self.mock_file_open.start()

        # Ensure a clean manager for each test, default behavior for exists is False
        self.mock_exists.return_value = False
        self.manager = NotificationManager(filepath=self.test_filepath)

        # Clear any calls to mocks from initial instantiation if not relevant to the specific test
        self.mocked_open_function.reset_mock()
        self.mock_makedirs.reset_mock()


    def tearDown(self):
        patch.stopall()

    def test_init_new_file(self):
        self.mock_exists.return_value = False # Simulate file not existing
        # Re-instantiate to trigger _load_notifications under these conditions
        manager = NotificationManager(filepath=self.test_filepath)
        self.assertEqual(manager.notifications, [])
        self.mock_makedirs.assert_called_once_with(self.test_data_dir, exist_ok=True)
        # open shouldn't be called for writing if file doesn't exist and no notifications are added yet
        self.mocked_open_function.assert_not_called()


    def test_init_loads_existing_valid_file(self):
        self.mock_exists.return_value = True
        notification_data = [
            Notification(NotificationType.GENERAL_INFO, "Test 1", timestamp=datetime.now(timezone.utc) - timedelta(hours=1)).to_dict(),
            Notification(NotificationType.WARNING, "Test 2").to_dict()
        ]
        # Ensure newest is first for consistent load order if sorted by timestamp later
        notification_data.sort(key=lambda x: x['timestamp'], reverse=True)

        mock_json_data = json.dumps(notification_data)
        self.mocked_open_function.return_value.read.return_value = mock_json_data

        manager = NotificationManager(filepath=self.test_filepath)
        self.assertEqual(len(manager.notifications), 2)
        self.assertEqual(manager.notifications[0].summary_message, "Test 2") # Newest
        self.assertEqual(manager.notifications[1].summary_message, "Test 1")
        self.mocked_open_function.assert_called_once_with(self.test_filepath, 'r', encoding='utf-8')

    def test_init_handles_empty_file(self):
        self.mock_exists.return_value = True
        self.mocked_open_function.return_value.read.return_value = "" # Empty file
        manager = NotificationManager(filepath=self.test_filepath)
        self.assertEqual(manager.notifications, [])

    def test_init_handles_corrupted_json_file(self):
        self.mock_exists.return_value = True
        self.mocked_open_function.return_value.read.return_value = "{corrupted_json"
        with patch('builtins.print') as mock_print: # Suppress error print during test
            manager = NotificationManager(filepath=self.test_filepath)
            self.assertEqual(manager.notifications, [])
            mock_print.assert_any_call(f"Error loading notifications from '{self.test_filepath}': Expecting property name enclosed in double quotes: line 1 column 2 (char 1). Initializing with empty list.")


    def test_add_notification_creates_and_saves(self):
        summary = "A new task completed!"
        event_type = NotificationType.TASK_COMPLETED_SUCCESSFULLY
        related_id = "task_123"

        # Reset mock_open for this specific test's save call check
        self.mocked_open_function.reset_mock()

        notification = self.manager.add_notification(event_type, summary, related_id)

        self.assertIsInstance(notification, Notification)
        self.assertEqual(notification.summary_message, summary)
        self.assertEqual(notification.event_type, event_type)
        self.assertEqual(notification.related_item_id, related_id)
        self.assertEqual(notification.status, NotificationStatus.UNREAD)
        self.assertIsNotNone(notification.notification_id)
        self.assertIsNotNone(notification.timestamp)

        self.assertIn(notification, self.manager.notifications)
        self.assertEqual(self.manager.notifications[0], notification) # Added to beginning
        self.mocked_open_function.assert_called_once_with(self.test_filepath, 'w', encoding='utf-8')

    def test_add_notification_truncates_long_summary(self):
        long_summary = "a" * 600
        truncated_summary = "a" * 497 + "..."
        notification = self.manager.add_notification(NotificationType.GENERAL_INFO, long_summary)
        self.assertEqual(notification.summary_message, truncated_summary)

    def test_get_notifications_filters_and_limits_and_sorts(self):
        # Timestamps are important for sorting
        time_now = datetime.now(timezone.utc)
        n1 = self.manager.add_notification(NotificationType.GENERAL_INFO, "Info Unread", timestamp=time_now - timedelta(seconds=30))
        n2 = self.manager.add_notification(NotificationType.WARNING, "Warn Unread", timestamp=time_now - timedelta(seconds=20))
        n3 = self.manager.add_notification(NotificationType.GENERAL_INFO, "Info Read", timestamp=time_now - timedelta(seconds=10))
        n4 = self.manager.add_notification(NotificationType.ERROR, "Error Archived", timestamp=time_now)

        self.manager.mark_as_read([n3.notification_id])
        self.manager.mark_as_archived([n4.notification_id])

        # Default: UNREAD, limit 10
        unread_notifications = self.manager.get_notifications()
        self.assertEqual(len(unread_notifications), 2)
        self.assertEqual(unread_notifications[0].notification_id, n2.notification_id) # n2 is newer unread
        self.assertEqual(unread_notifications[1].notification_id, n1.notification_id)

        # READ
        read_notifications = self.manager.get_notifications(status_filter=NotificationStatus.READ)
        self.assertEqual(len(read_notifications), 1)
        self.assertEqual(read_notifications[0].notification_id, n3.notification_id)

        # ARCHIVED
        archived_notifications = self.manager.get_notifications(status_filter=NotificationStatus.ARCHIVED)
        self.assertEqual(len(archived_notifications), 1)
        self.assertEqual(archived_notifications[0].notification_id, n4.notification_id)

        # Type filter
        general_info_unread = self.manager.get_notifications(type_filter=NotificationType.GENERAL_INFO)
        self.assertEqual(len(general_info_unread), 1) # Only n1 is GENERAL_INFO and UNREAD
        self.assertEqual(general_info_unread[0].notification_id, n1.notification_id)

        # Limit
        limited_unread = self.manager.get_notifications(limit=1)
        self.assertEqual(len(limited_unread), 1)
        self.assertEqual(limited_unread[0].notification_id, n2.notification_id) # Newest unread

        # All (status_filter=None), sorted by timestamp (newest from add_notification)
        all_notifications = self.manager.get_notifications(status_filter=None, limit=4)
        self.assertEqual(len(all_notifications), 4)
        # Order after status updates and their timestamp changes: n4 (archived, newest), n3 (read, newer), n2 (unread), n1 (unread, oldest)
        self.assertEqual(all_notifications[0].notification_id, n4.notification_id)
        self.assertEqual(all_loaded[1].notification_id, n3.notification_id) # Using all_loaded from test_save_load_cycle
        self.assertEqual(all_loaded[2].notification_id, n2.notification_id)


    def test_mark_as_read_updates_status_and_saves(self):
        n = self.manager.add_notification(NotificationType.GENERAL_INFO, "Test Read")
        original_timestamp = n.timestamp
        self.mocked_open_function.reset_mock() # Reset after add_notification's save

        self.assertTrue(self.manager.mark_as_read([n.notification_id]))
        self.assertEqual(n.status, NotificationStatus.READ)
        self.assertGreater(n.timestamp, original_timestamp) # Timestamp should be updated
        self.mocked_open_function.assert_called_once()

        # Try marking non-existent
        self.assertFalse(self.manager.mark_as_read(["non_existent_id"]))
        # Try marking already read
        self.mocked_open_function.reset_mock()
        self.assertFalse(self.manager.mark_as_read([n.notification_id]))
        self.mocked_open_function.assert_not_called()


    def test_mark_as_archived_updates_status_and_saves(self):
        n_unread = self.manager.add_notification(NotificationType.GENERAL_INFO, "Test Archive Unread")
        n_read = self.manager.add_notification(NotificationType.WARNING, "Test Archive Read")
        self.manager.mark_as_read([n_read.notification_id])

        original_ts_unread = n_unread.timestamp
        original_ts_read = n_read.timestamp
        self.mocked_open_function.reset_mock()

        self.assertTrue(self.manager.mark_as_archived([n_unread.notification_id, n_read.notification_id]))
        self.assertEqual(n_unread.status, NotificationStatus.ARCHIVED)
        self.assertEqual(n_read.status, NotificationStatus.ARCHIVED)
        self.assertGreater(n_unread.timestamp, original_ts_unread)
        self.assertGreater(n_read.timestamp, original_ts_read)
        self.mocked_open_function.assert_called_once()

        # Try marking non-existent
        self.assertFalse(self.manager.mark_as_archived(["non_existent_id"]))
        # Try marking already archived
        self.mocked_open_function.reset_mock()
        self.assertFalse(self.manager.mark_as_archived([n_unread.notification_id]))
        self.mocked_open_function.assert_not_called()


    def test_save_load_cycle_preserves_data(self):
        self.mock_exists.return_value = True # Simulate file will exist for loading

        # Add diverse notifications
        t1 = datetime.now(timezone.utc) - timedelta(days=1)
        t2 = datetime.now(timezone.utc)

        n1_orig = Notification(NotificationType.TASK_COMPLETED_SUCCESSFULLY, "Task A done", "id_task_a", t1, NotificationStatus.READ, "taskA", "task", {"detail1": "value1"})
        n2_orig = Notification(NotificationType.NEW_SUGGESTION_AI, "Suggest B", "id_sugg_b", t2, NotificationStatus.UNREAD, "suggB", "suggestion")

        # Manually add to manager's list to control timestamps precisely for this test
        self.manager.notifications = [n2_orig, n1_orig] # n2 is newer
        self.manager._save_notifications() # This will sort them: n2, n1

        # Capture what was written
        # mock_open().write() is called within json.dump, so we check the first call to open('w')
        # and its arguments. The actual content is on the handler.
        # Ensure the path is correct.
        self.mocked_open_function.assert_called_with(self.test_filepath, 'w', encoding='utf-8')
        written_content = self.mocked_open_function().write.call_args[0][0]

        # Setup for load: new manager, mock 'open' to read the captured content
        self.mocked_open_function.reset_mock()
        mock_read_handler = mock_open(read_data=written_content)
        self.mocked_open_function.side_effect = mock_read_handler # Use side_effect for subsequent calls

        manager2 = NotificationManager(filepath=self.test_filepath) # This will call _load_notifications

        self.assertEqual(len(manager2.notifications), 2)
        loaded_n2 = manager2.notifications[0] # After loading, it's sorted by timestamp desc
        loaded_n1 = manager2.notifications[1]

        self.assertEqual(loaded_n2.notification_id, n2_orig.notification_id)
        self.assertEqual(loaded_n2.event_type, n2_orig.event_type)
        self.assertEqual(loaded_n2.summary_message, n2_orig.summary_message)
        self.assertEqual(loaded_n2.timestamp, n2_orig.timestamp)
        self.assertEqual(loaded_n2.status, n2_orig.status)
        self.assertEqual(loaded_n2.related_item_id, n2_orig.related_item_id)
        self.assertEqual(loaded_n2.related_item_type, n2_orig.related_item_type)
        self.assertEqual(loaded_n2.details_payload, n2_orig.details_payload)

        self.assertEqual(loaded_n1.notification_id, n1_orig.notification_id)
        self.assertEqual(loaded_n1.event_type, n1_orig.event_type)
        self.assertEqual(loaded_n1.summary_message, n1_orig.summary_message)
        self.assertEqual(loaded_n1.timestamp, n1_orig.timestamp)
        self.assertEqual(loaded_n1.status, n1_orig.status)
        self.assertEqual(loaded_n1.related_item_id, n1_orig.related_item_id)
        self.assertEqual(loaded_n1.related_item_type, n1_orig.related_item_type)
        self.assertEqual(loaded_n1.details_payload, n1_orig.details_payload)

if __name__ == '__main__': # pragma: no cover
    unittest.main()
