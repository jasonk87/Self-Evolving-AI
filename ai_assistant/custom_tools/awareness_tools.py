from ai_assistant.core.task_manager import TaskManager, ActiveTaskStatus, ActiveTaskType
from ai_assistant.core.notification_manager import (
    NotificationManager,
    NotificationStatus,  # noqa: F401 - used by get_system_status_summary at runtime
)
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum, auto
from dataclasses import asdict
from ai_assistant.core.suggestion_manager import find_suggestion
from ai_assistant.core.project_manager import find_project
from ai_assistant.memory.persistent_memory import load_learned_facts

def get_system_status_summary(task_manager: Optional[TaskManager]=None, notification_manager: Optional[NotificationManager]=None, active_limit: int=5, archived_limit: int=3, unread_notifications_limit: int=3) -> str:
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
    summary_lines = ['System Status Summary:']
    if task_manager:
        active_tasks = task_manager.list_active_tasks()
        archived_tasks = task_manager.list_archived_tasks(limit=archived_limit)
        summary_lines = ['System Status Summary:']
        summary_lines.append(f'\nActive Tasks ({len(active_tasks)} total):')
        if not active_tasks:
            summary_lines.append('  No active tasks currently.')
        else:
            for i, task in enumerate(active_tasks):
                if i >= active_limit:
                    summary_lines.append(f'  ... and {len(active_tasks) - active_limit} more active tasks.')
                    break
                details_str = f" (ID: {task.task_id}, Related: {task.related_item_id or 'N/A'})"
                reason_str = f' Reason: {task.status_reason}' if task.status_reason else ''
                step_str = f' Step: {task.current_step_description}' if task.current_step_description else ''
                summary_lines.append(f'  - {task.description[:60]}... ({task.task_type.name}) - Status: {task.status.name}{step_str}{reason_str}{details_str}')
        summary_lines.append(f'\nRecently Completed/Archived Tasks ({len(archived_tasks)} shown, up to {archived_limit}):')
        if not archived_tasks:
            summary_lines.append('  No recently archived tasks.')
        else:
            for task in archived_tasks:
                details_str = f" (ID: {task.task_id}, Related: {task.related_item_id or 'N/A'})"
                reason_str = f' Reason: {task.status_reason}' if task.status_reason else ''
                summary_lines.append(f'  - {task.description[:60]}... ({task.task_type.name}) - Final Status: {task.status.name}{reason_str}{details_str}')
        if active_tasks:
            summary_lines.append('\nActive Task Status Breakdown:')
            status_counts: Dict[ActiveTaskStatus, int] = {}
            for task in active_tasks:
                status_counts[task.status] = status_counts.get(task.status, 0) + 1
            for status_key, count in status_counts.items():
                summary_lines.append(f'  - {status_key.name}: {count}')
    else:
        summary_lines.append('TaskManager not available.')
    if not notification_manager:
        summary_lines.append('\nNotificationManager not available.')
    else:
        unread_notifications = notification_manager.get_notifications(status_filter=NotificationStatus.UNREAD, limit=unread_notifications_limit)
        num_unread_actually_shown = len(unread_notifications)
        total_unread_count_note = f'({num_unread_actually_shown} shown, up to {unread_notifications_limit} displayed)'
        summary_lines.append(f'\nUnread Notifications {total_unread_count_note}:')
        if not unread_notifications:
            summary_lines.append('  No unread notifications.')
        else:
            for i, notification in enumerate(unread_notifications):
                ts_str = 'Unknown Time'
                if isinstance(notification.timestamp, datetime):
                    ts_str = notification.timestamp.strftime('%Y-%m-%d %H:%M')
                summary_msg_display = notification.summary_message
                if len(summary_msg_display) > 250:
                    summary_msg_display = summary_msg_display[:247] + '...'
                summary_lines.append(f'  - [{ts_str}] {notification.event_type.name}: {summary_msg_display} (ID: {notification.notification_id})')
    import glob
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        generated_tools_dir = os.path.join(current_dir, 'generated')
        if os.path.exists(generated_tools_dir):
            py_files = glob.glob(os.path.join(generated_tools_dir, '*.py'))
            py_files = [f for f in py_files if not os.path.basename(f).startswith('__')]
            py_files.sort(key=os.path.getmtime, reverse=True)
            recent_files = py_files[:5]
            if recent_files:
                summary_lines.append('\nRecently Created/Modified Tools (File System):')
                for f_path in recent_files:
                    f_name = os.path.basename(f_path)
                    mtime = datetime.fromtimestamp(os.path.getmtime(f_path)).strftime('%Y-%m-%d %H:%M')
                    summary_lines.append(f'  - {f_name} (Last Modified: {mtime})')
            else:
                summary_lines.append('\nRecently Created/Modified Tools: None found in generated directory.')
    except Exception as e:
        summary_lines.append(f'\nError scanning generated tools: {e}')

    # Append Background Service Report
    try:
        from ai_assistant.core.background_service import get_background_activity_report
        bg_report = get_background_activity_report()
        summary_lines.append(f'\n{bg_report}')
    except Exception as e:
        summary_lines.append(f'\nError retrieving background service status: {e}')

    return '\n'.join(summary_lines)
GET_SYSTEM_STATUS_SUMMARY_SCHEMA = {'name': 'get_system_status_summary', 'description': 'Provides a summary of current system activity, including active/archived tasks and recent unread notifications.', 'parameters': [{'name': 'active_limit', 'type': 'int', 'description': 'Optional. Max active tasks to detail (default 5).'}, {'name': 'archived_limit', 'type': 'int', 'description': 'Optional. Max archived tasks to detail (default 3).'}, {'name': 'unread_notifications_limit', 'type': 'int', 'description': 'Optional. Max unread notifications to detail (default 3).'}], 'returns': {'type': 'str', 'description': 'A multi-line string summarizing system status and notifications.'}}

def get_self_awareness_info_and_converse(
    context: Optional[str] = None,
    *,
    query: Optional[str] = None,
    task_manager: Optional[TaskManager] = None,
    notification_manager: Optional[NotificationManager] = None,
    **kwargs,
) -> str:
    """
    Retrieves a concise summary of the AI's current state, including active tasks,
    notifications, and general system health, formatted for a conversational response.
    
    Args:
        context: Optional context or reason for the check (often provided by the planner).
        task_manager: Injected TaskManager.
        notification_manager: Injected NotificationManager.
    """
    import json
    from ai_assistant.config import get_data_dir
    import os
    normalized_context = context
    if normalized_context is None and query is not None:
        normalized_context = str(query)
    if isinstance(normalized_context, (dict, list, tuple)):
        normalized_context = json.dumps(normalized_context, ensure_ascii=False)
    elif normalized_context is not None:
        normalized_context = str(normalized_context)

    status_summary = get_system_status_summary(task_manager=task_manager, notification_manager=notification_manager, active_limit=3, archived_limit=5, unread_notifications_limit=3)
    facts = load_learned_facts()
    facts_summary = '\nLearned Facts:\n'
    if not facts:
        facts_summary += '  No specific facts learned yet.'
    elif isinstance(facts, list) and len(facts) > 0 and isinstance(facts[0], str):
        for f in facts[:3]:
            facts_summary += f'  - {f}\n'
        if len(facts) > 3:
            facts_summary += f'  ... and {len(facts) - 3} more.'
    elif isinstance(facts, list) and len(facts) > 0 and isinstance(facts[0], dict):
        for f in facts[:3]:
            text = f.get('text', 'Unknown fact')
            facts_summary += f'  - {text}\n'
        if len(facts) > 3:
            facts_summary += f'  ... and {len(facts) - 3} more.'
    else:
        facts_summary = '  Facts are in an unexpected format.'
    registry_path = os.path.join(get_data_dir(), 'tool_registry.json')
    tool_list_str = '\nAvailable Tools:\n'
    try:
        if os.path.exists(registry_path):
            with open(registry_path, 'r') as f:
                registry = json.load(f)
                tool_names = sorted(list(registry.keys()))
                count = len(tool_names)
                tool_names = tool_names[:3]
                tool_list_str += f'  (Showing {len(tool_names)} of {count} total)\n'
                tool_list_str += '  ' + ', '.join(tool_names)
        else:
            tool_list_str += '  Registry file not found.'
    except Exception as e:
        tool_list_str += f'  Error reading tool registry: {e}'
    response = 'Self-Awareness Report:\n'
    if normalized_context:
        response += f'(Context: {normalized_context})\n'
    response += f'{status_summary}\n{facts_summary}\n{tool_list_str}\n\n(End of report.)'
    return response
GET_SELF_AWARENESS_INFO_AND_CONVERSE_SCHEMA = {'name': 'get_self_awareness_info_and_converse', 'description': "Retrieves internal state and system status to enable the AI to answer questions about 'how it is doing' or what it is working on.", 'parameters': [{'name': 'context', 'type': 'str', 'description': 'Optional. A brief explanation of why self-awareness is being checked or what specific information is being sought.'}], 'returns': {'type': 'str', 'description': 'A detailed text report of internal status.'}}

class ItemTypeForDetails(Enum):
    TASK = 'task'
    SUGGESTION = 'suggestion'
    PROJECT = 'project'
    NOTIFICATION = 'notification'

def get_item_details_by_id(item_id: str, item_type: str, task_manager: Optional[TaskManager]=None, notification_manager: Optional[NotificationManager]=None) -> Optional[Dict[str, Any]]:
    """
    Retrieves details for a specific item (task, suggestion, or project) by its ID.

    Args:
        item_id: The ID of the item to retrieve.
        item_type: The type of item (e.g., "task", "suggestion", "project", "notification").
        task_manager: An instance of the TaskManager (required if item_type is "task").
        notification_manager: An instance of the NotificationManager (required if item_type is "notification").

    Returns:
        A dictionary containing the item's details, or an error dictionary if not found or type is invalid.
    """
    try:
        resolved_item_type = ItemTypeForDetails(item_type.lower())
    except ValueError:
        return {'error': f"Invalid item_type '{item_type}'. Valid types are: {[t.value for t in ItemTypeForDetails]}"}
    details: Optional[Dict[str, Any]] = None
    if resolved_item_type == ItemTypeForDetails.TASK:
        if not task_manager:
            return {'error': "TaskManager instance not provided for item_type 'task'."}
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
        if details and 'source' not in details:
            details['source'] = 'AI'
    elif resolved_item_type == ItemTypeForDetails.PROJECT:
        details = find_project(item_id)
    elif resolved_item_type == ItemTypeForDetails.NOTIFICATION:
        if not notification_manager:
            return {'error': "NotificationManager instance not provided for item_type 'notification'."}
        found_notif = None
        for n in notification_manager.notifications:
            if n.notification_id == item_id:
                found_notif = n
                break
        if found_notif:
            details = {'notification_id': found_notif.notification_id, 'event_type': found_notif.event_type.name, 'summary_message': found_notif.summary_message, 'details': found_notif.details_payload, 'related_item_id': found_notif.related_item_id, 'status': found_notif.status.name, 'timestamp': found_notif.timestamp.isoformat() if isinstance(found_notif.timestamp, datetime) else str(found_notif.timestamp)}
    if not details and resolved_item_type:
        return {'error': f"{resolved_item_type.value.capitalize()} with ID '{item_id}' not found."}
    return details
GET_ITEM_DETAILS_BY_ID_SCHEMA = {'name': 'get_item_details_by_id', 'description': 'Retrieves details for a specific system item (task, suggestion, or project) using its ID and type.', 'parameters': [{'name': 'item_id', 'type': 'str', 'description': 'The unique ID of the item.'}, {'name': 'item_type', 'type': 'str', 'description': "The type of item. Valid values: 'task', 'suggestion', 'project', 'notification'."}], 'returns': {'type': 'dict', 'description': "A dictionary containing the item's details, or an error dictionary if not found or type is invalid."}}

def list_formatted_suggestions(status_filter: Optional[str]='pending') -> List[Dict[str, Any]]:
    """
    Lists suggestions, optionally filtered by status, and formats them.

    Args:
        status_filter: Optional. Filter suggestions by status (e.g., "pending", "approved").
                       If "all", all suggestions are returned. Defaults to "pending".

    Returns:
        A list of dictionaries, where each dictionary contains key details of a suggestion.
        Returns an empty list if no suggestions match or if suggestion_manager is unavailable.
    """
    from ai_assistant.core.suggestion_manager import list_suggestions
    try:
        all_suggs = list_suggestions()
    except Exception:
        return []
    if not all_suggs:
        return []
    filtered_suggestions: List[Dict[str, Any]] = []
    status_to_filter = status_filter.lower() if status_filter else 'pending'
    for sugg in all_suggs:
        current_status = sugg.get('status', '').lower() if isinstance(sugg, dict) else ''
        if status_to_filter == 'all' or current_status == status_to_filter:
            formatted_sugg = {'suggestion_id': sugg.get('suggestion_id', 'N/A'), 'type': sugg.get('type', 'N/A'), 'description': sugg.get('description', 'N/A'), 'status': sugg.get('status', 'N/A'), 'created_at': sugg.get('creation_timestamp', 'N/A'), 'source': sugg.get('source', 'AI')}
            if 'type' in formatted_sugg and hasattr(formatted_sugg['type'], 'name'):
                formatted_sugg['type'] = formatted_sugg['type'].name
            filtered_suggestions.append(formatted_sugg)
    return filtered_suggestions
LIST_FORMATTED_SUGGESTIONS_SCHEMA = {'name': 'list_formatted_suggestions', 'description': 'Lists system-generated or user-added suggestions, optionally filtered by status (e.g., pending, approved). Useful for reviewing items that might lead to agent improvements or new tasks.', 'parameters': [{'name': 'status_filter', 'type': 'str', 'description': "Optional. Filter suggestions by status (e.g., 'pending', 'approved', 'implemented', 'denied'). If 'all' or not provided, lists pending suggestions by default. Use 'all' to see all suggestions regardless of status.", 'default': 'pending'}], 'returns': {'type': 'list', 'item_type': 'dict', 'description': 'A list of dictionaries, each representing a suggestion with its key details (id, type, description, status, created_at, source). Returns an empty list if no suggestions match.'}}
if __name__ == '__main__':
    from unittest.mock import patch
    import json
    import os
    from ai_assistant.core.notification_manager import NotificationManager, NotificationType
    from ai_assistant.config import get_data_dir

    class MockInsightType(Enum):
        TOOL_ENHANCEMENT_SUGGESTED = auto()
        KNOWLEDGE_GAP_IDENTIFIED = auto()
        NEW_TOOL_SUGGESTED = auto()
    print('--- Testing awareness_tools.py ---')
    tm_test_notifications_file = os.path.join(get_data_dir(), 'test_tm_notifications_for_awareness.json')
    if os.path.exists(tm_test_notifications_file):
        os.remove(tm_test_notifications_file)
    tm_notification_manager = NotificationManager(filepath=tm_test_notifications_file)
    tm = TaskManager(notification_manager=tm_notification_manager)
    task1_desc = 'Creating new calculator tool with advanced trigonometric functions and history.'
    task1 = tm.add_task(task1_desc, ActiveTaskType.AGENT_TOOL_CREATION, 'calculator_v3')
    tm.update_task_status(task1.task_id, ActiveTaskStatus.GENERATING_CODE, step_desc='LLM call for function body')
    task2_desc = 'Processing user suggestion sugg_xyz to implement dark mode feature.'
    task2 = tm.add_task(task2_desc, ActiveTaskType.SUGGESTION_PROCESSING, 'sugg_xyz')
    task3_desc = 'Modifying the existing logging tool to support structured JSON output.'
    task3 = tm.add_task(task3_desc, ActiveTaskType.AGENT_TOOL_MODIFICATION, 'logger_tool_v2')
    tm.update_task_status(task3.task_id, ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step_desc='Submitted to primary and secondary critics')
    task4_completed_desc = 'Learning about Python context managers and their applications in resource management.'
    task4_completed = tm.add_task(task4_completed_desc, ActiveTaskType.LEARNING_NEW_FACT, 'python_context_managers')
    tm.update_task_status(task4_completed.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason='Fact learned, categorized, and saved successfully.')
    task5_failed_desc = "Scaffolding a new web application project named 'MyIntranetPortal' with FastAPI and React."
    task5_failed = tm.add_task(task5_failed_desc, ActiveTaskType.USER_PROJECT_SCAFFOLDING, 'MyIntranetPortal_proj')
    tm.update_task_status(task5_failed.task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason='Invalid project name format: contains special characters not allowed by the scaffolder.')
    for i in range(4):
        tm.add_task(f'Miscellaneous background task {i + 1}', ActiveTaskType.MISC_CODE_GENERATION, f'misc_action_00{i + 1}')
    print('\n--- Testing get_system_status_summary (populated TaskManager) ---')
    summary = get_system_status_summary(task_manager=tm, active_limit=3, archived_limit=2)
    print(summary)
    print('\n--- Testing get_system_status_summary (empty TaskManager) ---')
    empty_tm = TaskManager()
    empty_summary = get_system_status_summary(task_manager=empty_tm)
    print(empty_summary)
    print('\n--- Testing get_system_status_summary (only archived in a new TM) ---')
    archived_test_tm = TaskManager()
    archived_task_desc1 = "Old tool build for 'LegacyUtility' completed last month."
    archived_task1_obj = archived_test_tm.add_task(archived_task_desc1, ActiveTaskType.AGENT_TOOL_CREATION)
    archived_test_tm.update_task_status(archived_task1_obj.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason='Build successful, deployed to sandbox.')
    archived_task_desc2 = "Attempted fix for old tool 'DataConverter', failed due to dependency issues."
    archived_task2_obj = archived_test_tm.add_task(archived_task_desc2, ActiveTaskType.AGENT_TOOL_MODIFICATION)
    archived_test_tm.update_task_status(archived_task2_obj.task_id, ActiveTaskStatus.FAILED_DURING_APPLY, reason='Dependency conflict: libX v1 required, v2 found.')
    archived_task_desc3 = "User query regarding 'AdvancedSearch' feature processed and answered."
    archived_task3_obj = archived_test_tm.add_task(archived_task_desc3, ActiveTaskType.MISC_CODE_GENERATION)
    archived_test_tm.update_task_status(archived_task3_obj.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY)
    archived_test_tm._active_tasks = {}
    archived_summary = get_system_status_summary(task_manager=archived_test_tm, active_limit=2, archived_limit=2, notification_manager=None)
    print(archived_summary)
    print('\n--- Testing get_system_status_summary (no TaskManager, no NotificationManager) ---')
    no_deps_summary = get_system_status_summary(task_manager=None, notification_manager=None)
    print(no_deps_summary)
    assert 'TaskManager not available.' in no_deps_summary
    assert 'NotificationManager not available.' in no_deps_summary
    print('Test for no TaskManager and no NotificationManager passed.')
    print('\n--- Testing get_system_status_summary with Notifications ---')
    test_notify_file = os.path.join(get_data_dir(), 'test_awareness_notifications.json')
    if os.path.exists(test_notify_file):
        os.remove(test_notify_file)
    nm = NotificationManager(filepath=test_notify_file)
    import time
    nm.add_notification(NotificationType.TASK_COMPLETED_SUCCESSFULLY, "Tool 'alpha_tool' created by user.", 'task_alpha')
    time.sleep(0.01)
    nm.add_notification(NotificationType.NEW_SUGGESTION_CREATED_AI, "Suggest to refactor module 'beta_module' for improved clarity and performance.", 'sugg_beta')
    time.sleep(0.01)
    nm.add_notification(NotificationType.WARNING, 'System disk space is critically low (currently at 95% usage). Please investigate.', 'system_warning_disk_space_01')
    time.sleep(0.01)
    nm.add_notification(NotificationType.GENERAL_INFO, "User preferences for project 'GammaProject' have been updated successfully.", 'user_pref_gamma')
    time.sleep(0.01)
    nm.add_notification(NotificationType.ERROR, "Failed to connect to external API 'OmegaService' after 3 retries.", 'api_omega_conn_fail')
    time.sleep(0.01)
    prefs_updated_notif_id = None
    for notif in nm.notifications:
        if 'User preferences' in notif.summary_message:
            prefs_updated_notif_id = notif.notification_id
            break
    if prefs_updated_notif_id:
        nm.mark_as_read([prefs_updated_notif_id])
    summary_with_notifs = get_system_status_summary(task_manager=tm, notification_manager=nm, active_limit=2, archived_limit=1, unread_notifications_limit=3)
    print('\nSummary with Notifications (unread limit 3):')
    print(summary_with_notifs)
    assert 'Unread Notifications (3 shown, up to 3 displayed)' in summary_with_notifs
    assert "Suggest to refactor module 'beta_module'" in summary_with_notifs
    assert 'System disk space is critically low' in summary_with_notifs
    assert "Failed to connect to external API 'OmegaService'" in summary_with_notifs
    assert 'User preferences updated' not in summary_with_notifs
    assert "Tool 'alpha_tool' created" not in summary_with_notifs
    summary_with_notifs_limit_1 = get_system_status_summary(task_manager=tm, notification_manager=nm, unread_notifications_limit=1)
    print('\nSummary with Notifications (unread limit 1):')
    print(summary_with_notifs_limit_1)
    assert 'Unread Notifications (1 shown, up to 1 displayed)' in summary_with_notifs_limit_1
    assert "Failed to connect to external API 'OmegaService'" in summary_with_notifs_limit_1
    assert 'System disk space is critically low' not in summary_with_notifs_limit_1
    if os.path.exists(test_notify_file):
        os.remove(test_notify_file)
    if os.path.exists(tm_test_notifications_file):
        os.remove(tm_test_notifications_file)
    print('\n--- Testing get_item_details_by_id ---')
    if tm.list_active_tasks():
        first_task_id = tm.list_active_tasks()[0].task_id
        task_details_result = get_item_details_by_id(first_task_id, 'task', task_manager=tm)
        print(f"Details for task '{first_task_id}': {task_details_result}")
        assert task_details_result and 'task_id' in task_details_result and (not task_details_result.get('error'))
        assert isinstance(task_details_result.get('status'), str)
        assert isinstance(task_details_result.get('created_at'), str)
    task_not_found_result = get_item_details_by_id('non_existent_task', 'task', task_manager=tm)
    print(f'Details for non_existent_task: {task_not_found_result}')
    assert task_not_found_result and task_not_found_result.get('error')
    mock_sugg_details = {'suggestion_id': 'sugg123', 'description': 'A mock suggestion', 'status': 'pending', 'type': 'tool_improvement', 'creation_timestamp': '2023-01-01T10:00:00Z'}
    mock_proj_details = {'project_id': 'proj789', 'name': 'Mock Project', 'status': 'active'}
    patch_base = __name__
    with patch(f'{patch_base}.find_suggestion', return_value=mock_sugg_details) as mock_fs, patch(f'{patch_base}.find_project', return_value=mock_proj_details) as mock_fp:
        sugg_details_result = get_item_details_by_id('sugg123', 'suggestion')
        print(f"Details for suggestion 'sugg123': {sugg_details_result}")
        assert sugg_details_result == mock_sugg_details
        proj_details_result = get_item_details_by_id('proj789', 'project')
        print(f"Details for project 'proj789': {proj_details_result}")
        assert proj_details_result == mock_proj_details
    with patch(f'{patch_base}.find_suggestion', return_value=None) as mock_fs_none, patch(f'{patch_base}.find_project', return_value=None) as mock_fp_none:
        sugg_not_found = get_item_details_by_id('non_sugg', 'suggestion')
        print(f'Details for non_sugg: {sugg_not_found}')
        assert sugg_not_found and sugg_not_found.get('error')
        proj_not_found = get_item_details_by_id('non_proj', 'project')
        print(f'Details for non_proj: {proj_not_found}')
        assert proj_not_found and proj_not_found.get('error')
    invalid_type_result = get_item_details_by_id('any_id', 'invalid_type', task_manager=tm)
    print(f'Details for invalid_type: {invalid_type_result}')
    assert invalid_type_result and invalid_type_result.get('error')
    no_tm_for_task_result = get_item_details_by_id('any_task_id', 'task', task_manager=None)
    print(f'Details for task with no TM: {no_tm_for_task_result}')
    assert no_tm_for_task_result and no_tm_for_task_result.get('error')
    print('\n--- Testing get_item_details_by_id (notification) ---')
    if nm.notifications:
        first_notif = nm.notifications[0]
        notif_details = get_item_details_by_id(first_notif.notification_id, 'notification', notification_manager=nm)
        print(f"Details for notification '{first_notif.notification_id}': {json.dumps(notif_details, default=str)}")
        assert notif_details and notif_details.get('notification_id') == first_notif.notification_id
        assert 'summary_message' in notif_details
    else:
        print('No notifications to test lookup.')
    print('\n--- Testing list_formatted_suggestions ---')
    mock_suggestions_data = [{'suggestion_id': 'sugg_pend1', 'type': MockInsightType.TOOL_ENHANCEMENT_SUGGESTED, 'description': 'Improve X', 'status': 'pending', 'creation_timestamp': '2023-01-01T10:00:00Z'}, {'suggestion_id': 'sugg_appr1', 'type': MockInsightType.KNOWLEDGE_GAP_IDENTIFIED, 'description': 'Learn Y', 'status': 'approved', 'creation_timestamp': '2023-01-02T10:00:00Z'}, {'suggestion_id': 'sugg_pend2', 'type': MockInsightType.NEW_TOOL_SUGGESTED, 'description': 'Create Z', 'status': 'pending', 'creation_timestamp': '2023-01-03T10:00:00Z'}]
    with patch(f'{patch_base}.list_suggestions', return_value=mock_suggestions_data) as mock_ls:
        pending_suggs = list_formatted_suggestions(status_filter='pending')
        print(f'Pending suggestions: {json.dumps(pending_suggs, indent=2)}')
        assert len(pending_suggs) == 2
        assert pending_suggs[0]['suggestion_id'] == 'sugg_pend1'
        approved_suggs = list_formatted_suggestions(status_filter='approved')
        print(f'Approved suggestions: {json.dumps(approved_suggs, indent=2)}')
        assert len(approved_suggs) == 1
        assert approved_suggs[0]['suggestion_id'] == 'sugg_appr1'
        all_suggs = list_formatted_suggestions(status_filter='all')
        print(f'All suggestions: {json.dumps(all_suggs, indent=2)}')
        assert len(all_suggs) == 3
        denied_suggs = list_formatted_suggestions(status_filter='denied')
        print(f'Denied suggestions (expected 0): {json.dumps(denied_suggs, indent=2)}')
        assert len(denied_suggs) == 0
    with patch(f'{patch_base}.list_suggestions', return_value=[]) as mock_ls_empty:
        no_suggs = list_formatted_suggestions()
        print(f'No suggestions (empty list from manager): {json.dumps(no_suggs, indent=2)}')
        assert len(no_suggs) == 0
    print('\n--- All awareness_tools.py tests finished ---')
