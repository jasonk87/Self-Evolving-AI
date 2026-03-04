from types import SimpleNamespace

from flask import Flask

import app_globals
from routes import chat, api_bp


def _build_test_app():
    app = Flask(__name__)
    app.register_blueprint(chat.chat_bp)
    return app


def _build_api_test_app():
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    return app


def _setup_chat_manager(monkeypatch):
    messages = []
    session_store = {"s1": {"id": "s1", "history": []}}

    def create_session(title="New Chat"):
        return "s1"

    def get_session(session_id):
        return session_store.get(session_id)

    def delete_session(session_id):
        existed = session_id in session_store
        session_store.pop(session_id, None)
        return existed

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

    notice_store = []
    identity_map = {}

    def get_or_create_session_for_identity(identity_key, title="New Chat"):
        if identity_key in identity_map:
            return identity_map[identity_key]
        sid = create_session(title=title)
        identity_map[identity_key] = sid
        return sid

    def rotate_session_for_identity(identity_key, title="New Chat"):
        sid = f"s{len(session_store)+1}"
        session_store[sid] = {"id": sid, "title": title, "history": []}
        identity_map[identity_key] = sid
        return sid

    def list_identity_pointers(limit=100):
        items = []
        for identity_key, sid in identity_map.items():
            items.append({
                "identity_key": identity_key,
                "session_id": sid,
                "session_exists": sid in session_store,
            })
        return sorted(items, key=lambda i: i["identity_key"])[:limit]

    def prune_invalid_identity_pointers():
        removed = 0
        for identity_key in list(identity_map.keys()):
            sid = identity_map[identity_key]
            if sid not in session_store:
                identity_map.pop(identity_key, None)
                removed += 1
        return removed

    def add_user_notice(user_scope, message, notice_type="delegated_work", source_session_id=None, task_id=None, status=None, metadata=None):
        notice = {
            "id": f"n{len(notice_store)+1}",
            "read": False,
            "scope": user_scope,
            "message": message,
            "type": notice_type,
            "source_session_id": source_session_id,
            "task_id": task_id,
            "status": status,
            "metadata": metadata or {},
        }
        notice_store.append(notice)
        return notice

    def list_user_notices(user_scope, include_read=False, limit=20):
        data = [n for n in notice_store if n["scope"] == user_scope and (include_read or not n["read"])]
        return data[:limit]

    def mark_user_notices_read(user_scope, notice_ids=None):
        count = 0
        ids = set(notice_ids or [])
        for n in notice_store:
            if n["scope"] != user_scope:
                continue
            if notice_ids and n["id"] not in ids:
                continue
            if not n["read"]:
                n["read"] = True
                count += 1
        return count

    monkeypatch.setattr(app_globals, "chat_manager", SimpleNamespace(
        create_session=create_session,
        get_session=get_session,
        delete_session=delete_session,
        add_message=add_message,
        update_session_metadata=update_session_metadata,
        add_user_notice=add_user_notice,
        list_user_notices=list_user_notices,
        mark_user_notices_read=mark_user_notices_read,
        get_or_create_session_for_identity=get_or_create_session_for_identity,
        rotate_session_for_identity=rotate_session_for_identity,
        get_session_for_identity=lambda identity_key: identity_map.get(identity_key),
        list_identity_pointers=list_identity_pointers,
        prune_invalid_identity_pointers=prune_invalid_identity_pointers,
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
    assert "background" in payload["response"].lower()
    assert added["session_id"] == "s1"
    assert added["details"]["source"] == "chat_delegate"
    assert added["details"]["worker_profile"] == "coder_worker"
    assert added["details"]["scope_type"] == "session"
    assert added["details"]["capability_profile"] == "workspace_code_generation"
    assert added["details"]["retention_policy"] == "drop_task_memory_on_completion_keep_artifacts"
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


def test_chat_work_inbox_lists_notices(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    app_globals.chat_manager.add_user_notice(
        "local_default",
        "Delegated task a1b2 completed.",
        source_session_id="s1",
        task_id="a1b2c3d4",
        status="completed",
    )

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/work-inbox", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "Work Inbox:" in payload["response"]
    assert "completed" in payload["response"]


def test_chat_work_inbox_clear_marks_read(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    app_globals.chat_manager.add_user_notice(
        "local_default",
        "Delegated task z9y8 failed.",
        source_session_id="s1",
        task_id="z9y8x7w6",
        status="failed",
    )

    with app.test_client() as client:
        clear_response = client.post('/chat', json={"message": "/work-inbox clear", "session_id": "s1"})
        list_response = client.post('/chat', json={"message": "/work-inbox", "session_id": "s1"})

    assert clear_response.status_code == 200
    assert "Marked 1 work notice" in clear_response.get_json()["response"]
    assert list_response.status_code == 200
    assert "No work notices yet" in list_response.get_json()["response"]


def test_chat_work_inbox_read_specific_notice(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    app_globals.chat_manager.add_user_notice(
        "local_default",
        "Delegated task AAA completed.",
        source_session_id="s1",
        task_id="AAA11111",
        status="completed",
    )
    app_globals.chat_manager.add_user_notice(
        "local_default",
        "Delegated task BBB failed.",
        source_session_id="s1",
        task_id="BBB22222",
        status="failed",
    )

    with app.test_client() as client:
        read_response = client.post('/chat', json={"message": "/work-inbox read n1", "session_id": "s1"})
        list_response = client.post('/chat', json={"message": "/work-inbox", "session_id": "s1"})

    assert read_response.status_code == 200
    assert "Marked 1 selected work notice" in read_response.get_json()["response"]
    assert list_response.status_code == 200
    response_text = list_response.get_json()["response"]
    assert "BBB22222"[:8] in response_text
    assert "AAA11111"[:8] not in response_text


def test_chat_work_inbox_read_usage_error(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/work-inbox read ", "session_id": "s1"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "Usage: /work-inbox read" in payload["response"]


def test_chat_identity_continuity_reuses_session(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    with app.test_client() as client:
        first = client.post('/chat', json={"message": "/work-inbox", "platform": "telegram", "user_id": "u1", "chat_id": "c1"})
        second = client.post('/chat', json={"message": "/work-inbox", "platform": "telegram", "user_id": "u1", "chat_id": "c1"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()["session_id"] == second.get_json()["session_id"]


def test_chat_start_rotates_identity_session(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    with app.test_client() as client:
        first = client.post('/chat', json={"message": "/work-inbox", "platform": "telegram", "user_id": "u1", "chat_id": "c2"})
        restart = client.post('/chat', json={"message": "/start", "platform": "telegram", "user_id": "u1", "chat_id": "c2"})
        third = client.post('/chat', json={"message": "/work-inbox", "platform": "telegram", "user_id": "u1", "chat_id": "c2"})

    assert first.status_code == 200
    assert restart.status_code == 200
    assert third.status_code == 200
    assert restart.get_json()["session_id"] != first.get_json()["session_id"]
    assert third.get_json()["session_id"] == restart.get_json()["session_id"]


def test_chat_work_inbox_open_notice_returns_context(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))
    monkeypatch.setattr(app_globals, "task_manager", SimpleNamespace(
        get_task_including_archive=lambda task_id: SimpleNamespace(task_id="abc12345ef", status=SimpleNamespace(name="COMPLETED_SUCCESSFULLY")),
        get_task=lambda task_id: None,
        list_active_tasks=lambda: [],
        list_archived_tasks=lambda limit=200: [],
    ))

    notice = app_globals.chat_manager.add_user_notice(
        "local_default",
        "Delegated task abc completed.",
        source_session_id="origin-session-1",
        task_id="abc12345ef",
        status="completed",
    )

    with app.test_client() as client:
        response = client.post('/chat', json={"message": f"/work-inbox open {notice['id']}", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "origin-session-1" in payload["response"]
    assert "COMPLETED_SUCCESSFULLY" in payload["response"]




def test_chat_work_inbox_retry_notice_executes_retry(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    notice = app_globals.chat_manager.add_user_notice(
        "local_default",
        "Delegated task failed.",
        source_session_id="origin-session-2",
        task_id="task_xyz_12345",
        status="failed",
    )

    monkeypatch.setattr(chat, "execute_alert_action", lambda task_id, action: {
        "success": True,
        "message": f"retrying:{task_id}:{action}",
        "new_task_id": "task_retry_1",
    })

    with app.test_client() as client:
        response = client.post('/chat', json={"message": f"/work-inbox retry {notice['id']}", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "retrying:task_xyz_12345:retry" in payload["response"]
    assert "task_ret" in payload["response"]


def test_chat_work_inbox_retry_notice_without_task_returns_400(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    notice = app_globals.chat_manager.add_user_notice(
        "local_default",
        "General notice with no task.",
        source_session_id="origin-session-3",
        task_id=None,
        status="failed",
    )

    with app.test_client() as client:
        response = client.post('/chat', json={"message": f"/work-inbox retry {notice['id']}", "session_id": "s1"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "not linked to a task" in payload["response"]

def test_chat_work_inbox_open_notice_not_found(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/work-inbox open missing", "session_id": "s1"})

    assert response.status_code == 404
    payload = response.get_json()
    assert payload["success"] is False
    assert "No work notice found" in payload["response"]


def test_chat_session_info_command_identity_mapping(monkeypatch):
    app = _build_test_app()

    _setup_chat_manager(monkeypatch)
    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "config_manager", SimpleNamespace(
        get_all_settings=lambda: {"ENABLE_THINKING": True},
        coerce_setting_value=lambda key, raw: raw,
        update_setting=lambda key, value: True,
    ))

    with app.test_client() as client:
        warm = client.post('/chat', json={"message": "/work-inbox", "platform": "telegram", "user_id": "u99", "chat_id": "c88"})
        info = client.post('/chat', json={"message": "/session-info", "platform": "telegram", "user_id": "u99", "chat_id": "c88"})

    assert warm.status_code == 200
    assert info.status_code == 200
    payload = info.get_json()
    assert payload["success"] is True
    assert "Identity: telegram:u99:c88" in payload["response"]
    assert "Mapped Session:" in payload["response"]


def test_identity_session_pointer_api_roundtrip(monkeypatch):
    app = _build_api_test_app()

    _setup_chat_manager(monkeypatch)

    # Prime mapping through chat continuity flow.
    chat_app = _build_test_app()
    with chat_app.test_client() as chat_client:
        warm = chat_client.post('/chat', json={"message": "/work-inbox", "platform": "telegram", "user_id": "u5", "chat_id": "c5"})
    assert warm.status_code == 200

    with app.test_client() as client:
        get_resp = client.get('/api/sessions/identity', query_string={"platform": "telegram", "user_id": "u5", "chat_id": "c5"})

    assert get_resp.status_code == 200
    payload = get_resp.get_json()
    assert payload["success"] is True
    assert payload["identity_key"] == "telegram:u5:c5"
    assert payload["session_id"]


def test_identity_session_pointer_reset_api(monkeypatch):
    app = _build_api_test_app()

    _setup_chat_manager(monkeypatch)

    chat_app = _build_test_app()
    with chat_app.test_client() as chat_client:
        warm = chat_client.post('/chat', json={"message": "/work-inbox", "platform": "telegram", "user_id": "u6", "chat_id": "c6"})
    assert warm.status_code == 200
    old_session = warm.get_json()["session_id"]

    with app.test_client() as client:
        reset = client.post('/api/sessions/identity/reset', json={"platform": "telegram", "user_id": "u6", "chat_id": "c6"})
        check = client.get('/api/sessions/identity', query_string={"platform": "telegram", "user_id": "u6", "chat_id": "c6"})

    assert reset.status_code == 200
    reset_payload = reset.get_json()
    assert reset_payload["success"] is True
    assert reset_payload["previous_session_id"] == old_session
    assert reset_payload["session_id"] != old_session

    assert check.status_code == 200
    assert check.get_json()["session_id"] == reset_payload["session_id"]


def test_identity_session_pointer_list_api(monkeypatch):
    app = _build_api_test_app()

    _setup_chat_manager(monkeypatch)

    chat_app = _build_test_app()
    with chat_app.test_client() as chat_client:
        chat_client.post('/chat', json={"message": "/work-inbox", "platform": "telegram", "user_id": "u8", "chat_id": "c8"})

    with app.test_client() as client:
        response = client.get('/api/sessions/identity/list')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["count"] >= 1
    assert any(item["identity_key"] == "telegram:u8:c8" for item in payload["pointers"])


def test_identity_session_pointer_prune_api(monkeypatch):
    app = _build_api_test_app()

    _setup_chat_manager(monkeypatch)

    # Create pointer, then remove backing session to make it stale.
    chat_app = _build_test_app()
    with chat_app.test_client() as chat_client:
        warm = chat_client.post('/chat', json={"message": "/work-inbox", "platform": "telegram", "user_id": "u9", "chat_id": "c9"})
    sid = warm.get_json()["session_id"]

    # Delete mapped session through existing route.
    with app.test_client() as client:
        client.delete(f'/api/sessions/{sid}')
        prune = client.post('/api/sessions/identity/prune')

    assert prune.status_code == 200
    payload = prune.get_json()
    assert payload["success"] is True
    assert payload["removed"] >= 1



def test_identity_session_pointer_api_rejects_partial_tuple(monkeypatch):
    app = _build_api_test_app()

    _setup_chat_manager(monkeypatch)

    with app.test_client() as client:
        response = client.get('/api/sessions/identity', query_string={"platform": "telegram", "user_id": "u5"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "must all be provided together" in payload["error"]


def test_identity_session_pointer_api_rejects_ambiguous_identity_payload(monkeypatch):
    app = _build_api_test_app()

    _setup_chat_manager(monkeypatch)

    with app.test_client() as client:
        response = client.get(
            '/api/sessions/identity',
            query_string={
                "identity_key": "telegram:u5:c5",
                "platform": "telegram",
                "user_id": "u5",
                "chat_id": "c5",
            },
        )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "either identity_key or platform+user_id+chat_id" in payload["error"]


def test_identity_session_pointer_reset_rejects_oversized_identity_key(monkeypatch):
    app = _build_api_test_app()

    _setup_chat_manager(monkeypatch)

    with app.test_client() as client:
        response = client.post('/api/sessions/identity/reset', json={"identity_key": "x" * 513})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "exceeds max length" in payload["error"]
