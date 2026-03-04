from types import SimpleNamespace

import app_globals

from ai_assistant.core import conversational_alerts
from ai_assistant.core.task_manager import ActiveTask, ActiveTaskStatus, ActiveTaskType


def _make_task(status=ActiveTaskStatus.FAILED_UNKNOWN):
    return ActiveTask(
        description="Repair failing deployment pipeline",
        task_type=ActiveTaskType.MISC_CODE_GENERATION,
        status=status,
        status_reason="unit tests failed after patch",
        current_step_description="Running post-modification tests",
        session_id="session-123",
    )


def test_emit_task_failure_alert_persists_message_and_emits_socket(monkeypatch):
    task = _make_task()

    emitted = {}
    added = {}

    chat_mgr = SimpleNamespace(
        add_message=lambda sid, role, content: added.update({"sid": sid, "role": role, "content": content}),
        list_sessions=lambda: [{"id": "session-xyz"}],
        create_session=lambda title: "new-session",
    )
    socketio = SimpleNamespace(
        emit=lambda event, payload: emitted.update({"event": event, "payload": payload})
    )

    monkeypatch.setattr(app_globals, "chat_manager", chat_mgr)
    monkeypatch.setattr(app_globals, "socketio", socketio)

    payload = conversational_alerts.emit_task_failure_alert(task)

    assert payload is not None
    assert emitted["event"] == "assistant_alert"
    assert payload["session_id"] == "session-123"
    assert added["sid"] == "session-123"
    assert added["role"] == "assistant"
    assert "background mission hit an issue" in added["content"].lower()
    assert len(payload["suggested_actions"]) == 3


def test_emit_task_failure_alert_ignores_success_status(monkeypatch):
    task = _make_task(status=ActiveTaskStatus.COMPLETED_SUCCESSFULLY)

    chat_mgr = SimpleNamespace(
        add_message=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not add message")),
        list_sessions=lambda: [],
        create_session=lambda title: "new-session",
    )

    monkeypatch.setattr(app_globals, "chat_manager", chat_mgr)
    monkeypatch.setattr(app_globals, "socketio", SimpleNamespace(emit=lambda *a, **k: None))

    payload = conversational_alerts.emit_task_failure_alert(task)
    assert payload is None


def test_execute_alert_action_summarize(monkeypatch):
    task = _make_task()
    fake_tm = SimpleNamespace(get_task_including_archive=lambda _tid: task)
    fake_orch = SimpleNamespace(task_manager=fake_tm)

    monkeypatch.setattr(app_globals, "orchestrator", fake_orch)

    result = conversational_alerts.execute_alert_action(task.task_id, "summarize")
    assert result["success"] is True
    assert "background mission hit an issue" in result["message"].lower()


def test_execute_alert_action_retry_creates_new_task(monkeypatch):
    task = _make_task()

    created = {}

    class FakeTM:
        def get_task_including_archive(self, _tid):
            return task

        def add_task(self, description, task_type, related_item_id=None, details=None, session_id=None):
            created["description"] = description
            created["task_type"] = task_type
            created["session_id"] = session_id
            return SimpleNamespace(task_id="task_retry_1")

        def update_task_status(self, *args, **kwargs):
            created["updated"] = True

    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(task_manager=FakeTM()))

    result = conversational_alerts.execute_alert_action(task.task_id, "retry")

    assert result["success"] is True
    assert result["new_task_id"] == "task_retry_1"
    assert created["updated"] is True


def test_execute_alert_action_pause_sets_flag(monkeypatch):
    task = _make_task()
    task.details = {}

    class FakeTM:
        def get_task_including_archive(self, _tid):
            return task

        def _save_active_tasks(self):
            return None

    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace(task_manager=FakeTM()))

    result = conversational_alerts.execute_alert_action(task.task_id, "pause")
    assert result["success"] is True
    assert task.details["autonomous_retry_paused"] is True
