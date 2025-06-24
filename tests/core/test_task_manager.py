import unittest
from unittest.mock import MagicMock, patch, mock_open
import os
import sys
import json
import uuid
from datetime import datetime, timezone, timedelta
import tempfile

# Add project root to sys.path to allow importing ai_assistant modules
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path: # pragma: no cover
    sys.path.insert(0, project_root)

from ai_assistant.core.task_manager import (
    TaskManager, ActiveTask, ActiveTaskStatus, ActiveTaskType,
    ACTIVE_TASKS_FILE_NAME, get_data_dir # Import get_data_dir to patch it
)
from ai_assistant.core.notification_manager import NotificationManager # For mocking

class TestTaskManagerPersistence(unittest.TestCase):

    def setUp(self):
        self.mock_notification_manager = MagicMock(spec=NotificationManager)

        # Patch get_data_dir to use a temporary directory for tests
        self.temp_dir_patcher = patch('ai_assistant.core.task_manager.get_data_dir')
        self.mock_get_data_dir = self.temp_dir_patcher.start()

        self.test_dir = tempfile.TemporaryDirectory()
        self.mock_get_data_dir.return_value = self.test_dir.name

        self.active_tasks_filepath = os.path.join(self.test_dir.name, ACTIVE_TASKS_FILE_NAME)

        # Ensure _ensure_data_dir_exists is also patched if it's separate and creates dirs
        # For this test, os.makedirs will be called by TaskManager via _ensure_data_dir_exists
        # which is fine as it uses the temp self.test_dir.name.

    def tearDown(self):
        self.test_dir.cleanup()
        self.temp_dir_patcher.stop()

    def _read_json_file(self, filepath):
        if not os.path.exists(filepath):
            return None
        with open(filepath, 'r') as f:
            return json.load(f)

    def test_01_init_loads_tasks_if_file_exists_empty_otherwise(self):
        # Scenario 1: File does not exist
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        self.assertEqual(len(tm._active_tasks), 0)
        # _load_active_tasks does not create the file, only _save_active_tasks does
        self.assertFalse(os.path.exists(self.active_tasks_filepath))

        # Scenario 2: File exists but is empty
        with open(self.active_tasks_filepath, 'w') as f:
            f.write("") # Empty file
        tm2 = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        self.assertEqual(len(tm2._active_tasks), 0)

        # Scenario 3: File exists with valid data (tested in test_load_active_tasks_correctly_deserializes)

    def test_02_add_task_saves_to_file(self):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        task = tm.add_task(ActiveTaskType.AGENT_TOOL_CREATION, "Test add task")

        self.assertTrue(os.path.exists(self.active_tasks_filepath))
        saved_data = self._read_json_file(self.active_tasks_filepath)
        self.assertIsNotNone(saved_data)
        self.assertEqual(len(saved_data), 1)
        self.assertEqual(saved_data[0]['task_id'], task.task_id)
        self.assertEqual(saved_data[0]['description'], "Test add task")

    def test_03_update_task_status_saves_to_file(self):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        task = tm.add_task(ActiveTaskType.AGENT_TOOL_MODIFICATION, "Test update task")

        tm.update_task_status(
            task.task_id, ActiveTaskStatus.PLANNING,
            reason="Planning started",
            step_desc="Generating plan",
            sub_step_name="Detailing step 1",
            progress=50,
            out_preview="Plan outline...",
            resume_data={"current_plan_step": 1}
        )

        saved_data = self._read_json_file(self.active_tasks_filepath)
        self.assertIsNotNone(saved_data)
        self.assertEqual(len(saved_data), 1)
        self.assertEqual(saved_data[0]['task_id'], task.task_id)
        self.assertEqual(saved_data[0]['status'], ActiveTaskStatus.PLANNING.name)
        self.assertEqual(saved_data[0]['status_reason'], "Planning started")
        self.assertEqual(saved_data[0]['current_step_description'], "Generating plan")
        self.assertEqual(saved_data[0]['current_sub_step_name'], "Detailing step 1")
        self.assertEqual(saved_data[0]['progress_percentage'], 50)
        self.assertEqual(saved_data[0]['output_preview'], "Plan outline...")
        self.assertEqual(saved_data[0]['data_for_resume'], {"current_plan_step": 1})
        self.assertEqual(saved_data[0]['error_count'], 0)

        tm.update_task_status(task.task_id, ActiveTaskStatus.GENERATING_CODE, is_error_increment=True)
        saved_data_after_error = self._read_json_file(self.active_tasks_filepath)
        self.assertEqual(saved_data_after_error[0]['error_count'], 1)


    def test_04_archive_task_updates_active_tasks_file(self):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        task1 = tm.add_task(ActiveTaskType.LEARNING_NEW_FACT, "Task to be archived")
        task2 = tm.add_task(ActiveTaskType.MISC_CODE_GENERATION, "Task to remain active")

        # Trigger archiving for task1
        tm.update_task_status(task1.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY)

        saved_data = self._read_json_file(self.active_tasks_filepath)
        self.assertIsNotNone(saved_data)
        self.assertEqual(len(saved_data), 1) # Only task2 should remain
        self.assertEqual(saved_data[0]['task_id'], task2.task_id)

        self.assertIn(task1, tm._completed_tasks_archive) # Check internal archive list

    def test_05_load_active_tasks_correctly_deserializes(self):
        now = datetime.now(timezone.utc)
        task_data_serialized = [
            {
                "task_id": "task_load_1",
                "task_type": ActiveTaskType.USER_PROJECT_SCAFFOLDING.name,
                "description": "Load test task 1",
                "status": ActiveTaskStatus.AWAITING_CRITIC_REVIEW.name,
                "status_reason": "Ready for review",
                "created_at": (now - timedelta(hours=1)).isoformat(),
                "last_updated_at": now.isoformat(),
                "related_item_id": "proj_abc",
                "details": {"target_dir": "/tmp/proj_abc"},
                "current_step_description": "Waiting for critic feedback",
                "current_sub_step_name": "CriticReviewPending",
                "progress_percentage": 75,
                "error_count": 2,
                "output_preview": "Generated file structure...",
                "data_for_resume": {"last_file_generated": "main.py"}
            }
        ]
        with open(self.active_tasks_filepath, 'w') as f:
            json.dump(task_data_serialized, f)

        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        self.assertEqual(len(tm._active_tasks), 1)
        loaded_task = tm.get_task("task_load_1")
        self.assertIsNotNone(loaded_task)

        self.assertEqual(loaded_task.task_type, ActiveTaskType.USER_PROJECT_SCAFFOLDING)
        self.assertEqual(loaded_task.description, "Load test task 1")
        self.assertEqual(loaded_task.status, ActiveTaskStatus.AWAITING_CRITIC_REVIEW)
        self.assertEqual(loaded_task.status_reason, "Ready for review")
        self.assertEqual(loaded_task.created_at, datetime.fromisoformat(task_data_serialized[0]['created_at']))
        self.assertEqual(loaded_task.last_updated_at, now)
        self.assertEqual(loaded_task.related_item_id, "proj_abc")
        self.assertEqual(loaded_task.details, {"target_dir": "/tmp/proj_abc"})
        self.assertEqual(loaded_task.current_step_description, "Waiting for critic feedback")
        self.assertEqual(loaded_task.current_sub_step_name, "CriticReviewPending")
        self.assertEqual(loaded_task.progress_percentage, 75)
        self.assertEqual(loaded_task.error_count, 2)
        self.assertEqual(loaded_task.output_preview, "Generated file structure...")
        self.assertEqual(loaded_task.data_for_resume, {"last_file_generated": "main.py"})

    def test_06_load_active_tasks_handles_empty_or_corrupt_file(self):
        # Test with empty file (already partially covered in test_01_init)
        with open(self.active_tasks_filepath, 'w') as f:
            f.write("")
        tm_empty = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        self.assertEqual(len(tm_empty._active_tasks), 0)

        # Test with corrupt JSON
        with open(self.active_tasks_filepath, 'w') as f:
            f.write("{not_json_at_all")
        tm_corrupt = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        self.assertEqual(len(tm_corrupt._active_tasks), 0)
        # Should also print an error, but we'd need to capture stdout to assert that.

    def test_07_load_active_tasks_handles_missing_optional_fields_in_old_data(self):
        now = datetime.now(timezone.utc)
        old_task_data = [
            { # Task missing all new optional fields
                "task_id": "task_old_1",
                "task_type": ActiveTaskType.AGENT_TOOL_CREATION.name,
                "description": "Old task format",
                "status": ActiveTaskStatus.PLANNING.name,
                "created_at": now.isoformat(),
                "last_updated_at": now.isoformat(),
                "details": {}
                # Missing: status_reason, related_item_id, current_step_description,
                # current_sub_step_name, progress_percentage, error_count (should default to 0),
                # output_preview, data_for_resume
            }
        ]
        with open(self.active_tasks_filepath, 'w') as f:
            json.dump(old_task_data, f)

        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        self.assertEqual(len(tm._active_tasks), 1)
        loaded_task = tm.get_task("task_old_1")
        self.assertIsNotNone(loaded_task)

        self.assertIsNone(loaded_task.status_reason)
        self.assertIsNone(loaded_task.related_item_id)
        self.assertIsNone(loaded_task.current_step_description)
        self.assertIsNone(loaded_task.current_sub_step_name)
        self.assertIsNone(loaded_task.progress_percentage)
        self.assertEqual(loaded_task.error_count, 0) # Should default to 0
        self.assertIsNone(loaded_task.output_preview)
        self.assertIsNone(loaded_task.data_for_resume)
        self.assertEqual(loaded_task.details, {})

    def test_08_clear_all_tasks_saves_empty_list(self):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        tm.add_task(ActiveTaskType.AGENT_TOOL_CREATION, "Task to be cleared")

        saved_data_before_clear = self._read_json_file(self.active_tasks_filepath)
        self.assertEqual(len(saved_data_before_clear), 1)

        tm.clear_all_tasks(clear_archive=False) # clear_archive doesn't affect active_tasks.json

        saved_data_after_clear = self._read_json_file(self.active_tasks_filepath)
        self.assertIsNotNone(saved_data_after_clear)
        self.assertEqual(len(saved_data_after_clear), 0)


if __name__ == '__main__': # pragma: no cover
    unittest.main()

    # --- Tests for HIERARCHICAL_PROJECT_EXECUTION ---
    def _get_sample_project_plan(self, num_steps=3):
        return [{"step_id": f"s{i+1}", "description": f"Step {i+1} desc"} for i in range(num_steps)]

    def test_add_hierarchical_project_execution_task_successful(self):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        user_goal = "Test hierarchical execution"
        project_plan = self._get_sample_project_plan(2)
        details = {"user_goal": user_goal, "project_plan": project_plan, "project_name": "HierTest"}

        task = tm.add_task(ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION, "Hierarchical Task Test", details=details)
        self.assertIsNotNone(task)
        self.assertEqual(task.task_type, ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION)
        self.assertEqual(task.details["user_goal"], user_goal)
        self.assertEqual(len(task.details["project_plan"]), 2)
        self.assertEqual(task.details["current_plan_step_index"], 0)
        self.assertEqual(len(task.details["plan_step_statuses"]), 2)
        self.assertEqual(task.details["plan_step_statuses"][0]["step_id"], "s1")
        self.assertEqual(task.details["plan_step_statuses"][0]["status"], "pending")
        self.assertEqual(task.details["plan_step_statuses"][1]["description"], "Step 2 desc")

        saved_data = self._read_json_file(self.active_tasks_filepath)
        self.assertEqual(saved_data[0]["details"]["current_plan_step_index"], 0)
        self.assertEqual(len(saved_data[0]["details"]["plan_step_statuses"]), 2)

    @patch('builtins.print')
    def test_add_hierarchical_project_execution_task_missing_details(self, mock_print):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        # Missing 'project_plan'
        details_missing_plan = {"user_goal": "Goal without plan"}
        task1 = tm.add_task(ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION, "Missing Plan", details=details_missing_plan)
        self.assertTrue(any("without 'project_plan' or 'user_goal'" in str(call_args) for call_args in mock_print.call_args_list))
        self.assertEqual(task1.details["current_plan_step_index"], 0) # Still initializes other parts
        self.assertEqual(len(task1.details["plan_step_statuses"]), 0) # Based on empty plan

        mock_print.reset_mock()
        # Missing 'user_goal'
        details_missing_goal = {"project_plan": self._get_sample_project_plan(1)}
        task2 = tm.add_task(ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION, "Missing Goal", details=details_missing_goal)
        self.assertTrue(any("without 'project_plan' or 'user_goal'" in str(call_args) for call_args in mock_print.call_args_list))

    @patch('builtins.print')
    def test_add_hierarchical_project_execution_task_invalid_plan_type(self, mock_print):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        details_invalid_plan = {"user_goal": "Goal", "project_plan": "not_a_list"}
        task = tm.add_task(ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION, "Invalid Plan Type", details=details_invalid_plan)
        self.assertTrue(any("'project_plan' for HIERARCHICAL_PROJECT_EXECUTION task must be a list" in str(call_args) for call_args in mock_print.call_args_list))
        self.assertEqual(len(task.details.get("plan_step_statuses", [])), 0) # Should default to empty if plan was invalid


    def _add_hierarchical_task_for_update_tests(self, tm: TaskManager, num_steps=3) -> ActiveTask:
        user_goal = "Test hierarchical updates"
        project_plan = self._get_sample_project_plan(num_steps)
        details = {"user_goal": user_goal, "project_plan": project_plan}
        return tm.add_task(ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION, "Hierarchical Update Task", details=details)

    def test_update_hierarchical_task_step_success_ongoing(self):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        task = self._add_hierarchical_task_for_update_tests(tm, num_steps=3)

        resume_data_step1_success = {
            "plan_step_update": {"step_id": "s1", "status": "success", "output_preview": "Step 1 good output"}
        }
        updated_task = tm.update_task_status(
            task.task_id,
            ActiveTaskStatus.EXECUTING_PROJECT_PLAN,
            resume_data=resume_data_step1_success
        )

        self.assertIsNotNone(updated_task)
        self.assertEqual(updated_task.details["current_plan_step_index"], 1)
        self.assertEqual(updated_task.details["plan_step_statuses"][0]["status"], "success")
        self.assertEqual(updated_task.details["plan_step_statuses"][0]["output_preview"], "Step 1 good output")
        self.assertEqual(updated_task.progress_percentage, 33) # 1 of 3 complete
        self.assertEqual(updated_task.status, ActiveTaskStatus.EXECUTING_PROJECT_PLAN)
        self.assertIn("Executing plan: Step 2 desc", updated_task.current_step_description)

        # Simulate second step success
        resume_data_step2_success = {
            "plan_step_update": {"step_id": "s2", "status": "success", "output_preview": "Step 2 amazing"}
        }
        updated_task_s2 = tm.update_task_status(
            task.task_id,
            ActiveTaskStatus.EXECUTING_PROJECT_PLAN,
            resume_data=resume_data_step2_success
        )
        self.assertEqual(updated_task_s2.details["current_plan_step_index"], 2)
        self.assertEqual(updated_task_s2.progress_percentage, 66) # 2 of 3 complete
        self.assertIn("Executing plan: Step 3 desc", updated_task_s2.current_step_description)


    def test_update_hierarchical_task_step_success_all_complete(self):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        task = self._add_hierarchical_task_for_update_tests(tm, num_steps=1) # Single step plan

        resume_data_step1_success = {
            "plan_step_update": {"step_id": "s1", "status": "success", "output_preview": "Final step done"}
        }
        # Caller (e.g. orchestrator) determines this is the last step and sets overall status
        updated_task = tm.update_task_status(
            task.task_id,
            ActiveTaskStatus.COMPLETED_SUCCESSFULLY,
            resume_data=resume_data_step1_success
        )
        self.assertIsNotNone(updated_task)
        self.assertEqual(updated_task.details["current_plan_step_index"], 1) # Incremented past last step
        self.assertEqual(updated_task.details["plan_step_statuses"][0]["status"], "success")
        self.assertEqual(updated_task.progress_percentage, 100)
        self.assertEqual(updated_task.status, ActiveTaskStatus.COMPLETED_SUCCESSFULLY)
        self.assertIn("Project plan executed successfully.", updated_task.current_step_description)

        # Task should be archived
        self.assertIsNone(tm.get_task(task.task_id))
        archived = tm.list_archived_tasks()
        self.assertTrue(any(t.task_id == task.task_id for t in archived))

    def test_update_hierarchical_task_step_failure(self):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        task = self._add_hierarchical_task_for_update_tests(tm, num_steps=2)

        resume_data_step_failure = {
            "plan_step_update": {
                "step_id": "s1",
                "status": "failed",
                "error_message": "Script exploded",
                "description": "Step 1 desc" # Adding description here as it's used in status_reason
            }
        }
        # Caller (orchestrator) sets overall status to PROJECT_PLAN_FAILED_STEP
        updated_task = tm.update_task_status(
            task.task_id,
            ActiveTaskStatus.PROJECT_PLAN_FAILED_STEP,
            reason="Failure during plan execution on step s1", # Overall reason
            resume_data=resume_data_step_failure
        )
        self.assertIsNotNone(updated_task)
        self.assertEqual(updated_task.details["plan_step_statuses"][0]["status"], "failed")
        self.assertEqual(updated_task.details["plan_step_statuses"][0]["error_message"], "Script exploded")
        self.assertEqual(updated_task.details["current_plan_step_index"], 0) # Index does not advance on failure
        self.assertEqual(updated_task.progress_percentage, 0) # No steps successfully completed
        self.assertEqual(updated_task.status, ActiveTaskStatus.PROJECT_PLAN_FAILED_STEP)
        self.assertIn("Project plan failed at step: 'Step 1 desc'. Reason: Script exploded", updated_task.current_step_description)
        self.assertEqual(updated_task.status_reason, "Failure during plan execution on step s1")

        # Task should be archived due to terminal status PROJECT_PLAN_FAILED_STEP
        self.assertIsNone(tm.get_task(task.task_id))
        archived = tm.list_archived_tasks()
        self.assertTrue(any(t.task_id == task.task_id for t in archived))

    def test_update_hierarchical_task_no_plan_step_update_in_resume_data(self):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        task = self._add_hierarchical_task_for_update_tests(tm)
        original_plan_step_statuses = list(task.details["plan_step_statuses"]) # Deep copy for comparison
        original_index = task.details["current_plan_step_index"]

        # Update with a general status, but no specific plan_step_update
        updated_task = tm.update_task_status(
            task.task_id,
            ActiveTaskStatus.EXECUTING_PROJECT_PLAN,
            step_desc="General update, no step processed",
            resume_data={"some_other_info": "value"} # resume_data exists but no plan_step_update
        )
        self.assertIsNotNone(updated_task)
        self.assertEqual(updated_task.status, ActiveTaskStatus.EXECUTING_PROJECT_PLAN)
        self.assertEqual(updated_task.current_step_description, "General update, no step processed")
        # Internal plan details should not have changed
        self.assertEqual(updated_task.details["plan_step_statuses"], original_plan_step_statuses)
        self.assertEqual(updated_task.details["current_plan_step_index"], original_index)
        # Progress might be passed explicitly to task.update_status if known, otherwise not recalculated here
        # Current implementation defaults progress to 0 if not explicitly passed and not calculated from plan_step_update
        self.assertEqual(updated_task.progress_percentage, 0)


    @patch('builtins.print')
    def test_update_hierarchical_task_step_id_not_found(self, mock_print):
        tm = TaskManager(notification_manager=self.mock_notification_manager, filepath=self.active_tasks_filepath)
        task = self._add_hierarchical_task_for_update_tests(tm)
        original_plan_step_statuses = list(task.details["plan_step_statuses"])

        resume_data_bad_step_id = {
            "plan_step_update": {"step_id": "s_non_existent", "status": "success"}
        }
        updated_task = tm.update_task_status(
            task.task_id,
            ActiveTaskStatus.EXECUTING_PROJECT_PLAN,
            resume_data=resume_data_bad_step_id
        )
        self.assertIsNotNone(updated_task)
        self.assertTrue(any("Plan step ID 's_non_existent' not found" in str(call_args) for call_args in mock_print.call_args_list))
        # Plan details should not have changed
        self.assertEqual(updated_task.details["plan_step_statuses"], original_plan_step_statuses)
