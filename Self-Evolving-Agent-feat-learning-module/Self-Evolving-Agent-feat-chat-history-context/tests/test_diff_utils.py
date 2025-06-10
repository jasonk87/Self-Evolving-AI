import unittest
import sys
import os

# Ensure the 'ai_assistant' module can be imported
# This might need adjustment based on your exact project structure and how tests are run
try:
    from ai_assistant.core.diff_utils import generate_diff
except ImportError:
    # Fallback for local execution if PYTHONPATH isn't set up
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) # Adjust '..' as needed
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core.diff_utils import generate_diff


class TestDiffUtils(unittest.TestCase):

    def test_generate_diff_no_changes(self):
        old_code = "def hello():\n    print('world')"
        new_code = "def hello():\n    print('world')"
        diff = generate_diff(old_code, new_code)
        self.assertEqual(diff, "")

    def test_generate_diff_simple_change(self):
        old_code = "def hello():\n    print('world')"
        new_code = "def hello():\n    print('new world')"
        diff = generate_diff(old_code, new_code, file_name="test.py")
        self.assertIn("--- a/test.py", diff)
        self.assertIn("+++ b/test.py", diff)
        self.assertIn("-    print('world')", diff)
        self.assertIn("+    print('new world')", diff)

    def test_generate_diff_addition(self):
        old_code = "line1\nline2"
        new_code = "line1\nline2\nline3"
        diff = generate_diff(old_code, new_code, file_name="add.txt")
        self.assertIn("--- a/add.txt", diff)
        self.assertIn("+++ b/add.txt", diff)
        self.assertIn("+line3", diff)

    def test_generate_diff_deletion(self):
        old_code = "line1\nline2\nline3"
        new_code = "line1\nline3"
        diff = generate_diff(old_code, new_code, file_name="delete.txt")
        self.assertIn("--- a/delete.txt", diff)
        self.assertIn("+++ b/delete.txt", diff)
        self.assertIn("-line2", diff)

    def test_generate_diff_empty_old_code(self):
        old_code = ""
        new_code = "a new beginning"
        diff = generate_diff(old_code, new_code, file_name="new_file.txt")
        self.assertIn("--- a/new_file.txt", diff)
        self.assertIn("+++ b/new_file.txt", diff)
        self.assertIn("+a new beginning", diff)

    def test_generate_diff_empty_new_code(self):
        old_code = "all will be deleted"
        new_code = ""
        diff = generate_diff(old_code, new_code, file_name="deleted_file.txt")
        self.assertIn("--- a/deleted_file.txt", diff)
        self.assertIn("+++ b/deleted_file.txt", diff)
        self.assertIn("-all will be deleted", diff)

if __name__ == '__main__':
    unittest.main()
