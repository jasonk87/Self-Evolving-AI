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
    FAILED_INTERRUPTED = auto() # Task was in a non-terminal state and agent restarted
    # For Hierarchical Project Execution
    EXECUTING_PROJECT_PLAN = auto()
    PROJECT_PLAN_FAILED_STEP = auto()
    # Added from later inspection
    FAILED_CODE_GENERATION = auto()


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
    HIERARCHICAL_PROJECT_EXECUTION = auto() # For executing a plan from HierarchicalPlanner

@dataclass
class ActiveTask:
    """
    Represents an active task being managed by the TaskManager.

    Attributes:
        description: High-level description of the task.
        task_type: The type of task (ActiveTaskType).
        task_id: Unique identifier for the task.
        status: Current status of the task (ActiveTaskStatus).
        status_reason: Optional brief reason for the current status, especially for failures.
        created_at: Timestamp of when the task was created.
        last_updated_at: Timestamp of the last update to the task.
        related_item_id: Optional ID of a related item (e.g., suggestion_id, project_id).
        details: Task-specific details. Structure depends on task_type.
            For HIERARCHICAL_PROJECT_EXECUTION, `details` is expected to contain:
            {
                "project_name": Optional[str],
                "user_goal": str, // The original user goal for the project
                "project_plan": List[Dict[str, Any]], // The plan from HierarchicalPlanner
                "current_plan_step_index": int, // 0-based index of the current/next step
                "plan_step_statuses": List[Dict[str, str]]
                    // e.g., [{"step_id": "1.1", "status": "success", "output_preview": "...", "error_message": null}, ...]
            }
        current_step_description: User-readable description of the current step.
        current_sub_step_name: More specific internal name for the current part of the step.
        progress_percentage: Optional overall progress percentage.
        error_count: Number of errors encountered.
        output_preview: Short preview of the last significant output.
        data_for_resume: Optional dictionary for storing state needed for task resumption.
    """
    description: str # High-level description of what the agent is trying to achieve
    task_type: ActiveTaskType
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    status: ActiveTaskStatus = ActiveTaskStatus.INITIALIZING
    status_reason: Optional[str] = None # Brief reason for current status, esp. for failures
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    related_item_id: Optional[str] = None # e.g., suggestion_id, reflection_id, project_id
    details: Dict[str, Any] = field(default_factory=dict) # Task-specific details, e.g., file paths, code snippets
    current_step_description: Optional[str] = None # e.g., "Generating code for function X", "Awaiting review from Critic 1"
    current_sub_step_name: Optional[str] = None
    progress_percentage: Optional[int] = None
    error_count: int = 0
    output_preview: Optional[str] = None
    data_for_resume: Optional[Dict[str, Any]] = None

    def update_status(self,
                      new_status: ActiveTaskStatus,
                      reason: Optional[str] = None,
                      step_desc: Optional[str] = None,
                      sub_step_name: Optional[str] = None,
                      progress: Optional[int] = None,
                      is_error_increment: bool = False,
                      out_preview: Optional[str] = None,
                      resume_data: Optional[Dict[str, Any]] = None):

        self.status = new_status

        if reason is not None: self.status_reason = reason
        elif new_status != self.status: self.status_reason = None

        if step_desc is not None: self.current_step_description = step_desc

        if sub_step_name is not None: self.current_sub_step_name = sub_step_name
        if progress is not None: self.progress_percentage = progress
        if out_preview is not None: self.output_preview = out_preview[:250]
        if resume_data is not None: self.data_for_resume = resume_data

        if is_error_increment:
            self.error_count += 1

        self.last_updated_at = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.name,
            "description": self.description,
            "status": self.status.name,
            "status_reason": self.status_reason,
            "created_at": self.created_at.isoformat(),
            "last_updated_at": self.last_updated_at.isoformat(),
            "related_item_id": self.related_item_id,
            "details": self.details,
            "current_step_description": self.current_step_description,
            "current_sub_step_name": self.current_sub_step_name,
            "progress_percentage": self.progress_percentage,
            "error_count": self.error_count,
            "output_preview": self.output_preview,
            "data_for_resume": self.data_for_resume,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActiveTask':
        return cls(
            description=data["description"], # Corrected order
            task_type=ActiveTaskType[data["task_type"]], # Corrected order
            task_id=data["task_id"],
            status=ActiveTaskStatus[data["status"]],
            status_reason=data.get("status_reason"),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_updated_at=datetime.fromisoformat(data["last_updated_at"]),
            related_item_id=data.get("related_item_id"),
            details=data.get("details", {}),
            current_step_description=data.get("current_step_description"),
            current_sub_step_name=data.get("current_sub_step_name"),
            progress_percentage=data.get("progress_percentage"),
            error_count=data.get("error_count", 0),
            output_preview=data.get("output_preview"),
            data_for_resume=data.get("data_for_resume"),
        )

ACTIVE_TASKS_FILE_NAME = "active_tasks.json"
import os
import json
from .notification_manager import NotificationManager, NotificationType


def get_data_dir():
    """Gets the application's data directory."""
    return os.path.join(os.path.expanduser("~"), ".ai_assistant_data")

def _ensure_data_dir_exists():
    """Ensures the application's data directory exists."""
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


class TaskManager:
    def __init__(self, notification_manager: Optional['NotificationManager'] = None, filepath: Optional[str] = None):
        self._active_tasks: Dict[str, ActiveTask] = {}
        self._completed_tasks_archive: List[ActiveTask] = []
        self._archive_limit = 100
        self.notification_manager = notification_manager

        _ensure_data_dir_exists()
        self.active_tasks_filepath = filepath or os.path.join(get_data_dir(), ACTIVE_TASKS_FILE_NAME)
        self._load_active_tasks()


    def _load_active_tasks(self):
        try:
            if os.path.exists(self.active_tasks_filepath) and os.path.getsize(self.active_tasks_filepath) > 0:
                with open(self.active_tasks_filepath, 'r', encoding='utf-8') as f:
                    tasks_data = json.load(f)
                    for task_dict in tasks_data:
                        try:
                            task = ActiveTask.from_dict(task_dict)
                            self._active_tasks[task.task_id] = task
                        except (KeyError, ValueError) as e: # pragma: no cover
                             print(f"TaskManager: Error deserializing task from dict {task_dict.get('task_id', 'UnknownID')}: {e}")
            else: # pragma: no cover
                self._active_tasks = {}
        except FileNotFoundError: # pragma: no cover
            self._active_tasks = {}
            print(f"TaskManager: Active tasks file '{self.active_tasks_filepath}' not found. Initializing empty task list.")
        except (json.JSONDecodeError, ValueError) as e: # pragma: no cover
            print(f"TaskManager: Error loading active tasks from '{self.active_tasks_filepath}': {e}. Initializing empty task list.")
            self._active_tasks = {}

    def _save_active_tasks(self):
        try:
            _ensure_data_dir_exists()
            tasks_to_save = [task.to_dict() for task in self._active_tasks.values()]
            with open(self.active_tasks_filepath, 'w', encoding='utf-8') as f:
                json.dump(tasks_to_save, f, indent=2, ensure_ascii=False)
        except IOError as e: # pragma: no cover
            print(f"TaskManager: Error saving active tasks to '{self.active_tasks_filepath}': {e}")
        except Exception as e_gen: # pragma: no cover
            print(f"TaskManager: An unexpected error occurred during _save_active_tasks: {e_gen}")


    def add_task(self, description: str, task_type: ActiveTaskType, related_item_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> ActiveTask:
        # Ensure description and task_type are first, as per dataclass definition
        initialized_details = details or {}

        if task_type == ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION:
            if not all(k in initialized_details for k in ['project_plan', 'user_goal']):
                print(f"TaskManager Error: HIERARCHICAL_PROJECT_EXECUTION task created for '{description}' without 'project_plan' or 'user_goal' in details. Details provided: {initialized_details}")
                plan = []
            else:
                plan = initialized_details.get('project_plan', [])
                if not isinstance(plan, list):
                    print(f"TaskManager Error: 'project_plan' for HIERARCHICAL_PROJECT_EXECUTION task '{description}' must be a list. Got: {type(plan)}. Details: {initialized_details}")
                    plan = []

            initial_step_statuses = []
            for step in plan:
                if isinstance(step, dict):
                    initial_step_statuses.append({
                        "step_id": step.get("step_id", f"unknown_id_{uuid.uuid4().hex[:4]}"),
                        "description": step.get("description", "No step description provided."),
                        "status": "pending",
                        "error_message": None,
                        "output_preview": None
                    })
                else: # pragma: no cover
                    print(f"TaskManager Warning: Invalid step found in project_plan for task '{description}'. Step: {step}. Skipping this step status initialization.")

            initialized_details.update({
                "current_plan_step_index": 0,
                "plan_step_statuses": initial_step_statuses,
            })

        new_task = ActiveTask(
            description=description,
            task_type=task_type,
            related_item_id=related_item_id,
            details=initialized_details
        )
        self._active_tasks[new_task.task_id] = new_task
        self._save_active_tasks()
        print(f"TaskManager: New task added: {new_task.task_id} - {description[:50]}... ({task_type.name})")
        return new_task

    def get_task(self, task_id: str) -> Optional[ActiveTask]:
        return self._active_tasks.get(task_id)

    def update_task_status(self,
                           task_id: str,
                           new_status: ActiveTaskStatus,
                           reason: Optional[str] = None,
                           step_desc: Optional[str] = None,
                           sub_step_name: Optional[str] = None,
                           progress: Optional[int] = None,
                           is_error_increment: bool = False,
                           out_preview: Optional[str] = None,
                           resume_data: Optional[Dict[str, Any]] = None
                           ) -> Optional[ActiveTask]:
        task = self.get_task(task_id)
        if task:
            old_status = task.status

            if task.task_type == ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION and resume_data:
                plan_step_update_data = resume_data.get("plan_step_update")
                if plan_step_update_data and isinstance(plan_step_update_data, dict):
                    step_id_to_update = plan_step_update_data.get("step_id")
                    new_step_status = plan_step_update_data.get("status")
                    step_error_message = plan_step_update_data.get("error_message")
                    step_output_preview = plan_step_update_data.get("output_preview")

                    found_step = False
                    if 'plan_step_statuses' in task.details and isinstance(task.details['plan_step_statuses'], list):
                        for step_status_entry in task.details['plan_step_statuses']:
                            if step_status_entry.get("step_id") == step_id_to_update:
                                step_status_entry["status"] = new_step_status
                                step_status_entry["error_message"] = step_error_message
                                step_status_entry["output_preview"] = step_output_preview
                                found_step = True
                                print(f"TaskManager: Updated plan step '{step_id_to_update}' to status '{new_step_status}' for task '{task_id}'.")
                                break
                        if not found_step: # pragma: no cover
                            print(f"TaskManager Warning: Plan step ID '{step_id_to_update}' not found in task '{task_id}' details.")

                    if found_step:
                        completed_steps = sum(1 for s in task.details.get('plan_step_statuses', []) if s.get("status") == "success")
                        total_steps = len(task.details.get('project_plan', []))
                        if total_steps > 0:
                            progress = int((completed_steps / total_steps) * 100)
                        else: # pragma: no cover
                            progress = 0

                        current_idx = task.details.get("current_plan_step_index", 0)
                        current_step_in_plan = task.details.get('project_plan', [])[current_idx] if current_idx < total_steps else None

                        if new_step_status == "success" and current_step_in_plan and current_step_in_plan.get("step_id") == step_id_to_update:
                             task.details["current_plan_step_index"] = current_idx + 1

                        new_current_idx = task.details.get("current_plan_step_index", 0)
                        if new_status == ActiveTaskStatus.COMPLETED_SUCCESSFULLY:
                            step_desc = "Project plan executed successfully."
                        elif new_status == ActiveTaskStatus.PROJECT_PLAN_FAILED_STEP:
                            failed_step_desc = plan_step_update_data.get("description", step_id_to_update)
                            step_desc = f"Project plan failed at step: '{failed_step_desc}'. Reason: {step_error_message or 'Unknown error'}"
                        elif new_current_idx < total_steps:
                            next_step_in_plan = task.details.get('project_plan', [])[new_current_idx]
                            step_desc = f"Executing plan: {next_step_in_plan.get('description', 'Next step')}"
                        else:
                            step_desc = "All plan steps processed."

            task.update_status(new_status, reason, step_desc, sub_step_name, progress, is_error_increment, out_preview, resume_data)
            self._save_active_tasks()
            print(f"TaskManager: Task {task_id} ({task.description[:30]}...) status updated from {old_status.name} to {new_status.name}. Step: {task.current_step_description or 'N/A'}")

            terminal_statuses = [
                ActiveTaskStatus.COMPLETED_SUCCESSFULLY,
                ActiveTaskStatus.FAILED_PRE_REVIEW,
                ActiveTaskStatus.FAILED_DURING_APPLY,
                ActiveTaskStatus.FAILED_UNKNOWN,
                ActiveTaskStatus.USER_CANCELLED,
                ActiveTaskStatus.CRITIC_REVIEW_REJECTED,
                ActiveTaskStatus.POST_MOD_TEST_FAILED,
                ActiveTaskStatus.FAILED_CODE_GENERATION,
                ActiveTaskStatus.FAILED_INTERRUPTED
            ]
            if new_status in terminal_statuses:
                print(f"TaskManager: Task {task_id} reached terminal status: {new_status.name}. Archiving.")
                if self.notification_manager:
                    notif_type = NotificationType.GENERAL_INFO
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
                    elif new_status == ActiveTaskStatus.FAILED_INTERRUPTED:
                        notif_type = getattr(NotificationType, "TASK_INTERRUPTED", NotificationType.TASK_FAILED_UNKNOWN)
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
            if len(self._completed_tasks_archive) > self._archive_limit: # pragma: no cover
                self._completed_tasks_archive.pop(0)
            self._save_active_tasks()

    def list_active_tasks(self, task_type_filter: Optional[ActiveTaskType] = None, status_filter: Optional[ActiveTaskStatus] = None) -> List[ActiveTask]:
        tasks = list(self._active_tasks.values())
        if task_type_filter:
            tasks = [t for t in tasks if t.task_type == task_type_filter]
        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    def list_archived_tasks(self, limit: int = 20) -> List[ActiveTask]:
        return sorted(self._completed_tasks_archive, key=lambda t: t.last_updated_at, reverse=True)[:limit]


    def clear_all_tasks(self, clear_archive: bool = False):
        """Primarily for testing or reset purposes."""
        self._active_tasks.clear()
        if clear_archive:
            self._completed_tasks_archive.clear()
        self._save_active_tasks()
        print(f"TaskManager: All active tasks cleared. Archive cleared: {clear_archive}")


if __name__ == '__main__': # pragma: no cover
    print("--- TaskManager Persistence Test ---")

    test_file_dir = os.path.join(os.path.dirname(__file__), ".test_data")
    os.makedirs(test_file_dir, exist_ok=True)
    test_active_tasks_file = os.path.join(test_file_dir, "test_active_tasks.json")

    if os.path.exists(test_active_tasks_file):
        os.remove(test_active_tasks_file)

    notif_mgr_for_test = NotificationManager()

    print("\n--- Instance 1 Operations ---")
    tm1 = TaskManager(notification_manager=notif_mgr_for_test, filepath=test_active_tasks_file)

    task1_desc = "Develop a new tool for calculating planetary orbits."
    # Corrected add_task call
    task1 = tm1.add_task(description=task1_desc, task_type=ActiveTaskType.AGENT_TOOL_CREATION, related_item_id="sugg_planet_orbit_tool")
    print(f"Added task 1: {task1.task_id}, Status: {task1.status.name}")

    task2_desc = "Learn about the user's preference for dark mode."
    # Corrected add_task call
    task2 = tm1.add_task(description=task2_desc, task_type=ActiveTaskType.LEARNING_NEW_FACT, details={"fact_candidate": "User likes dark mode"})
    print(f"Added task 2: {task2.task_id}, Status: {task2.status.name}")

    tm1.update_task_status(
        task1.task_id, ActiveTaskStatus.PLANNING,
        step_desc="PlannerAgent generating initial plan.",
        sub_step_name="Outline Generation",
        progress=25,
        out_preview="Generated outline: ...",
        resume_data={"current_component": "component_A"}
    )
    task1_retrieved_tm1 = tm1.get_task(task1.task_id)
    if task1_retrieved_tm1:
        print(f"Task 1 (TM1) after update: Status: {task1_retrieved_tm1.status.name}, SubStep: {task1_retrieved_tm1.current_sub_step_name}, Progress: {task1_retrieved_tm1.progress_percentage}%")
        print(f"  Error Count: {task1_retrieved_tm1.error_count}, Output Preview: '{task1_retrieved_tm1.output_preview}'")
        print(f"  Resume Data: {task1_retrieved_tm1.data_for_resume}")

    tm1.update_task_status(task1.task_id, ActiveTaskStatus.GENERATING_CODE, is_error_increment=True)


    print("\n--- Instance 2 Operations (Loading from file) ---")
    tm2 = TaskManager(notification_manager=notif_mgr_for_test, filepath=test_active_tasks_file)

    task1_reloaded_tm2 = tm2.get_task(task1.task_id)
    task2_reloaded_tm2 = tm2.get_task(task2.task_id)

    print("\nReloaded Active Tasks (TM2):")
    if task1_reloaded_tm2:
        print(f"- Task 1 Reloaded: ID: {task1_reloaded_tm2.task_id}, Type: {task1_reloaded_tm2.task_type.name}, Status: {task1_reloaded_tm2.status.name}")
        print(f"  SubStep: {task1_reloaded_tm2.current_sub_step_name}, Progress: {task1_reloaded_tm2.progress_percentage}%, Errors: {task1_reloaded_tm2.error_count}")
        print(f"  Output Preview: '{task1_reloaded_tm2.output_preview}', Resume Data: {task1_reloaded_tm2.data_for_resume}")
        assert task1_reloaded_tm2.error_count == 1
        assert task1_reloaded_tm2.progress_percentage == 25
        assert task1_reloaded_tm2.status == ActiveTaskStatus.GENERATING_CODE
    else:
        print(f"Error: Task 1 ({task1.task_id}) not found in TM2 after reload.")

    if task2_reloaded_tm2:
        print(f"- Task 2 Reloaded: ID: {task2_reloaded_tm2.task_id}, Type: {task2_reloaded_tm2.task_type.name}, Status: {task2_reloaded_tm2.status.name}")
    else:
        print(f"Error: Task 2 ({task2.task_id}) not found in TM2 after reload.")


    print("\n--- Archiving Task 2 (TM2) ---")
    tm2.update_task_status(task2.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Fact learned and stored.")
    task2_after_archive_tm2 = tm2.get_task(task2.task_id)
    print(f"Task 2 (TM2) after completion (from active list): {task2_after_archive_tm2}")
    assert task2_after_archive_tm2 is None, "Task 2 should be archived and not in active list of TM2."

    print("\nArchived Tasks (TM2):")
    archived_tm2 = tm2.list_archived_tasks(limit=5)
    task2_found_in_archive = any(t.task_id == task2.task_id for t in archived_tm2)
    print(f"Task 2 found in TM2 archive: {task2_found_in_archive}")
    assert task2_found_in_archive, "Task 2 was not found in TM2's archive."

    print("\n--- Instance 3 Operations (Verifying archive persistence) ---")
    tm3 = TaskManager(notification_manager=notif_mgr_for_test, filepath=test_active_tasks_file)
    task1_reloaded_tm3 = tm3.get_task(task1.task_id)
    task2_reloaded_tm3 = tm3.get_task(task2.task_id)

    print(f"Task 1 (TM3) still active: {task1_reloaded_tm3 is not None}")
    assert task1_reloaded_tm3 is not None, "Task 1 should still be active in TM3."
    print(f"Task 2 (TM3) (should be archived) active status: {task2_reloaded_tm3 is not None}")
    assert task2_reloaded_tm3 is None, "Task 2 should remain archived (not active) in TM3."


    if os.path.exists(test_active_tasks_file):
        os.remove(test_active_tasks_file)
    if os.path.exists(test_file_dir):
        try:
            os.rmdir(test_file_dir)
        except OSError: # pragma: no cover
            print(f"Note: Test directory {test_file_dir} not empty, not removed.")

    print("\n--- TaskManager Persistence Test Finished ---")
