import unittest
import datetime
import json
from ai_assistant.core.reflection import ReflectionLogEntry, ReflectionLog

class TestReflectionLogEntry(unittest.TestCase):

    def test_initialization_with_self_modification_fields(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        entry = ReflectionLogEntry(
            goal_description="Test goal",
            plan=[{"tool_name": "dummy_tool"}],
            execution_results=["dummy_result"],
            status="SUCCESS",
            timestamp=now,
            is_self_modification_attempt=True,
            source_suggestion_id="SUG001",
            modification_type="MODIFY_TOOL_CODE",
            modification_details={"module": "test.py", "function_name": "do_stuff"},
            post_modification_test_passed=True,
            post_modification_test_details={"passed": True, "notes": "All good", "stdout": "OK"},
            commit_info={"message": "Committed successfully", "status": True}
        )
        self.assertTrue(entry.is_self_modification_attempt)
        self.assertEqual(entry.source_suggestion_id, "SUG001")
        self.assertEqual(entry.modification_type, "MODIFY_TOOL_CODE")
        self.assertEqual(entry.modification_details, {"module": "test.py", "function_name": "do_stuff"})
        self.assertTrue(entry.post_modification_test_passed)
        self.assertEqual(entry.post_modification_test_details, {"passed": True, "notes": "All good", "stdout": "OK"})
        self.assertEqual(entry.commit_info, {"message": "Committed successfully", "status": True})
        self.assertEqual(entry.timestamp, now)

    def test_serialization_deserialization_self_modification(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        entry_orig = ReflectionLogEntry(
            goal_description="Test serialization",
            plan=[],
            execution_results=[],
            status="FAILURE",
            notes="Self-mod test",
            timestamp=now,
            is_self_modification_attempt=True,
            source_suggestion_id="SUG002",
            modification_type="UPDATE_TOOL_DESCRIPTION",
            modification_details={"tool_name": "tool_x", "new_description": "Better desc"},
            post_modification_test_passed=False,
            post_modification_test_details={"passed": False, "notes": "Test failed", "stderr": "Error!"},
            commit_info={"message": "Commit attempt failed", "status": False, "error": "Git error"}
        )

        serialized_dict = entry_orig.to_serializable_dict()

        # Assert new fields are in the dict
        self.assertTrue(serialized_dict["is_self_modification_attempt"])
        self.assertEqual(serialized_dict["source_suggestion_id"], "SUG002")
        self.assertEqual(serialized_dict["modification_type"], "UPDATE_TOOL_DESCRIPTION")
        self.assertEqual(serialized_dict["modification_details"], {"tool_name": "tool_x", "new_description": "Better desc"})
        self.assertFalse(serialized_dict["post_modification_test_passed"])
        self.assertEqual(serialized_dict["post_modification_test_details"], {"passed": False, "notes": "Test failed", "stderr": "Error!"})
        self.assertEqual(serialized_dict["commit_info"], {"message": "Commit attempt failed", "status": False, "error": "Git error"})
        self.assertEqual(serialized_dict["timestamp"], now.isoformat())


        entry_new = ReflectionLogEntry.from_serializable_dict(serialized_dict)

        # Assert new entry has all fields correctly loaded
        self.assertTrue(entry_new.is_self_modification_attempt)
        self.assertEqual(entry_new.source_suggestion_id, "SUG002")
        self.assertEqual(entry_new.modification_type, "UPDATE_TOOL_DESCRIPTION")
        self.assertEqual(entry_new.modification_details, {"tool_name": "tool_x", "new_description": "Better desc"})
        self.assertFalse(entry_new.post_modification_test_passed)
        self.assertEqual(entry_new.post_modification_test_details, {"passed": False, "notes": "Test failed", "stderr": "Error!"})
        self.assertEqual(entry_new.commit_info, {"message": "Commit attempt failed", "status": False, "error": "Git error"})
        self.assertEqual(entry_new.timestamp, now) # Timestamps are compared directly
        self.assertEqual(entry_new.notes, "Self-mod test")

    def test_deserialization_backward_compatibility(self):
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        old_entry_data = {
            "goal_description": "Old goal",
            "plan": [{"tool_name": "old_tool"}],
            "execution_results": ["old_result"],
            "status": "SUCCESS",
            "notes": "Old notes",
            "timestamp": now_iso,
            "error_type": None,
            "error_message": None,
            "traceback_snippet": None
            # New self-modification fields are absent
        }

        entry = ReflectionLogEntry.from_serializable_dict(old_entry_data)

        self.assertFalse(entry.is_self_modification_attempt)
        self.assertIsNone(entry.source_suggestion_id)
        self.assertIsNone(entry.modification_type)
        self.assertIsNone(entry.modification_details)
        self.assertIsNone(entry.post_modification_test_passed)
        self.assertIsNone(entry.post_modification_test_details)
        self.assertIsNone(entry.commit_info)
        self.assertEqual(entry.goal_description, "Old goal")

    def test_to_formatted_string_self_modification(self):
        entry_mod = ReflectionLogEntry(
            goal_description="Test formatting self-mod",
            plan=[], execution_results=[], status="SUCCESS",
            is_self_modification_attempt=True,
            source_suggestion_id="SUG003",
            modification_type="MODIFY_TOOL_CODE",
            modification_details={"module": "a.b.c", "function": "xyz"},
            post_modification_test_passed=True,
            post_modification_test_details={"passed": True, "notes": "Tests look great!"},
            commit_info={"message": "Code committed", "status": True}
        )
        formatted_str_mod = entry_mod.to_formatted_string()
        self.assertIn("--- Self-Modification Attempt Details ---", formatted_str_mod)
        self.assertIn("Source Suggestion ID: SUG003", formatted_str_mod)
        self.assertIn("Modification Type: MODIFY_TOOL_CODE", formatted_str_mod)
        self.assertIn(json.dumps(entry_mod.modification_details, indent=2), formatted_str_mod)
        self.assertIn("Test Outcome: True", formatted_str_mod) # Note: was PASSED, now True/False
        self.assertIn("Test Details: Tests look great!", formatted_str_mod)
        self.assertIn("Commit Info: Code committed", formatted_str_mod)


        entry_no_mod = ReflectionLogEntry(
            goal_description="Test formatting no self-mod",
            plan=[], execution_results=[], status="SUCCESS",
            is_self_modification_attempt=False
        )
        formatted_str_no_mod = entry_no_mod.to_formatted_string()
        self.assertNotIn("--- Self-Modification Attempt Details ---", formatted_str_no_mod)


class TestReflectionLog(unittest.TestCase):

    def setUp(self):
        # For these tests, we'll use an in-memory ReflectionLog
        # by not providing a filepath or mocking persistence functions.
        self.reflection_log = ReflectionLog(filepath=":memory:") # Use a special value or mock load/save

    def test_log_execution_with_self_modification_params(self):
        goal = "Test self-mod logging in ReflectionLog"
        plan_data = [{"tool_name": "self_mod_tool"}]
        exec_results = [{"outcome": "details from apply_code_modification"}]
        mod_details = {"module": "core.py", "change": "refactor"}
        test_details = {"passed": False, "notes": "Unit test failed post-mod"}
        commit_details = {"message": "Attempted refactor, tests failed", "status": False}

        self.reflection_log.log_execution(
            goal_description=goal,
            plan=plan_data,
            execution_results=exec_results,
            overall_success=False, # The self-mod attempt itself might "succeed" but the outcome (test) failed
            notes="Logging a self-modification attempt.",
            is_self_modification_attempt=True,
            source_suggestion_id="SUG004",
            modification_type="MODIFY_TOOL_CODE",
            modification_details=mod_details,
            post_modification_test_passed=False,
            post_modification_test_details=test_details,
            commit_info=commit_details
        )

        self.assertEqual(len(self.reflection_log.log_entries), 1)
        last_entry = self.reflection_log.log_entries[-1]

        self.assertIsInstance(last_entry, ReflectionLogEntry)
        self.assertEqual(last_entry.goal_description, goal)
        self.assertEqual(last_entry.notes, "Logging a self-modification attempt.")
        self.assertTrue(last_entry.is_self_modification_attempt)
        self.assertEqual(last_entry.source_suggestion_id, "SUG004")
        self.assertEqual(last_entry.modification_type, "MODIFY_TOOL_CODE")
        self.assertEqual(last_entry.modification_details, mod_details)
        self.assertFalse(last_entry.post_modification_test_passed)
        self.assertEqual(last_entry.post_modification_test_details, test_details)
        self.assertEqual(last_entry.commit_info, commit_details)
        # Check a few other standard fields
        self.assertEqual(last_entry.plan, plan_data)
        self.assertEqual(last_entry.execution_results, exec_results)
        # Status should be determined by log_execution logic based on overall_success and other factors.
        # If overall_success is False, status should reflect failure.
        self.assertIn(last_entry.status, ["FAILURE", "PARTIAL_SUCCESS"])


if __name__ == '__main__':
    unittest.main()
