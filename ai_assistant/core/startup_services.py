# ai_assistant/core/startup_services.py
import asyncio
import logging # For logging in the future, using print for now
from typing import List, Optional

from .task_manager import TaskManager, ActiveTask, ActiveTaskStatus, ActiveTaskType
from .notification_manager import NotificationManager, NotificationType

# Placeholder for ActionExecutor if needed in more advanced resumption
# from ..execution.action_executor import ActionExecutor

logger = logging.getLogger(__name__)

async def resume_interrupted_tasks(
    task_manager: TaskManager,
    notification_manager: Optional[NotificationManager] = None
    # action_executor: Optional[ActionExecutor] = None # If direct re-execution is attempted
):
    """
    Checks for tasks that were active during the last session and might have been interrupted.
    Marks them as FAILED_INTERRUPTED. Future enhancements could attempt more sophisticated resumption.

    Args:
        task_manager: The TaskManager instance with loaded tasks.
        notification_manager: Optional NotificationManager to send notifications.
    """
    print("StartupServices: Checking for interrupted tasks...") # Replace with logger.info
    interrupted_tasks_found = 0

    # TaskManager loads tasks in its __init__. We just list them here.
    # Define statuses that indicate a task was in progress and not yet finished.
    non_terminal_statuses = [
        ActiveTaskStatus.INITIALIZING,
        ActiveTaskStatus.PLANNING,
        ActiveTaskStatus.GENERATING_CODE,
        ActiveTaskStatus.AWAITING_CRITIC_REVIEW,
        ActiveTaskStatus.CRITIC_REVIEW_APPROVED, # Approved but not yet applied/tested fully
        ActiveTaskStatus.POST_MOD_TESTING,
        ActiveTaskStatus.APPLYING_CHANGES
    ]

    # Get all tasks currently considered "active" by the TaskManager
    # (i.e., loaded from the persisted active_tasks.json)
    active_tasks_on_startup = task_manager.list_active_tasks(status_filter=None)

    for task in active_tasks_on_startup:
        if task.status in non_terminal_statuses:
            interrupted_tasks_found += 1
            original_status = task.status
            reason = f"Task was in state '{original_status.name}' and agent shutdown occurred."

            # RESUMPTION LOGIC:
            # Instead of failing, we want to resume.
            # However, if a task was in a middle of an action (like GENERATING_CODE), that context is lost.
            # It is safer to revert "in-flight" statuses to a state that triggers re-evaluation, like PLANNING.
            
            new_status = original_status
            if original_status in [
                ActiveTaskStatus.GENERATING_CODE,
                ActiveTaskStatus.POST_MOD_TESTING,
                ActiveTaskStatus.APPLYING_CHANGES,
                ActiveTaskStatus.CRITIC_REVIEW_APPROVED # Re-apply might be needed
            ]:
                new_status = ActiveTaskStatus.PLANNING
                reason = f"Resuming task. Reverted from volatile state '{original_status.name}' to PLANNING for safe retry."
            else:
                 reason = f"Resuming task from state '{original_status.name}'."

            print(f"StartupServices: Resuming Task {task.task_id} ('{task.description[:30]}...'). Status: {original_status.name} -> {new_status.name}")

            # Update status but DO NOT archive (status is not terminal)
            task_manager.update_task_status(
                task.task_id,
                new_status,
                reason=reason,
                step_desc="Task resumed on agent startup."
            )

            if notification_manager:
                notification_manager.add_notification(
                    NotificationType.GENERAL_INFO, # Use INFO instead of INTERRUPTED/FAILED
                    f"Resuming interrupted task '{task.description[:50]}...' (ID: {task.task_id}). Status: {new_status.name}",
                    related_item_id=task.task_id,
                    related_item_type="task"
                )

    if interrupted_tasks_found == 0:
        print("StartupServices: No potentially interrupted tasks found.") # Replace with logger.info
    else:
        print(f"StartupServices: Processed {interrupted_tasks_found} potentially interrupted task(s).") # Replace with logger.info


if __name__ == '__main__': # pragma: no cover
    # Basic test for resume_interrupted_tasks
    from unittest.mock import MagicMock, AsyncMock

    async def main_test():
        print("--- Testing resume_interrupted_tasks ---")

        # Mock TaskManager
        mock_task_manager = MagicMock(spec=TaskManager)

        # Create some mock tasks
        task1_planning = ActiveTask(task_type=ActiveTaskType.AGENT_TOOL_CREATION, description="Tool X", status=ActiveTaskStatus.PLANNING)
        task2_generating = ActiveTask(task_type=ActiveTaskType.USER_PROJECT_FILE_GENERATION, description="File Y", status=ActiveTaskStatus.GENERATING_CODE)
        task3_completed = ActiveTask(task_type=ActiveTaskType.LEARNING_NEW_FACT, description="Fact Z", status=ActiveTaskStatus.COMPLETED_SUCCESSFULLY)

        # Simulate TaskManager's list_active_tasks returning these
        mock_task_manager.list_active_tasks.return_value = [task1_planning, task2_generating, task3_completed]

        # Mock NotificationManager
        mock_notification_manager = MagicMock(spec=NotificationManager)
        mock_notification_manager.add_notification = MagicMock()

        # Call the function
        await resume_interrupted_tasks(mock_task_manager, mock_notification_manager)

        # Assertions
        # update_task_status should be called for non-terminal tasks
        expected_update_calls = [
            MagicMock(
                task_id=task1_planning.task_id,
                new_status=ActiveTaskStatus.PLANNING, # Resumed (kept original)
                reason=f"Resuming task from state '{ActiveTaskStatus.PLANNING.name}'.",
                step_desc="Task resumed on agent startup."
            ),
            MagicMock(
                task_id=task2_generating.task_id,
                new_status=ActiveTaskStatus.PLANNING, # Reverted to PLANNING
                reason=f"Resuming task. Reverted from volatile state '{ActiveTaskStatus.GENERATING_CODE.name}' to PLANNING for safe retry.",
                step_desc="Task resumed on agent startup."
            )
        ]

        # Check calls to update_task_status
        # Need to compare relevant parts of the call_args if full mock object comparison is tricky
        update_calls_actual = mock_task_manager.update_task_status.call_args_list
        assert len(update_calls_actual) == 2

        # Check task1
        call1_args, call1_kwargs = update_calls_actual[0]
        assert call1_args[0] == task1_planning.task_id
        assert call1_args[1] == ActiveTaskStatus.PLANNING
        assert call1_kwargs['reason'] == f"Resuming task from state '{ActiveTaskStatus.PLANNING.name}'."
        assert call1_kwargs['step_desc'] == "Task resumed on agent startup."

        # Check task2
        call2_args, call2_kwargs = update_calls_actual[1]
        assert call2_args[0] == task2_generating.task_id
        assert call2_args[1] == ActiveTaskStatus.PLANNING
        assert call2_kwargs['reason'] == f"Resuming task. Reverted from volatile state '{ActiveTaskStatus.GENERATING_CODE.name}' to PLANNING for safe retry."
        assert call2_kwargs['step_desc'] == "Task resumed on agent startup."

        # Check calls to add_notification
        # add_notification should be called twice
        assert mock_notification_manager.add_notification.call_count == 2

        notif_call1_args, notif_call1_kwargs = mock_notification_manager.add_notification.call_args_list[0]
        assert notif_call1_args[0] == NotificationType.GENERAL_INFO
        assert task1_planning.task_id in notif_call1_args[1]
        assert notif_call1_kwargs['related_item_id'] == task1_planning.task_id

        notif_call2_args, notif_call2_kwargs = mock_notification_manager.add_notification.call_args_list[1]
        assert notif_call2_args[0] == NotificationType.GENERAL_INFO
        assert task2_generating.task_id in notif_call2_args[1]
        assert "Status: PLANNING" in notif_call2_args[1]
        assert notif_call2_kwargs['related_item_id'] == task2_generating.task_id

        print("--- resume_interrupted_tasks Test Finished ---")

    asyncio.run(main_test())
