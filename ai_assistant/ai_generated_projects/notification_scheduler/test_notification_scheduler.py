import unittest
import datetime
import time
from unittest.mock import patch

# Assume notification_scheduler.py exists in the same directory
from notification_scheduler import NotificationScheduler

class TestNotificationScheduler(unittest.TestCase):

    def setUp(self):
        self.scheduler = NotificationScheduler()
        self.scheduler.clear_scheduled_notifications() # Ensure a clean slate for each test

    def test_schedule_notification(self):
        notification_time = datetime.datetime.now() + datetime.timedelta(seconds=1)
        message = "Test Notification"
        self.scheduler.schedule_notification(notification_time, message)
        self.assertEqual(len(self.scheduler.scheduled_notifications), 1)
        self.assertEqual(self.scheduler.scheduled_notifications[0]['message'], message)
        self.assertEqual(self.scheduler.scheduled_notifications[0]['time'], notification_time)

    def test_cancel_notification(self):
        notification_time = datetime.datetime.now() + datetime.timedelta(seconds=1)
        message = "Test Notification"
        self.scheduler.schedule_notification(notification_time, message)
        notification_id = self.scheduler.scheduled_notifications[0]['id']
        self.scheduler.cancel_notification(notification_id)
        self.assertEqual(len(self.scheduler.scheduled_notifications), 0)

    def test_run_pending_notifications(self):
        notification_time = datetime.datetime.now() + datetime.timedelta(seconds=1)
        message = "Test Notification"
        self.scheduler.schedule_notification(notification_time, message)
        time.sleep(2)  # Wait for the notification to be due
        with patch('notification_scheduler.print') as mock_print:
            self.scheduler.run_pending_notifications()
            mock_print.assert_called_with(message)
        self.assertEqual(len(self.scheduler.scheduled_notifications), 0)

    def test_run_pending_notifications_no_notifications(self):
        with patch('notification_scheduler.print') as mock_print:
            self.scheduler.run_pending_notifications()
            mock_print.assert_not_called()

    def test_run_pending_notifications_future_notification(self):
        notification_time = datetime.datetime.now() + datetime.timedelta(seconds=10)
        message = "Test Notification"
        self.scheduler.schedule_notification(notification_time, message)
        with patch('notification_scheduler.print') as mock_print:
            self.scheduler.run_pending_notifications()
            mock_print.assert_not_called()
        self.assertEqual(len(self.scheduler.scheduled_notifications), 1)

    def test_clear_scheduled_notifications(self):
        notification_time = datetime.datetime.now() + datetime.timedelta(seconds=1)
        message = "Test Notification"
        self.scheduler.schedule_notification(notification_time, message)
        self.scheduler.clear_scheduled_notifications()
        self.assertEqual(len(self.scheduler.scheduled_notifications), 0)

if __name__ == '__main__':
    unittest.main()