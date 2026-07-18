from ai_assistant.core.notification_manager import NotificationManager
from ai_assistant.core.task_manager import ActiveTaskStatus, ActiveTaskType, TaskManager
from ai_assistant.custom_tools.awareness_tools import get_system_status_summary


def test_system_status_summary_uses_isolated_task_storage(tmp_path):
    """Status-summary tests must never write dummy tasks into production state."""
    notification_manager = NotificationManager(filepath=str(tmp_path / "notifications.json"))
    task_manager = TaskManager(
        notification_manager=notification_manager,
        filepath=str(tmp_path / "active_tasks.json"),
    )

    active_task = task_manager.add_task(
        "Test task 1 for summary.",
        ActiveTaskType.AGENT_TOOL_CREATION,
        "summary_tool_test1",
    )
    task_manager.update_task_status(
        active_task.task_id,
        ActiveTaskStatus.PLANNING,
        step_desc="Working on it (Planning)",
    )

    completed_task = task_manager.add_task(
        "Test task 2, completed.",
        ActiveTaskType.LEARNING_NEW_FACT,
        "summary_fact_test2",
    )
    task_manager.update_task_status(
        completed_task.task_id,
        ActiveTaskStatus.COMPLETED_SUCCESSFULLY,
        reason="All done.",
    )

    summary = get_system_status_summary(
        task_manager=task_manager,
        notification_manager=notification_manager,
        active_limit=3,
        archived_limit=3,
        unread_notifications_limit=3,
    )

    assert "Test task 1 for summary" in summary
    assert "PLANNING" in summary
    assert "Test task 2, completed" in summary
    assert "COMPLETED_SUCCESSFULLY" in summary
    assert (tmp_path / "active_tasks.json").exists()
    assert (tmp_path / "notifications.json").exists()
