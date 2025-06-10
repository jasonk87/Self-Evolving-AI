from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum, auto
import uuid
from datetime import datetime, timezone

class ActiveTaskStatus(Enum):
    INITIALIZING = auto()
    PLANNING = auto()
    GENERATING_CODE = auto()
    AWAITING_CRITIC_REVIEW = auto()
    CRITIC_REVIEW_REJECTED = auto()
    CRITIC_REVIEW_APPROVED = auto()
    POST_MOD_TESTING = auto()
    POST_MOD_TEST_FAILED = auto()
    POST_MOD_TEST_PASSED = auto()
    APPLYING_CHANGES = auto()
    COMPLETED_SUCCESSFULLY = auto()
    FAILED_PRE_REVIEW = auto() # e.g. diff generation failed, original code not found
    FAILED_DURING_APPLY = auto() # e.g. file write error, AST error after review
    FAILED_UNKNOWN = auto()
    USER_CANCELLED = auto() # If user interaction allows cancelling tasks

class ActiveTaskType(Enum):
    AGENT_TOOL_CREATION = auto()
    AGENT_TOOL_MODIFICATION = auto()
    USER_PROJECT_SCAFFOLDING = auto()
    USER_PROJECT_FILE_GENERATION = auto()
    LEARNING_NEW_FACT = auto()
    PROCESSING_REFLECTION = auto() # When agent is acting on a reflection insight
    SUGGESTION_PROCESSING = auto() # For when SuggestionProcessor is working on one
    MISC_CODE_GENERATION = auto() # For tasks like scaffold generation, or detail generation not part of a larger flow
    PLANNING_CODE_STRUCTURE = auto() # For outline generation

@dataclass
class ActiveTask:
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    task_type: ActiveTaskType
    description: str # High-level description of what the agent is trying to achieve
    status: ActiveTaskStatus = ActiveTaskStatus.INITIALIZING
    status_reason: Optional[str] = None # Brief reason for current status, esp. for failures
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    related_item_id: Optional[str] = None # e.g., suggestion_id, reflection_id, project_id
    details: Dict[str, Any] = field(default_factory=dict) # Task-specific details, e.g., file paths, code snippets
    current_step_description: Optional[str] = None # e.g., "Generating code for function X", "Awaiting review from Critic 1"

    def update_status(self, new_status: ActiveTaskStatus, reason: Optional[str] = None, step_desc: Optional[str] = None):
        self.status = new_status
        if reason is not None:
            self.status_reason = reason
        elif new_status != self.status:
            self.status_reason = None

        if step_desc is not None:
            self.current_step_description = step_desc
        elif new_status != self.status:
             self.current_step_description = None

        self.last_updated_at = datetime.now(timezone.utc)

class TaskManager:
    def __init__(self, notification_manager: Optional[NotificationManager] = None): # Direct type hint
        self._active_tasks: Dict[str, ActiveTask] = {}
        self._completed_tasks_archive: List[ActiveTask] = []
        self._archive_limit = 100
        self.notification_manager = notification_manager

    def add_task(self, task_type: ActiveTaskType, description: str, related_item_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> ActiveTask:
        new_task = ActiveTask(task_type=task_type, description=description, related_item_id=related_item_id, details=details or {})
        self._active_tasks[new_task.task_id] = new_task
        # Basic logging, can be enhanced with a proper logger
        print(f"TaskManager: New task added: {new_task.task_id} - {description[:50]}... ({task_type.name})")
        return new_task

    def get_task(self, task_id: str) -> Optional[ActiveTask]:
        return self._active_tasks.get(task_id)

    def update_task_status(self, task_id: str, new_status: ActiveTaskStatus, reason: Optional[str] = None, step_desc: Optional[str] = None) -> Optional[ActiveTask]:
        task = self.get_task(task_id)
        if task:
            old_status = task.status
            task.update_status(new_status, reason, step_desc)
            print(f"TaskManager: Task {task_id} ({task.description[:30]}...) status updated from {old_status.name} to {new_status.name}. Step: {step_desc or 'N/A'}")

            terminal_statuses = [
                ActiveTaskStatus.COMPLETED_SUCCESSFULLY,
                ActiveTaskStatus.FAILED_PRE_REVIEW,
                ActiveTaskStatus.FAILED_DURING_APPLY,
                ActiveTaskStatus.FAILED_UNKNOWN,
                ActiveTaskStatus.USER_CANCELLED,
                ActiveTaskStatus.CRITIC_REVIEW_REJECTED,
                ActiveTaskStatus.POST_MOD_TEST_FAILED,
                ActiveTaskStatus.FAILED_CODE_GENERATION # Added to terminal statuses for notifications
            ]
            if new_status in terminal_statuses:
                print(f"TaskManager: Task {task_id} reached terminal status: {new_status.name}. Archiving.")
                if self.notification_manager:
                    notif_type = NotificationType.GENERAL_INFO # Default
                    if new_status == ActiveTaskStatus.COMPLETED_SUCCESSFULLY:
                        notif_type = NotificationType.TASK_COMPLETED_SUCCESSFULLY
                    elif new_status == ActiveTaskStatus.CRITIC_REVIEW_REJECTED:
                        notif_type = NotificationType.TASK_FAILED_CRITIC_REVIEW
                    elif new_status == ActiveTaskStatus.POST_MOD_TEST_FAILED:
                        notif_type = NotificationType.TASK_FAILED_POST_MOD_TEST
                    elif new_status == ActiveTaskStatus.FAILED_DURING_APPLY:
                        notif_type = NotificationType.TASK_FAILED_APPLY
                    elif new_status == ActiveTaskStatus.FAILED_CODE_GENERATION:
                        notif_type = NotificationType.TASK_FAILED_CODE_GENERATION
                    elif new_status in [ActiveTaskStatus.FAILED_PRE_REVIEW, ActiveTaskStatus.FAILED_UNKNOWN]:
                        notif_type = NotificationType.TASK_FAILED_UNKNOWN
                    elif new_status == ActiveTaskStatus.USER_CANCELLED:
                        notif_type = NotificationType.TASK_CANCELLED

                    summary = f"Task '{task.description[:50]}...' {new_status.name}."
                    if task.status_reason:
                        summary += f" Reason: {task.status_reason}"

                    self.notification_manager.add_notification(
                        event_type=notif_type,
                        summary_message=summary,
                        related_item_id=task.task_id,
                        related_item_type="task",
                        details_payload={"task_type": task.task_type.name, "description": task.description}
                    )
                self._archive_task(task_id)
        else:
            print(f"TaskManager: Error - Task {task_id} not found for status update.")
        return task

    def _archive_task(self, task_id: str):
        if task_id in self._active_tasks:
            task_to_archive = self._active_tasks.pop(task_id)
            self._completed_tasks_archive.append(task_to_archive)
            if len(self._completed_tasks_archive) > self._archive_limit:
                self._completed_tasks_archive.pop(0)

    def list_active_tasks(self, task_type_filter: Optional[ActiveTaskType] = None, status_filter: Optional[ActiveTaskStatus] = None) -> List[ActiveTask]:
        tasks = list(self._active_tasks.values())
        if task_type_filter:
            tasks = [t for t in tasks if t.task_type == task_type_filter]
        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    def list_archived_tasks(self, limit: int = 20) -> List[ActiveTask]:
        # Return most recent archived tasks
        return sorted(self._completed_tasks_archive, key=lambda t: t.last_updated_at, reverse=True)[:limit]


    def clear_all_tasks(self, clear_archive: bool = False):
        """Primarily for testing or reset purposes."""
        self._active_tasks.clear()
        if clear_archive:
            self._completed_tasks_archive.clear()
        print(f"TaskManager: All active tasks cleared. Archive cleared: {clear_archive}")

# Global instance (optional, consider dependency injection for better testability and flexibility)
# task_manager_instance = TaskManager()
# Commented out to prefer dependency injection where possible.
# If a global instance is needed for direct CLI or simple integrations, it can be uncommented.

from .notification_manager import NotificationManager, NotificationType # Moved import to top

if __name__ == '__main__': # pragma: no cover
    print("--- TaskManager Basic Test ---")
    # For testing with notifications, a mock NotificationManager might be useful here,
    # or ensuring NotificationManager can run standalone for its __main__ test.
    # For this simple test, we'll instantiate a real one if its dependencies are simple.
    # Assuming NotificationManager can be instantiated without complex deps for this basic test.

    # Since NotificationManager writes to a file, we might want to control its path here
    # or use a mock that doesn't write files.
    # For now, let it use its default path which might create a file during test.
    # Adding a basic try-except for robust testing if NM has issues.
    try:
        notif_mgr_for_test = NotificationManager()
    except Exception as e: # pylint: disable=broad-except
        print(f"TaskManager __main__: Could not initialize NotificationManager for test ({e}), using None.")
        notif_mgr_for_test = None

    tm = TaskManager(notification_manager=notif_mgr_for_test)

    # Add a task
    task1_desc = "Develop a new tool for calculating planetary orbits."
    task1 = tm.add_task(ActiveTaskType.AGENT_TOOL_CREATION, task1_desc, related_item_id="sugg_planet_orbit_tool")
    print(f"Added task 1: {task1.task_id}, Status: {task1.status.name}")

    task2_desc = "Learn about the user's preference for dark mode."
    task2 = tm.add_task(ActiveTaskType.LEARNING_NEW_FACT, task2_desc, details={"fact_candidate": "User likes dark mode"})
    print(f"Added task 2: {task2.task_id}, Status: {task2.status.name}")

    # Update task status
    tm.update_task_status(task1.task_id, ActiveTaskStatus.PLANNING, step_desc="PlannerAgent generating initial plan.")
    task1_retrieved = tm.get_task(task1.task_id)
    if task1_retrieved:
        print(f"Task 1 after update: {task1_retrieved.task_id}, Status: {task1_retrieved.status.name}, Step: {task1_retrieved.current_step_description}")

    tm.update_task_status(task2.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Fact learned and stored.")
    task2_retrieved = tm.get_task(task2.task_id) # Should be None as it's archived
    print(f"Task 2 after completion (from active list): {task2_retrieved}")


    # List active tasks
    print("\n--- Active Tasks ---")
    active_tasks = tm.list_active_tasks()
    if active_tasks:
        for task in active_tasks:
            print(f"- ID: {task.task_id}, Type: {task.task_type.name}, Status: {task.status.name}, Desc: {task.description[:40]}..., Step: {task.current_step_description}")
    else:
        print("No active tasks.")

    # List archived tasks
    print("\n--- Archived Tasks (most recent) ---")
    archived_tasks = tm.list_archived_tasks(limit=5)
    if archived_tasks:
        for task in archived_tasks:
            print(f"- ID: {task.task_id}, Type: {task.task_type.name}, Status: {task.status.name} (Reason: {task.status_reason}), Desc: {task.description[:40]}...")
    else:
        print("No archived tasks.")

    # Clear tasks (for testing cleanup)
    # tm.clear_all_tasks(clear_archive=True)
    # print("\n--- Active Tasks after clear_all_tasks ---")
    # print(tm.list_active_tasks())
    # print("\n--- Archived Tasks after clear_all_tasks with clear_archive=True ---")
    # print(tm.list_archived_tasks())

    print("\n--- TaskManager Test Finished ---")
