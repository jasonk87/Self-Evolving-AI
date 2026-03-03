import sys
import os

# Add project root to sys.path
# The script is expected to be in /app/, so Self-Evolving-Agent... is one level down.
project_root_parts = ["Self-Evolving-Agent-feat-learning-module", "Self-Evolving-Agent-feat-chat-history-context"]
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), *project_root_parts))

if project_root not in sys.path:
    sys.path.insert(0, project_root)

print(f"Python version: {sys.version}")
print(f"Project root: {project_root}")
print(f"sys.path: {sys.path}")

# Initialize to None
notification_manager_instance = None
task_manager_instance = None

print("\n--- Initializing Managers ---")
try:
    from ai_assistant.core.notification_manager import NotificationManager, NotificationStatus
    notification_manager_instance = NotificationManager()
    print("NotificationManager initialized successfully.")
except Exception as e:
    print(f"Error initializing NotificationManager: {type(e).__name__} - {e}")

if notification_manager_instance:
    try:
        from ai_assistant.core.task_manager import TaskManager
        # TaskManager constructor requires a notification_manager instance.
        task_manager_instance = TaskManager(notification_manager=notification_manager_instance)
        print("TaskManager initialized successfully.")
    except Exception as e:
        print(f"Error initializing TaskManager: {type(e).__name__} - {e}")
else:
    print("TaskManager cannot be initialized because NotificationManager failed or was not initialized.")

# Test /notifications list
print("\n--- Testing '/notifications list' equivalent ---")
if notification_manager_instance:
    try:
        print("--- Notifications List (Unread) ---")
        notifications = notification_manager_instance.get_notifications(status_filter=NotificationStatus.UNREAD, limit=5)
        if notifications:
            for n in notifications:
                print(f"  ID: {n.notification_id}, Type: {n.event_type.name}, Status: {n.status.name}, Summary: {n.summary_message}")
        else:
            print("  No unread notifications found.")
    except Exception as e:
        print(f"Error listing notifications: {type(e).__name__} - {e}")
else:
    print("NotificationManager could not be initialized. Skipping notifications list.")

# Test /tasks (using get_system_status_summary)
print("\n--- Testing '/tasks' equivalent (via get_system_status_summary) ---")
if task_manager_instance and notification_manager_instance:
    try:
        from ai_assistant.custom_tools.awareness_tools import get_system_status_summary
        print("--- Tasks (System Status Summary) ---")
        # Add some dummy tasks to see output
        try:
            from ai_assistant.core.task_manager import ActiveTaskType, ActiveTaskStatus
            task1_desc = "Test task 1 for summary."
            # Corrected argument order: description, task_type
            task1 = task_manager_instance.add_task(task1_desc, ActiveTaskType.AGENT_TOOL_CREATION, "summary_tool_test1")
            task_manager_instance.update_task_status(task1.task_id, ActiveTaskStatus.PLANNING, step_desc="Working on it (Planning)") # Corrected Enum

            task2_desc = "Test task 2, completed."
            # Corrected argument order: description, task_type
            task2 = task_manager_instance.add_task(task2_desc, ActiveTaskType.LEARNING_NEW_FACT, "summary_fact_test2")
            task_manager_instance.update_task_status(task2.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="All done.")
            print("Dummy tasks added for testing summary.")
        except Exception as e:
            print(f"Error adding dummy tasks: {type(e).__name__} - {e}")

        summary = get_system_status_summary(
            task_manager=task_manager_instance,
            notification_manager=notification_manager_instance,
            active_limit=3,
            archived_limit=3,
            unread_notifications_limit=3
        )
        print(summary)
    except Exception as e:
        print(f"Error getting system status summary for tasks: {type(e).__name__} - {e}")
else:
    print("TaskManager or NotificationManager could not be initialized/available for tasks test. Skipping tasks test.")

print("\n--- Script Finished ---")
