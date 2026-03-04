import tempfile
from unittest.mock import MagicMock

from ai_assistant.core.notification_manager import NotificationManager
from ai_assistant.core.task_manager import ActiveTaskStatus, ActiveTaskType, TaskManager


def test_task_manager_emits_conversational_alert_for_failure(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        tm = TaskManager(
            notification_manager=MagicMock(spec=NotificationManager),
            filepath=f"{temp_dir}/active_tasks.json",
        )

        task = tm.add_task("Task that should fail", ActiveTaskType.MISC_CODE_GENERATION)

        calls = {}

        def fake_emit(task_obj):
            calls["task_id"] = task_obj.task_id

        monkeypatch.setattr("ai_assistant.core.conversational_alerts.emit_task_failure_alert", fake_emit)

        tm.update_task_status(task.task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason="boom")

        assert calls["task_id"] == task.task_id


def test_task_manager_skips_conversational_alert_for_user_cancelled(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        tm = TaskManager(
            notification_manager=MagicMock(spec=NotificationManager),
            filepath=f"{temp_dir}/active_tasks.json",
        )

        task = tm.add_task("Task user cancelled", ActiveTaskType.MISC_CODE_GENERATION)

        def fail_emit(_):
            raise AssertionError("conversational alert should not be emitted for USER_CANCELLED")

        monkeypatch.setattr("ai_assistant.core.conversational_alerts.emit_task_failure_alert", fail_emit)

        tm.update_task_status(task.task_id, ActiveTaskStatus.USER_CANCELLED, reason="stopped by user")
