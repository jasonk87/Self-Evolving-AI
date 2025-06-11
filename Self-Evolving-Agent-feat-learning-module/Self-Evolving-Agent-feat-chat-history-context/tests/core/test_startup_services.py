import unittest
from unittest.mock import MagicMock, AsyncMock, call # Import call for checking multiple calls
import asyncio
import os
import sys
from datetime import datetime, timezone

# Add project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path: # pragma: no cover
    sys.path.insert(0, project_root)

from ai_assistant.core.startup_services import resume_interrupted_tasks
from ai_assistant.core.task_manager import TaskManager, ActiveTask, ActiveTaskStatus, ActiveTaskType
from ai_assistant.core.notification_manager import NotificationManager, NotificationType

class TestStartupServices(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_task_manager = MagicMock(spec=TaskManager)
        self.mock_notification_manager = MagicMock(spec=NotificationManager)
        # Ensure add_notification is an AsyncMock if it's awaited, but it's not in current design
        self.mock_notification_manager.add_notification = MagicMock()


    async def test_resume_interrupted_tasks_no_interrupted_tasks(self):
        # Scenario 1: No active tasks
        self.mock_task_manager.list_active_tasks.return_value = []
        await resume_interrupted_tasks(self.mock_task_manager, self.mock_notification_manager)
        self.mock_task_manager.update_task_status.assert_not_called()
        self.mock_notification_manager.add_notification.assert_not_called()

        # Scenario 2: Active tasks are all in terminal states
        completed_task = ActiveTask(task_type=ActiveTaskType.AGENT_TOOL_CREATION, description="Done", status=ActiveTaskStatus.COMPLETED_SUCCESSFULLY)
        failed_task = ActiveTask(task_type=ActiveTaskType.AGENT_TOOL_MODIFICATION, description="Failed", status=ActiveTaskStatus.FAILED_UNKNOWN)
        self.mock_task_manager.list_active_tasks.return_value = [completed_task, failed_task]

        await resume_interrupted_tasks(self.mock_task_manager, self.mock_notification_manager)
        self.mock_task_manager.update_task_status.assert_not_called()
        self.mock_notification_manager.add_notification.assert_not_called()

    async def test_resume_interrupted_tasks_marks_non_terminal_as_failed_interrupted(self):
        task1_planning = ActiveTask(task_id="t1", task_type=ActiveTaskType.AGENT_TOOL_CREATION, description="Tool X", status=ActiveTaskStatus.PLANNING)
        task2_generating = ActiveTask(task_id="t2", task_type=ActiveTaskType.USER_PROJECT_FILE_GENERATION, description="File Y", status=ActiveTaskStatus.GENERATING_CODE)
        task3_review_approved = ActiveTask(task_id="t3", task_type=ActiveTaskType.AGENT_TOOL_MODIFICATION, description="Mod Z", status=ActiveTaskStatus.CRITIC_REVIEW_APPROVED)

        self.mock_task_manager.list_active_tasks.return_value = [task1_planning, task2_generating, task3_review_approved]

        await resume_interrupted_tasks(self.mock_task_manager, self.mock_notification_manager)

        # Check update_task_status calls
        expected_update_calls = [
            call(task1_planning.task_id, ActiveTaskStatus.FAILED_INTERRUPTED,
                 reason=f"Task was in state '{ActiveTaskStatus.PLANNING.name}' and agent shutdown occurred.",
                 step_desc="Task marked as interrupted on agent startup."),
            call(task2_generating.task_id, ActiveTaskStatus.FAILED_INTERRUPTED,
                 reason=f"Task was in state '{ActiveTaskStatus.GENERATING_CODE.name}' and agent shutdown occurred.",
                 step_desc="Task marked as interrupted on agent startup."),
            call(task3_review_approved.task_id, ActiveTaskStatus.FAILED_INTERRUPTED,
                 reason=f"Task was in state '{ActiveTaskStatus.CRITIC_REVIEW_APPROVED.name}' and agent shutdown occurred.",
                 step_desc="Task marked as interrupted on agent startup.")
        ]
        self.mock_task_manager.update_task_status.assert_has_calls(expected_update_calls, any_order=True)
        self.assertEqual(self.mock_task_manager.update_task_status.call_count, 3)

        # Check add_notification calls
        expected_notification_calls = [
            call(NotificationType.TASK_INTERRUPTED,
                 f"Task '{task1_planning.description[:50]}...' (ID: {task1_planning.task_id}) was in state '{ActiveTaskStatus.PLANNING.name}' and has been marked as interrupted.",
                 related_item_id=task1_planning.task_id, related_item_type="task"),
            call(NotificationType.TASK_INTERRUPTED,
                 f"Task '{task2_generating.description[:50]}...' (ID: {task2_generating.task_id}) was in state '{ActiveTaskStatus.GENERATING_CODE.name}' and has been marked as interrupted.",
                 related_item_id=task2_generating.task_id, related_item_type="task"),
            call(NotificationType.TASK_INTERRUPTED,
                 f"Task '{task3_review_approved.description[:50]}...' (ID: {task3_review_approved.task_id}) was in state '{ActiveTaskStatus.CRITIC_REVIEW_APPROVED.name}' and has been marked as interrupted.",
                 related_item_id=task3_review_approved.task_id, related_item_type="task")
        ]
        self.mock_notification_manager.add_notification.assert_has_calls(expected_notification_calls, any_order=True)
        self.assertEqual(self.mock_notification_manager.add_notification.call_count, 3)


    async def test_resume_interrupted_tasks_skips_terminal_tasks(self):
        task_planning = ActiveTask(task_id="tp1", task_type=ActiveTaskType.PLANNING_CODE_STRUCTURE, description="Outline", status=ActiveTaskStatus.PLANNING)
        task_completed = ActiveTask(task_id="tc1",task_type=ActiveTaskType.LEARNING_NEW_FACT, description="Fact Z", status=ActiveTaskStatus.COMPLETED_SUCCESSFULLY)
        task_failed_already = ActiveTask(task_id="tf1", task_type=ActiveTaskType.USER_PROJECT_SCAFFOLDING, description="Scaffold", status=ActiveTaskStatus.FAILED_UNKNOWN)

        self.mock_task_manager.list_active_tasks.return_value = [task_planning, task_completed, task_failed_already]

        await resume_interrupted_tasks(self.mock_task_manager, self.mock_notification_manager)

        # update_task_status should only be called for task_planning
        self.mock_task_manager.update_task_status.assert_called_once_with(
            task_planning.task_id, ActiveTaskStatus.FAILED_INTERRUPTED,
            reason=f"Task was in state '{ActiveTaskStatus.PLANNING.name}' and agent shutdown occurred.",
            step_desc="Task marked as interrupted on agent startup."
        )

        # add_notification should only be called for task_planning
        self.mock_notification_manager.add_notification.assert_called_once_with(
            NotificationType.TASK_INTERRUPTED,
            f"Task '{task_planning.description[:50]}...' (ID: {task_planning.task_id}) was in state '{ActiveTaskStatus.PLANNING.name}' and has been marked as interrupted.",
            related_item_id=task_planning.task_id, related_item_type="task"
        )

    async def test_resume_interrupted_tasks_no_notification_manager(self):
        task_initializing = ActiveTask(task_id="ti1", task_type=ActiveTaskType.AGENT_TOOL_CREATION, description="New Tool", status=ActiveTaskStatus.INITIALIZING)
        self.mock_task_manager.list_active_tasks.return_value = [task_initializing]

        await resume_interrupted_tasks(self.mock_task_manager, notification_manager=None)

        # update_task_status should still be called
        self.mock_task_manager.update_task_status.assert_called_once_with(
            task_initializing.task_id, ActiveTaskStatus.FAILED_INTERRUPTED,
            reason=f"Task was in state '{ActiveTaskStatus.INITIALIZING.name}' and agent shutdown occurred.",
            step_desc="Task marked as interrupted on agent startup."
        )
        # add_notification should NOT be called
        self.mock_notification_manager.add_notification.assert_not_called()

if __name__ == '__main__': # pragma: no cover
    unittest.main()
