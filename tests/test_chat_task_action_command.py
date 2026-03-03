from types import SimpleNamespace

from flask import Flask

import app_globals
from routes import chat


def _build_test_app():
    app = Flask(__name__)
    app.register_blueprint(chat.chat_bp)
    return app


def test_chat_task_action_command_executes_without_orchestrator(monkeypatch):
    app = _build_test_app()

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

    monkeypatch.setattr(app_globals, "orchestrator", None)
    monkeypatch.setattr(app_globals, "chat_manager", SimpleNamespace(
        create_session=create_session,
        get_session=get_session,
        add_message=add_message,
    ))
    monkeypatch.setattr(chat, "execute_alert_action", lambda task_id, action: {"success": True, "message": f"done:{task_id}:{action}"})

    with app.test_client() as client:
        response = client.post('/chat', json={"message": "/task-action task_1 retry", "session_id": "s1"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["response"] == "done:task_1:retry"
    assert any(m[1] == "assistant" and "done:task_1:retry" in m[2] for m in messages)
