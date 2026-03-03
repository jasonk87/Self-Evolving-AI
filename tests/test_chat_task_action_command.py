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

    monkeypatch.setattr(app_globals, "chat_manager", SimpleNamespace(
        create_session=create_session,
        get_session=get_session,
        add_message=add_message,
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
