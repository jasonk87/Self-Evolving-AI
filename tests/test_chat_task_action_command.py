from types import SimpleNamespace

from flask import Flask

import app_globals
from routes import chat


def _build_test_app():
    app = Flask(__name__)
    app.register_blueprint(chat.chat_bp)
    return app


def _setup_chat_manager(monkeypatch):
    messages = []
    session_store = {"s1": {"id": "s1", "history": []}}

    def create_session(title="New Chat"):
        return "s1"

    def get_session(session_id):
        return session_store.get(session_id)

    def add_message(session_id, role, content, images=None):
        session_store.setdefault(session_id, {"id": session_id, "history": []})
        session_store[session_id]["history"].append({"role": role, "content": content})
        messages.append((session_id, role, content))
        return session_store[session_id]

    def update_session_metadata(session_id, metadata):
        session_store.setdefault(session_id, {"id": session_id, "history": []})
        existing = session_store[session_id].get("metadata", {})
        existing.update(metadata)
        session_store[session_id]["metadata"] = existing
        return session_store[session_id]

    monkeypatch.setattr(app_globals, "chat_manager", SimpleNamespace(
        create_session=create_session,
        get_session=get_session,
        add_message=add_message,
        update_session_metadata=update_session_metadata,
    ))
    return messages


def test_chat_task_action_command_executes_without_orchestrator(monkeypatch):
    app = _build_test_app()

    messages = _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(chat, "execute_alert_action", lambda task_id, action: {"success": True, "message": f"done:{task_id}:{action}"})

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/task-action task_1 retry", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["response"] == "done:task_1:retry"
    assert any(m[1] == "assistant" and "done:task_1:retry" in m[2] for m in messages)


def test_chat_set_config_command_updates_setting(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)

    updates = {}

    def update_setting(key, value):
        updates[key] = value
        return True

    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: str(raw).strip().lower() in {"true", "1", "yes", "on"},
        update_setting=update_setting,
    ))

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/set-config ENABLE_THINKING false", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert updates["ENABLE_THINKING"] is False


def test_chat_show_config_command_returns_value(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"DEFAULT_EXECUTION_MODE": "THINKING_PRO"},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/show-config DEFAULT_EXECUTION_MODE", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "DEFAULT_EXECUTION_MODE = THINKING_PRO" in payload["response"]


def test_chat_set_config_rejects_invalid_value(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)

    def coerce_setting_value(key, raw):
        raise ValueError("Expected boolean value (true/false)")

    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=coerce_setting_value,
        update_setting=lambda key, value: True,
    ))

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/set-config ENABLE_THINKING maybe", "session_id": "s1"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Failed to parse value" in payload["response"]


def test_chat_set_reminder_command(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))
    monkeypatch.setattr(chat, "set_reminder", lambda msg, time_str: f"Reminder set for {time_str}: {msg}")

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/set-reminder in 5 minutes :: stretch", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "Reminder set" in payload["response"]


def test_chat_list_and_delete_reminder_commands(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))
    monkeypatch.setattr(chat, "list_reminders", lambda status="pending": "Reminders: \n- [abc123] 2026-01-01T00:00:00: test")
    monkeypatch.setattr(chat, "delete_reminder", lambda reminder_id: f"Reminder {reminder_id} deleted.")

    with app.test_client() as client:
        list_response = client.post('/chat', json={"message": "/list-reminders all", "session_id": "s1"})
        delete_response = client.post('/chat', json={"message": "/delete-reminder abc123", "session_id": "s1"})

    assert list_response.status_code == 200
    assert delete_response.status_code == 200
    assert "Reminders:" in list_response.get_json()["response"]
    assert "deleted" in delete_response.get_json()["response"].lower()


def test_chat_update_reminder_command_updates_fields(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    calls = {}

    def fake_update(reminder_id, new_time_str=None, new_message=None):
        calls["reminder_id"] = reminder_id
        calls["new_time_str"] = new_time_str
        calls["new_message"] = new_message
        return f"Reminder {reminder_id} updated."

    monkeypatch.setattr(chat, "update_reminder", fake_update)

    with app.test_client() as client:
        response = client.post('/chat', json={
            "message": "/update-reminder abc123 :: every 2 hours :: drink water",
            "session_id": "s1"
        })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert calls == {
        "reminder_id": "abc123",
        "new_time_str": "every 2 hours",
        "new_message": "drink water",
    }


def test_chat_update_reminder_command_supports_message_only(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    calls = {}

    def fake_update(reminder_id, new_time_str=None, new_message=None):
        calls["reminder_id"] = reminder_id
        calls["new_time_str"] = new_time_str
        calls["new_message"] = new_message
        return f"Reminder {reminder_id} updated."

    monkeypatch.setattr(chat, "update_reminder", fake_update)

    with app.test_client() as client:
        response = client.post('/chat', json={
            "message": "/update-reminder abc123 :: - :: stand up",
            "session_id": "s1"
        })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert calls == {
        "reminder_id": "abc123",
        "new_time_str": None,
        "new_message": "stand up",
    }


def test_chat_delegate_code_command_creates_task(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", SimpleNamespace())
    monkeypatch.setattr(app_globals, "ai_loop", object(), raising=False)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
        get_time=lambda: "2026-01-01T00:00:00",
    ))

    added = {}

    def add_task(description, task_type, related_item_id=None, details=None, session_id=None):
        added["description"] = description
        added["task_type"] = task_type
        added["details"] = details
        added["session_id"] = session_id
        return SimpleNamespace(task_id="task_12345678")

    monkeypatch.setattr(app_globals, "task_manager", SimpleNamespace(
        add_task=add_task,
        get_task_including_archive=lambda task_id: None,
        update_task_status=lambda *args, **kwargs: None,
    ))

    launched = {}

    def fake_launch(task_id, session_id, delegated_prompt, history_snapshot):
        launched["task_id"] = task_id
        launched["session_id"] = session_id
        launched["delegated_prompt"] = delegated_prompt
        launched["history_len"] = len(history_snapshot)

    monkeypatch.setattr(chat, "_launch_delegated_code_task", fake_launch)

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/delegate-code add tests for parser", "session_id": "s1"})

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["success"] is True
    assert "delegated" in payload["response"].lower()
    assert added["session_id"] == "s1"
    assert added["details"]["source"] == "chat_delegate"
    assert launched["task_id"] == "task_12345678"


def test_chat_work_status_command_for_specific_task(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))
    monkeypatch.setattr(app_globals, "task_manager", SimpleNamespace(
        get_task_including_archive=lambda task_id: SimpleNamespace(
            task_id="abcdef123456",
            status=SimpleNamespace(name="GENERATING_CODE"),
            current_step_description="Writing tests",
            status_reason="In progress",
        )
    ))

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/work-status abcdef12", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "GENERATING_CODE" in payload["response"]
    assert "Writing tests" in payload["response"]


def test_chat_work_status_command_accepts_task_id_prefix(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    active_task = SimpleNamespace(
        task_id="abcdef123456",
        status=SimpleNamespace(name="GENERATING_CODE"),
        current_step_description="Writing implementation",
        status_reason="In progress",
    )

    monkeypatch.setattr(app_globals, "task_manager", SimpleNamespace(
        get_task_including_archive=lambda task_id: None,
        list_active_tasks=lambda: [active_task],
        list_archived_tasks=lambda limit=200: [],
    ))

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/work-status abcdef12", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "GENERATING_CODE" in payload["response"]


def test_chat_work_status_lists_recent_task_statuses(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    task1 = SimpleNamespace(task_id="task11112222", status=SimpleNamespace(name="GENERATING_CODE"), current_step_description=None, status_reason=None)
    task2 = SimpleNamespace(task_id="task33334444", status=SimpleNamespace(name="COMPLETED_SUCCESSFULLY"), current_step_description=None, status_reason=None)

    # Inject delegated metadata into session via chat manager directly.
    session = app_globals.chat_manager.get_session("s1")
    session["metadata"] = {
        "delegated_tasks": [
            {"task_id": task1.task_id},
            {"task_id": task2.task_id},
        ]
    }

    monkeypatch.setattr(app_globals, "task_manager", SimpleNamespace(
        get_task_including_archive=lambda task_id: None,
        list_active_tasks=lambda: [task1],
        list_archived_tasks=lambda limit=200: [task2],
    ))

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/work-status", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "task1111:GENERATING_CODE" in payload["response"]
    assert "task3333:COMPLETED_SUCCESSFULLY" in payload["response"]
