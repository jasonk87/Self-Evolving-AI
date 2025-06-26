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
    logger.info("--- DIAGNOSTIC: StartupServices - Checking for interrupted tasks... ---")
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

            logger.info(f"--- DIAGNOSTIC: StartupServices - Task {task.task_id} ('{task.description[:30]}...') was in {original_status.name}, marking as FAILED_INTERRUPTED. ---")

            task_manager.update_task_status(
                task.task_id,
                ActiveTaskStatus.FAILED_INTERRUPTED,
                reason=reason,
                step_desc="Task marked as interrupted on agent startup."
            )

            if notification_manager:
                notification_manager.add_notification(
                    NotificationType.TASK_INTERRUPTED,
                    f"Task '{task.description[:50]}...' (ID: {task.task_id}) was in state '{original_status.name}' and has been marked as interrupted.",
                    related_item_id=task.task_id,
                    related_item_type="task"
                )

    if interrupted_tasks_found == 0:
        logger.info("--- DIAGNOSTIC: StartupServices - No potentially interrupted tasks found. ---")
    else:
        logger.info(f"--- DIAGNOSTIC: StartupServices - Processed {interrupted_tasks_found} potentially interrupted task(s). ---")


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
                new_status=ActiveTaskStatus.FAILED_INTERRUPTED,
                reason=f"Task was in state '{ActiveTaskStatus.PLANNING.name}' and agent shutdown occurred.",
                step_desc="Task marked as interrupted on agent startup."
            ),
            MagicMock(
                task_id=task2_generating.task_id,
                new_status=ActiveTaskStatus.FAILED_INTERRUPTED,
                reason=f"Task was in state '{ActiveTaskStatus.GENERATING_CODE.name}' and agent shutdown occurred.",
                step_desc="Task marked as interrupted on agent startup."
            )
        ]

        # Check calls to update_task_status
        # Need to compare relevant parts of the call_args if full mock object comparison is tricky
        update_calls_actual = mock_task_manager.update_task_status.call_args_list
        assert len(update_calls_actual) == 2

        # Check task1
        call1_args, call1_kwargs = update_calls_actual[0]
        assert call1_args[0] == task1_planning.task_id
        assert call1_args[1] == ActiveTaskStatus.FAILED_INTERRUPTED
        assert call1_kwargs['reason'] == f"Task was in state '{ActiveTaskStatus.PLANNING.name}' and agent shutdown occurred."
        assert call1_kwargs['step_desc'] == "Task marked as interrupted on agent startup."

        # Check task2
        call2_args, call2_kwargs = update_calls_actual[1]
        assert call2_args[0] == task2_generating.task_id
        assert call2_args[1] == ActiveTaskStatus.FAILED_INTERRUPTED
        assert call2_kwargs['reason'] == f"Task was in state '{ActiveTaskStatus.GENERATING_CODE.name}' and agent shutdown occurred."
        assert call2_kwargs['step_desc'] == "Task marked as interrupted on agent startup."

        # Check calls to add_notification
        # add_notification should be called twice
        assert mock_notification_manager.add_notification.call_count == 2

        notif_call1_args, notif_call1_kwargs = mock_notification_manager.add_notification.call_args_list[0]
        assert notif_call1_args[0] == NotificationType.TASK_INTERRUPTED
        assert task1_planning.task_id in notif_call1_args[1]
        assert notif_call1_kwargs['related_item_id'] == task1_planning.task_id

        notif_call2_args, notif_call2_kwargs = mock_notification_manager.add_notification.call_args_list[1]
        assert notif_call2_args[0] == NotificationType.TASK_INTERRUPTED
        assert task2_generating.task_id in notif_call2_args[1]
        assert notif_call2_kwargs['related_item_id'] == task2_generating.task_id

        print("--- resume_interrupted_tasks Test Finished ---")

    asyncio.run(main_test())


from typing import Tuple # Added for type hinting

# Centralized Service Initialization
# Moved here from cli.py and app.py to avoid duplication
# and ensure consistency.

from ..llm_interface.ollama_client import OllamaProvider
from ..planning.hierarchical_planner import HierarchicalPlanner
from ..learning.learning import LearningAgent
from ..execution.action_executor import ActionExecutor
from ..planning.planning import PlannerAgent
from ..planning.execution import ExecutionAgent
from .orchestrator import DynamicOrchestrator
from ..config import get_data_dir
import os # For os.path.join and os.makedirs

async def initialize_core_services(
    existing_task_manager: Optional[TaskManager] = None,
    existing_notification_manager: Optional[NotificationManager] = None
) -> Tuple[DynamicOrchestrator, TaskManager, NotificationManager]:
    """
    Initializes and returns the core AI services including the DynamicOrchestrator.
    It can optionally reuse existing TaskManager and NotificationManager instances.
    """
    logger.info("--- DIAGNOSTIC: CoreServices - Initializing AI services... ---")

    if existing_notification_manager:
        notification_manager = existing_notification_manager
        logger.info("--- DIAGNOSTIC: CoreServices - Reusing existing NotificationManager. ---")
    else:
        notification_manager = NotificationManager()
        logger.info("--- DIAGNOSTIC: CoreServices - Initialized new NotificationManager. ---")

    if existing_task_manager:
        task_manager = existing_task_manager
        logger.info("--- DIAGNOSTIC: CoreServices - Reusing existing TaskManager. ---")
    else:
        task_manager = TaskManager(notification_manager=notification_manager)
        logger.info("--- DIAGNOSTIC: CoreServices - Initialized new TaskManager. ---")

    # Resume interrupted tasks - this should happen after TaskManager is ready
    try:
        logger.info("--- DIAGNOSTIC: CoreServices - Attempting to resume interrupted tasks... ---")
        await resume_interrupted_tasks(task_manager, notification_manager)
        logger.info("--- DIAGNOSTIC: CoreServices - Resumed interrupted tasks successfully. ---")
    except Exception as e_resume:
        logger.error(f"--- DIAGNOSTIC: CoreServices - CRITICAL ERROR during task resumption: {e_resume} ---", exc_info=True)
        # Depending on severity, we might want to raise this or handle gracefully

    try:
        llm_provider = OllamaProvider()
        logger.info("--- DIAGNOSTIC: CoreServices - LLM Provider initialized. ---")
    except Exception as e_llm:
        logger.error(f"--- DIAGNOSTIC: CoreServices - CRITICAL ERROR initializing OllamaProvider: {e_llm}. Orchestrator might be non-functional. ---", exc_info=True)
        llm_provider = None

    hierarchical_planner = None
    if llm_provider:
        try:
            hierarchical_planner = HierarchicalPlanner(llm_provider=llm_provider)
            logger.info("--- DIAGNOSTIC: CoreServices - Hierarchical Planner initialized. ---")
        except Exception as e_hp:
            logger.error(f"--- DIAGNOSTIC: CoreServices - ERROR initializing HierarchicalPlanner: {e_hp} ---", exc_info=True)
            hierarchical_planner = None
    else:
        logger.warning("--- DIAGNOSTIC: CoreServices - LLM Provider not available, skipping Hierarchical Planner initialization. ---")


    insights_file_name = "actionable_insights.json"
    insights_dir = get_data_dir()
    insights_file_path = os.path.join(insights_dir, insights_file_name)
    os.makedirs(os.path.dirname(insights_file_path), exist_ok=True)

    logger.info(f"--- DIAGNOSTIC: CoreServices - LearningAgent insights path set to: {insights_file_path} ---")

    learning_agent = LearningAgent(
        insights_filepath=insights_file_path,
        task_manager=task_manager,
        notification_manager=notification_manager
    )
    logger.info("--- DIAGNOSTIC: CoreServices - Learning Agent initialized. ---")

    action_executor = ActionExecutor(
        learning_agent=learning_agent,
        task_manager=task_manager,
        notification_manager=notification_manager
    )
    logger.info("--- DIAGNOSTIC: CoreServices - Action Executor initialized. ---")

    planner_agent = PlannerAgent()
    execution_agent = ExecutionAgent()
    logger.info("--- DIAGNOSTIC: CoreServices - Planner and Execution Agents initialized. ---")

    orchestrator = DynamicOrchestrator(
        planner=planner_agent,
        executor=execution_agent,
        learning_agent=learning_agent,
        action_executor=action_executor,
        task_manager=task_manager,
        notification_manager=notification_manager,
        hierarchical_planner=hierarchical_planner
    )
    # print("CoreServices: Dynamic Orchestrator initialized successfully.") # Replaced by logger below
    logger.info("--- DIAGNOSTIC: CoreServices - Dynamic Orchestrator initialized successfully. ---")

    # Start background services after all other core components are ready
    try:
        from .background_service import start_background_services
        logger.info("--- DIAGNOSTIC: CoreServices - Attempting to start background services via start_background_services()... ---")
        start_background_services() # This function is synchronous but creates an asyncio task.
                                    # It uses asyncio.get_running_loop().
        logger.info("--- DIAGNOSTIC: CoreServices - Call to start_background_services() completed (background task should be scheduled). ---")
    except ImportError:
        logger.error("--- DIAGNOSTIC: CoreServices - FAILED to import start_background_services from .background_service. Background tasks will NOT run. ---")
    except Exception as e_bg_start:
        logger.error(f"--- DIAGNOSTIC: CoreServices - CRITICAL ERROR during background service startup: {e_bg_start} ---", exc_info=True)

    return orchestrator, task_manager, notification_manager
