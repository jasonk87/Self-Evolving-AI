# ai_assistant/custom_tools/awareness_tools.py
from typing import List, Dict, Any, Optional
from ai_assistant.core.task_manager import TaskManager, ActiveTask, ActiveTaskStatus, ActiveTaskType
from ai_assistant.core.notification_manager import NotificationManager, NotificationStatus, Notification # Added
from datetime import datetime, timezone, timedelta # datetime was already imported
from enum import Enum, auto
from dataclasses import asdict

# Assuming direct import from module for simplicity in tool definition
# In a real setup, these might be part of a service layer accessible via context
from ai_assistant.core.suggestion_manager import find_suggestion, list_suggestions # Added list_suggestions
from ai_assistant.core.project_manager import find_project


def get_system_status_summary(
    task_manager: Optional[TaskManager] = None,
    notification_manager: Optional[NotificationManager] = None, # New parameter
    active_limit: int = 5,
    archived_limit: int = 3,
    unread_notifications_limit: int = 3 # New parameter
) -> str:
    """
    Provides a summary of the system's current and recently completed tasks,
    and a summary of recent unread notifications.

    Args:
        task_manager: An instance of the TaskManager.
        notification_manager: An instance of the NotificationManager.
        active_limit: Max number of active tasks to detail.
        archived_limit: Max number of archived tasks to detail.
        unread_notifications_limit: Max number of unread notifications to detail.

    Returns:
        A string summarizing the system status and notifications.
    """
    # Convert limit parameters to integers with defaults if conversion fails
    try:
        active_limit = int(active_limit)
    except (ValueError, TypeError):
        active_limit = 5 # Default if conversion fails
    try:
        archived_limit = int(archived_limit)
    except (ValueError, TypeError):
        archived_limit = 3 # Default
    try:
        unread_notifications_limit = int(unread_notifications_limit)
    except (ValueError, TypeError):
        unread_notifications_limit = 3 # Default

    summary_lines = ["System Status Summary:"]
    active_tasks = []
    archived_tasks = []

    if not task_manager:
        summary_lines.append("\nTaskManager not available.") # Added newline for better formatting
    else:
        active_tasks = task_manager.list_active_tasks()
        # archived_limit is now guaranteed to be an int
        archived_tasks = task_manager.list_archived_tasks(limit=archived_limit)

    # This was a duplicate, removing: summary_lines = ["System Status Summary:"]

    num_active_tasks = len(active_tasks) # Store length once
    summary_lines.append(f"\nActive Tasks ({num_active_tasks} total):")
    if not active_tasks: # Check if list is empty
        summary_lines.append("  No active tasks currently.")
    else:
        for i, task in enumerate(active_tasks):
            if i >= active_limit:
                summary_lines.append(f"  ... and {len(active_tasks) - active_limit} more active tasks.")
                break
            details_str = f" (ID: {task.task_id}, Related: {task.related_item_id or 'N/A'})"
            reason_str = f" Reason: {task.status_reason}" if task.status_reason else ""
            step_str = f" Step: {task.current_step_description}" if task.current_step_description else ""
            summary_lines.append(
                f"  - {task.description[:60]}... ({task.task_type.name}) - Status: {task.status.name}{step_str}{reason_str}{details_str}"
            )

    summary_lines.append(f"\nRecently Completed/Archived Tasks ({len(archived_tasks)} shown, up to {archived_limit}):")
    if not archived_tasks:
        summary_lines.append("  No recently archived tasks.")
    else:
        for task in archived_tasks:
            details_str = f" (ID: {task.task_id}, Related: {task.related_item_id or 'N/A'})"
            reason_str = f" Reason: {task.status_reason}" if task.status_reason else ""
            summary_lines.append(
                f"  - {task.description[:60]}... ({task.task_type.name}) - Final Status: {task.status.name}{reason_str}{details_str}"
            )

    if active_tasks:
        summary_lines.append("\nActive Task Status Breakdown:")
        status_counts: Dict[ActiveTaskStatus, int] = {}
        for task in active_tasks:
            status_counts[task.status] = status_counts.get(task.status, 0) + 1
        for status_key, count in status_counts.items():
            summary_lines.append(f"  - {status_key.name}: {count}")

    if not notification_manager:
        summary_lines.append("\nNotificationManager not available.")
    else:
        unread_notifications = notification_manager.get_notifications(
            status_filter=NotificationStatus.UNREAD,
            limit=unread_notifications_limit
        )
        num_unread_actually_shown = len(unread_notifications)
        # Get total unread count if manager supports it, otherwise rely on what was fetched.
        # For simplicity, we'll just state how many are shown from the limit.
        # A more accurate total unread count would require another call or method in NotificationManager.
        total_unread_count_note = f"({num_unread_actually_shown} shown, up to {unread_notifications_limit} displayed)"

        summary_lines.append(f"\nUnread Notifications {total_unread_count_note}:")
        if not unread_notifications:
            summary_lines.append("  No unread notifications.")
        else:
            for i, notification in enumerate(unread_notifications):
                # Basic formatting, can be enhanced
                ts_str = "Unknown Time"
                if isinstance(notification.timestamp, datetime):
                    ts_str = notification.timestamp.strftime('%Y-%m-%d %H:%M')

                summary_msg_display = notification.summary_message
                if len(summary_msg_display) > 70:
                    summary_msg_display = summary_msg_display[:67] + "..."

                summary_lines.append(
                    f"  - [{ts_str}] {notification.event_type.name}: {summary_msg_display} (ID: {notification.notification_id})"
                )

    return "\n".join(summary_lines)

# Conceptual Schema
GET_SYSTEM_STATUS_SUMMARY_SCHEMA = {
    "name": "get_system_status_summary",
    "description": "Provides a summary of current system activity, including active/archived tasks and recent unread notifications.",
    "parameters": [
        # task_manager and notification_manager are runtime-injected dependencies, not direct user/LLM parameters.
        {"name": "active_limit", "type": "int", "description": "Optional. Max active tasks to detail (default 5)."},
        {"name": "archived_limit", "type": "int", "description": "Optional. Max archived tasks to detail (default 3)."},
        {"name": "unread_notifications_limit", "type": "int", "description": "Optional. Max unread notifications to detail (default 3)."}
    ],
    "returns": {"type": "str", "description": "A multi-line string summarizing system status and notifications."}
}


class ItemTypeForDetails(Enum):
    TASK = "task"
    SUGGESTION = "suggestion"
    PROJECT = "project"

def get_item_details_by_id(
    item_id: str,
    item_type: str,
    task_manager: Optional[TaskManager] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieves details for a specific item (task, suggestion, or project) by its ID.

    Args:
        item_id: The ID of the item to retrieve.
        item_type: The type of item (e.g., "task", "suggestion", "project").
        task_manager: An instance of the TaskManager (required if item_type is "task").

    Returns:
        A dictionary containing the item's details, or an error dictionary if not found or type is invalid.
    """
    try:
        resolved_item_type = ItemTypeForDetails(item_type.lower())
    except ValueError:
        return {"error": f"Invalid item_type '{item_type}'. Valid types are: {[t.value for t in ItemTypeForDetails]}"}

    details: Optional[Dict[str, Any]] = None

    if resolved_item_type == ItemTypeForDetails.TASK:
        if not task_manager:
            return {"error": "TaskManager instance not provided for item_type 'task'."}
        task_obj = task_manager.get_task(item_id)
        if task_obj:
            details = asdict(task_obj)
            if 'status' in details and isinstance(details['status'], Enum):
                details['status'] = details['status'].name
            if 'task_type' in details and isinstance(details['task_type'], Enum):
                details['task_type'] = details['task_type'].name
            if 'created_at' in details and isinstance(details['created_at'], datetime):
                details['created_at'] = details['created_at'].isoformat()
            if 'last_updated_at' in details and isinstance(details['last_updated_at'], datetime):
                details['last_updated_at'] = details['last_updated_at'].isoformat()

    elif resolved_item_type == ItemTypeForDetails.SUGGESTION:
        details = find_suggestion(item_id)
    elif resolved_item_type == ItemTypeForDetails.PROJECT:
        details = find_project(item_id)

    if not details and resolved_item_type:
        return {"error": f"{resolved_item_type.value.capitalize()} with ID '{item_id}' not found."}

    return details

# Conceptual Schema
GET_ITEM_DETAILS_BY_ID_SCHEMA = {
    "name": "get_item_details_by_id",
    "description": "Retrieves details for a specific system item (task, suggestion, or project) using its ID and type.",
    "parameters": [
        {"name": "item_id", "type": "str", "description": "The unique ID of the item."},
        {"name": "item_type", "type": "str", "description": "The type of item. Valid values: 'task', 'suggestion', 'project'."}
    ],
    "returns": {
        "type": "dict",
        "description": "A dictionary containing the item's details, or an error dictionary if not found or type is invalid."
    }
}

def list_formatted_suggestions(status_filter: Optional[str] = "pending") -> List[Dict[str, Any]]:
    """
    Lists suggestions, optionally filtered by status, and formats them.

    Args:
        status_filter: Optional. Filter suggestions by status (e.g., "pending", "approved").
                       If "all", all suggestions are returned. Defaults to "pending".

    Returns:
        A list of dictionaries, where each dictionary contains key details of a suggestion.
        Returns an empty list if no suggestions match or if suggestion_manager is unavailable.
    """
    all_suggs = list_suggestions() # This is imported from suggestion_manager
    if not all_suggs:
        return []

    filtered_suggestions: List[Dict[str, Any]] = []

    status_to_filter = status_filter.lower() if status_filter else "pending"

    for sugg in all_suggs:
        # Ensure sugg is a dict and has 'status' key before lowercasing
        current_status = sugg.get('status', '').lower() if isinstance(sugg, dict) else ''

        if status_to_filter == "all" or current_status == status_to_filter:
            # Format the suggestion into the desired dictionary structure
            formatted_sugg = {
                "suggestion_id": sugg.get("suggestion_id", "N/A"),
                "type": sugg.get("type", "N/A"), # Or InsightType(sugg.get("type")).name if it's an Enum
                "description": sugg.get("description", "N/A"),
                "status": sugg.get("status", "N/A"),
                "created_at": sugg.get("creation_timestamp", "N/A") # Align with ActionableInsight field name
            }
            # Handle if 'type' is an Enum object
            if isinstance(formatted_sugg["type"], Enum):
                formatted_sugg["type"] = formatted_sugg["type"].name
            filtered_suggestions.append(formatted_sugg)

    return filtered_suggestions

# Conceptual Schema
LIST_FORMATTED_SUGGESTIONS_SCHEMA = {
    "name": "list_formatted_suggestions",
    "description": "Lists system-generated or user-added suggestions, optionally filtered by status (e.g., pending, approved). Useful for reviewing items that might lead to agent improvements or new tasks.",
    "parameters": [
        {"name": "status_filter", "type": "str", "description": "Optional. Filter suggestions by status (e.g., 'pending', 'approved', 'implemented', 'denied'). If 'all' or not provided, lists pending suggestions by default. Use 'all' to see all suggestions regardless of status.", "default": "pending"}
    ],
    "returns": {
        "type": "list",
        "item_type": "dict",
        "description": "A list of dictionaries, each representing a suggestion with its key details (id, type, description, status, created_at). Returns an empty list if no suggestions match."
    }
}


if __name__ == '__main__': # pragma: no cover
    from unittest.mock import patch
    import json # Added for pretty printing in test
    import os # For __main__ test file path handling
    from ai_assistant.core.notification_manager import NotificationManager, NotificationType, NotificationStatus, Notification # For __main__
    from ai_assistant.config import get_data_dir # For __main__

    print("--- Testing awareness_tools.py ---")

    # Setup TaskManager for tests
    # For TaskManager, since it also writes a file if notifications are on,
    # we can pass a None notification_manager for its own tests if not focusing on that part.
    # Or, ensure its file path is also controlled.
    # For this test, get_system_status_summary needs a TaskManager.
    tm_test_notifications_file = os.path.join(get_data_dir(), "test_tm_notifications_for_awareness.json")
    if os.path.exists(tm_test_notifications_file):
        os.remove(tm_test_notifications_file) # Clean up before test

    # Create a NotificationManager for the TaskManager if we want to test TM's notifications
    # For this tool's test, we primarily care about the NM passed to get_system_status_summary directly.
    # So, TM can have its own or None.
    tm_notification_manager = NotificationManager(filepath=tm_test_notifications_file)
    tm = TaskManager(notification_manager=tm_notification_manager) # TaskManager now requires notification_manager

    task1_desc = "Creating new calculator tool with advanced trigonometric functions and history."
    task1 = tm.add_task(ActiveTaskType.AGENT_TOOL_CREATION, task1_desc, "calculator_v3")
    tm.update_task_status(task1.task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="LLM call for function body")

    task2_desc = "Processing user suggestion sugg_xyz to implement dark mode feature."
    task2 = tm.add_task(ActiveTaskType.SUGGESTION_PROCESSING, task2_desc, "sugg_xyz")

    task3_desc = "Modifying the existing logging tool to support structured JSON output."
    task3 = tm.add_task(ActiveTaskType.AGENT_TOOL_MODIFICATION, task3_desc, "logger_tool_v2")
    tm.update_task_status(task3.task_id, ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step_desc="Submitted to primary and secondary critics")

    task4_completed_desc = "Learning about Python context managers and their applications in resource management."
    task4_completed = tm.add_task(ActiveTaskType.LEARNING_NEW_FACT, task4_completed_desc, "python_context_managers")
    tm.update_task_status(task4_completed.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Fact learned, categorized, and saved successfully.")

    task5_failed_desc = "Scaffolding a new web application project named 'MyIntranetPortal' with FastAPI and React."
    task5_failed = tm.add_task(ActiveTaskType.USER_PROJECT_SCAFFOLDING, task5_failed_desc, "MyIntranetPortal_proj")
    tm.update_task_status(task5_failed.task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason="Invalid project name format: contains special characters not allowed by the scaffolder.")

    for i in range(4):
        tm.add_task(ActiveTaskType.MISC_SYSTEM_ACTION, f"Miscellaneous background task {i+1}", f"misc_action_00{i+1}")

    print("\n--- Testing get_system_status_summary (populated TaskManager) ---")
    summary = get_system_status_summary(task_manager=tm, active_limit=3, archived_limit=2)
    print(summary)

    print("\n--- Testing get_system_status_summary (empty TaskManager) ---")
    empty_tm = TaskManager()
    empty_summary = get_system_status_summary(task_manager=empty_tm)
    print(empty_summary)

    print("\n--- Testing get_system_status_summary (only archived in a new TM) ---")
    archived_test_tm = TaskManager()
    archived_task_desc1 = "Old tool build for 'LegacyUtility' completed last month."
    archived_task1_obj = archived_test_tm.add_task(ActiveTaskType.AGENT_TOOL_CREATION, archived_task_desc1)
    archived_test_tm.update_task_status(archived_task1_obj.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Build successful, deployed to sandbox.")
    archived_task_desc2 = "Attempted fix for old tool 'DataConverter', failed due to dependency issues."
    archived_task2_obj = archived_test_tm.add_task(ActiveTaskType.AGENT_TOOL_MODIFICATION, archived_task_desc2)
    archived_test_tm.update_task_status(archived_task2_obj.task_id, ActiveTaskStatus.FAILED_DURING_APPLY, reason="Dependency conflict: libX v1 required, v2 found.")
    archived_task_desc3 = "User query regarding 'AdvancedSearch' feature processed and answered."
    archived_task3_obj = archived_test_tm.add_task(ActiveTaskType.MISC_USER_REQUEST, archived_task_desc3)
    archived_test_tm.update_task_status(archived_task3_obj.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY)
    archived_test_tm._active_tasks = {}
    archived_summary = get_system_status_summary(task_manager=archived_test_tm, active_limit=2, archived_limit=2, notification_manager=None)
    print(archived_summary)

    print("\n--- Testing get_system_status_summary (no TaskManager, no NotificationManager) ---")
    no_deps_summary = get_system_status_summary(task_manager=None, notification_manager=None)
    print(no_deps_summary)
    assert "TaskManager not available." in no_deps_summary
    assert "NotificationManager not available." in no_deps_summary
    print("Test for no TaskManager and no NotificationManager passed.")


    print("\n--- Testing get_system_status_summary with Notifications ---")
    test_notify_file = os.path.join(get_data_dir(), "test_awareness_notifications.json")
    if os.path.exists(test_notify_file):
        os.remove(test_notify_file)

    nm = NotificationManager(filepath=test_notify_file)
    # Add more than the limit to test limit functionality
    nm.add_notification(NotificationType.TASK_COMPLETED_SUCCESSFULLY, "Tool 'alpha_tool' created by user.", "task_alpha")
    nm.add_notification(NotificationType.NEW_SUGGESTION_CREATED_AI, "Suggest to refactor module 'beta_module' for improved clarity and performance.", "sugg_beta")
    nm.add_notification(NotificationType.WARNING, "System disk space is critically low (currently at 95% usage). Please investigate.", "system_warning_disk_space_01")
    nm.add_notification(NotificationType.GENERAL_INFO, "User preferences for project 'GammaProject' have been updated successfully.", "user_pref_gamma")
    nm.add_notification(NotificationType.ERROR, "Failed to connect to external API 'OmegaService' after 3 retries.", "api_omega_conn_fail")

    # Mark one as read to test that only unread are shown by default or by unread_notifications_limit
    # Assuming notifications are added to the start of the list by default by NotificationManager
    # Let's mark the "User preferences updated" as read. (index 1 if added to start)
    # To be sure, find its ID.
    prefs_updated_notif_id = None
    for notif in nm.notifications: # nm.notifications is loaded sorted by timestamp desc
        if "User preferences updated" in notif.summary_message:
            prefs_updated_notif_id = notif.notification_id
            break
    if prefs_updated_notif_id:
        nm.mark_as_read([prefs_updated_notif_id])


    summary_with_notifs = get_system_status_summary(
        task_manager=tm, # Use the TaskManager populated earlier for task part of summary
        notification_manager=nm,
        active_limit=2,
        archived_limit=1,
        unread_notifications_limit=3 # Test showing 3 unread
    )
    print("\nSummary with Notifications (unread limit 3):")
    print(summary_with_notifs)
    assert "Unread Notifications (3 shown, up to 3 displayed)" in summary_with_notifs
    assert "Suggest to refactor module 'beta_module'" in summary_with_notifs
    assert "System disk space is critically low" in summary_with_notifs
    assert "Failed to connect to external API 'OmegaService'" in summary_with_notifs
    assert "User preferences updated" not in summary_with_notifs # This one was marked read
    assert "Tool 'alpha_tool' created" not in summary_with_notifs # This one should also be unread if not marked

    # Test with a different limit for notifications
    summary_with_notifs_limit_1 = get_system_status_summary(task_manager=tm, notification_manager=nm, unread_notifications_limit=1)
    print("\nSummary with Notifications (unread limit 1):")
    print(summary_with_notifs_limit_1)
    assert "Unread Notifications (1 shown, up to 1 displayed)" in summary_with_notifs_limit_1
    # Check that the most recent unread is shown (assuming default sort order in NotificationManager)
    # The most recent unread would be "Failed to connect to external API"
    assert "Failed to connect to external API 'OmegaService'" in summary_with_notifs_limit_1
    assert "System disk space is critically low" not in summary_with_notifs_limit_1 # Should not be shown with limit 1

    if os.path.exists(test_notify_file): # Cleanup test notification file
        os.remove(test_notify_file)
    if os.path.exists(tm_test_notifications_file): # Cleanup TM's notification file
        os.remove(tm_test_notifications_file)


    print("\n--- Testing get_item_details_by_id ---")

    if tm.list_active_tasks():
        first_task_id = tm.list_active_tasks()[0].task_id
        task_details_result = get_item_details_by_id(first_task_id, "task", task_manager=tm)
        print(f"Details for task '{first_task_id}': {task_details_result}")
        assert task_details_result and "task_id" in task_details_result and not task_details_result.get("error")
        assert isinstance(task_details_result.get('status'), str)
        assert isinstance(task_details_result.get('created_at'), str)

    task_not_found_result = get_item_details_by_id("non_existent_task", "task", task_manager=tm)
    print(f"Details for non_existent_task: {task_not_found_result}")
    assert task_not_found_result and task_not_found_result.get("error")

    mock_sugg_details = {"suggestion_id": "sugg123", "description": "A mock suggestion", "status": "pending", "type": "tool_improvement", "creation_timestamp": "2023-01-01T10:00:00Z"}
    mock_proj_details = {"project_id": "proj789", "name": "Mock Project", "status": "active"}

    with patch('ai_assistant.custom_tools.awareness_tools.find_suggestion', return_value=mock_sugg_details) as mock_fs, \
         patch('ai_assistant.custom_tools.awareness_tools.find_project', return_value=mock_proj_details) as mock_fp:

        sugg_details_result = get_item_details_by_id("sugg123", "suggestion")
        print(f"Details for suggestion 'sugg123': {sugg_details_result}")
        assert sugg_details_result == mock_sugg_details

        proj_details_result = get_item_details_by_id("proj789", "project")
        print(f"Details for project 'proj789': {proj_details_result}")
        assert proj_details_result == mock_proj_details

    with patch('ai_assistant.custom_tools.awareness_tools.find_suggestion', return_value=None) as mock_fs_none, \
         patch('ai_assistant.custom_tools.awareness_tools.find_project', return_value=None) as mock_fp_none:

        sugg_not_found = get_item_details_by_id("non_sugg", "suggestion")
        print(f"Details for non_sugg: {sugg_not_found}")
        assert sugg_not_found and sugg_not_found.get("error")

        proj_not_found = get_item_details_by_id("non_proj", "project")
        print(f"Details for non_proj: {proj_not_found}")
        assert proj_not_found and proj_not_found.get("error")

    invalid_type_result = get_item_details_by_id("any_id", "invalid_type", task_manager=tm)
    print(f"Details for invalid_type: {invalid_type_result}")
    assert invalid_type_result and invalid_type_result.get("error")

    no_tm_for_task_result = get_item_details_by_id("any_task_id", "task", task_manager=None)
    print(f"Details for task with no TM: {no_tm_for_task_result}")
    assert no_tm_for_task_result and no_tm_for_task_result.get("error")

    print("\n--- Testing list_formatted_suggestions ---")
    mock_suggestions_data = [
        # ActionableInsight like structure
        {"suggestion_id": "sugg_pend1", "type": InsightType.TOOL_ENHANCEMENT_SUGGESTED, "description": "Improve X", "status": "pending", "creation_timestamp": "2023-01-01T10:00:00Z"},
        {"suggestion_id": "sugg_appr1", "type": InsightType.KNOWLEDGE_GAP_IDENTIFIED, "description": "Learn Y", "status": "approved", "creation_timestamp": "2023-01-02T10:00:00Z"},
        {"suggestion_id": "sugg_pend2", "type": InsightType.NEW_TOOL_SUGGESTED, "description": "Create Z", "status": "pending", "creation_timestamp": "2023-01-03T10:00:00Z"},
    ]

    with patch('ai_assistant.custom_tools.awareness_tools.list_suggestions', return_value=mock_suggestions_data) as mock_ls:
        pending_suggs = list_formatted_suggestions(status_filter="pending")
        print(f"Pending suggestions: {json.dumps(pending_suggs, indent=2)}")
        assert len(pending_suggs) == 2
        assert pending_suggs[0]['suggestion_id'] == "sugg_pend1"

        approved_suggs = list_formatted_suggestions(status_filter="approved")
        print(f"Approved suggestions: {json.dumps(approved_suggs, indent=2)}")
        assert len(approved_suggs) == 1
        assert approved_suggs[0]['suggestion_id'] == "sugg_appr1"

        all_suggs = list_formatted_suggestions(status_filter="all")
        print(f"All suggestions: {json.dumps(all_suggs, indent=2)}")
        assert len(all_suggs) == 3

        denied_suggs = list_formatted_suggestions(status_filter="denied")
        print(f"Denied suggestions (expected 0): {json.dumps(denied_suggs, indent=2)}")
        assert len(denied_suggs) == 0

    with patch('ai_assistant.custom_tools.awareness_tools.list_suggestions', return_value=[]) as mock_ls_empty:
        no_suggs = list_formatted_suggestions()
        print(f"No suggestions (empty list from manager): {json.dumps(no_suggs, indent=2)}")
        assert len(no_suggs) == 0

    print("\n--- All awareness_tools.py tests finished ---")
