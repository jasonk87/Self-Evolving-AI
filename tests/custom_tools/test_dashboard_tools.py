import unittest
from unittest.mock import patch

# It's good practice to handle the case where the script is run from the root
# or from the tests directory.
try:
    from ai_assistant.custom_tools.dashboard_tools import get_system_status, get_dashboard_html
except ImportError:
    # If running from the root of the project, this might be needed
    # This is a common pattern in projects without a formal setup.py install
    import sys
    import os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
    from ai_assistant.custom_tools.dashboard_tools import get_system_status, get_dashboard_html

class TestDashboardTools(unittest.TestCase):

    @patch('ai_assistant.custom_tools.dashboard_tools.tool_system_instance')
    @patch('ai_assistant.custom_tools.dashboard_tools.global_reflection_log')
    def test_get_system_status_success(self, mock_reflection_log, mock_tool_system):
        """
        Tests that get_system_status returns the correct counts when its dependencies succeed.
        """
        # Configure mocks
        mock_tool_system.list_tools.return_value = ['tool1', 'tool2', 'tool3']
        mock_reflection_log.get_entries.return_value = ['log1', 'log2', 'log3', 'log4']

        # Call the function
        status = get_system_status()

        # Assertions
        self.assertIsInstance(status, dict)
        self.assertEqual(status['status'], 'OK')
        self.assertEqual(status['tool_count'], 3)
        self.assertEqual(status['reflection_log_entries'], 4)

    @patch('ai_assistant.custom_tools.dashboard_tools.tool_system_instance')
    @patch('ai_assistant.custom_tools.dashboard_tools.global_reflection_log')
    def test_get_system_status_dependency_failure(self, mock_reflection_log, mock_tool_system):
        """
        Tests that get_system_status handles exceptions from its dependencies gracefully.
        """
        # Configure mocks to raise exceptions
        mock_tool_system.list_tools.side_effect = Exception("Tool system failed")
        mock_reflection_log.get_entries.side_effect = Exception("Reflection log failed")

        # Call the function
        status = get_system_status()

        # Assertions
        self.assertIsInstance(status, dict)
        self.assertEqual(status['status'], 'OK')
        self.assertEqual(status['tool_count'], -1) # Should return -1 on error
        self.assertEqual(status['reflection_log_entries'], -1) # Should return -1 on error

    def test_get_dashboard_html(self):
        """
        Tests that get_dashboard_html returns a valid, non-empty HTML string.
        """
        # Call the function
        html = get_dashboard_html()

        # Assertions
        self.assertIsInstance(html, str)
        self.assertGreater(len(html), 0)
        self.assertIn('id="status-content"', html)
        self.assertIn('<button id="refresh-button"', html)
        self.assertIn("function refreshStatus()", html)
        self.assertIn("window.parent.postMessage(messagePayload, '*');", html)

if __name__ == '__main__':
    unittest.main()
