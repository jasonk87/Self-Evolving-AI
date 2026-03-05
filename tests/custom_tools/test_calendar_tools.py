import unittest
from unittest.mock import MagicMock, patch
import os
import datetime
from ai_assistant.integrations.google_calendar import CalendarManager

class TestCalendarManager(unittest.TestCase):
    def setUp(self):
        self.mock_service = MagicMock()

    @patch('ai_assistant.integrations.google_calendar.build')
    @patch('ai_assistant.integrations.google_calendar.InstalledAppFlow')
    @patch('ai_assistant.integrations.google_calendar.os.path.exists')
    @patch('ai_assistant.integrations.google_calendar.pickle.load')
    @patch('builtins.open')
    def test_list_upcoming_events(self, mock_open, mock_pickle_load, mock_exists, mock_flow, mock_build):
        # Mock auth to succeed
        mock_exists.return_value = True # token exists
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_pickle_load.return_value = mock_creds

        mock_build.return_value = self.mock_service

        # Mock events list
        mock_events = self.mock_service.events.return_value.list.return_value.execute.return_value
        mock_events.get.return_value = [
            {'start': {'dateTime': '2023-10-27T10:00:00Z'}, 'summary': 'Test Event 1'},
            {'start': {'dateTime': '2023-10-28T14:00:00Z'}, 'summary': 'Test Event 2'}
        ]

        manager = CalendarManager()
        result = manager.list_upcoming_events()

        self.assertIn("Test Event 1", result)
        self.assertIn("Test Event 2", result)
        self.assertIn("Upcoming Events:", result)

    @patch('ai_assistant.integrations.google_calendar.build')
    @patch('ai_assistant.integrations.google_calendar.os.path.exists')
    @patch('ai_assistant.integrations.google_calendar.pickle.load')
    @patch('builtins.open')
    def test_create_event(self, mock_open, mock_pickle_load, mock_exists, mock_build):
        mock_exists.return_value = True
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_pickle_load.return_value = mock_creds

        mock_build.return_value = self.mock_service

        # Mock execute return
        self.mock_service.events.return_value.insert.return_value.execute.return_value = {
            'htmlLink': 'http://calendar.google.com/event/123'
        }

        manager = CalendarManager()
        result = manager.create_event("Meeting", "2023-10-27T10:00:00", 30)

        self.assertIn("Event created", result)
        self.assertIn("http://calendar.google.com/event/123", result)

        # Verify call args
        args, kwargs = self.mock_service.events.return_value.insert.call_args
        body = kwargs['body']
        self.assertEqual(body['summary'], "Meeting")
        self.assertEqual(body['start']['dateTime'], "2023-10-27T10:00:00")
        # 30 mins later
        self.assertEqual(body['end']['dateTime'], "2023-10-27T10:30:00")

    @patch('ai_assistant.integrations.google_calendar.build')
    @patch('ai_assistant.integrations.google_calendar.os.path.exists')
    @patch('ai_assistant.integrations.google_calendar.pickle.load')
    @patch('builtins.open')
    def test_get_day_agenda(self, mock_open, mock_pickle_load, mock_exists, mock_build):
        mock_exists.return_value = True
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_pickle_load.return_value = mock_creds
        mock_build.return_value = self.mock_service

        # Mock events
        mock_events = self.mock_service.events.return_value.list.return_value.execute.return_value
        mock_events.get.return_value = []

        manager = CalendarManager()
        result = manager.get_day_agenda("today")

        self.assertIn("No events found", result)

if __name__ == '__main__':
    unittest.main()
