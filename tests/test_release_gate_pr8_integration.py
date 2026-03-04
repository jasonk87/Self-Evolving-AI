import sys
import types
from types import SimpleNamespace

import pytest
from flask import Flask

import app_globals

sys.modules.setdefault("pyaudio", types.SimpleNamespace())
sys.modules.setdefault(
    "ai_live_link",
    types.SimpleNamespace(
        toggle_live_mode=lambda: None,
        get_status=lambda: "inactive",
        stop_live_mode=lambda: None,
    ),
)

from routes import api_bp


@pytest.mark.integration
def test_reflection_to_specialist_handoff_lifecycle_integration(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(api_bp)

    insight = SimpleNamespace(
        insight_id="i_integration",
        type=SimpleNamespace(name="TOOL_BUG_SUSPECTED"),
        status="NEW",
        description="Investigate failing parser edge case",
        creation_timestamp=123,
        metadata={},
    )

    captured = {}

    def fake_add_task(description, task_type, details=None, session_id=None, related_item_id=None):
        captured["description"] = description
        captured["task_type"] = task_type
        captured["details"] = details or {}
        captured["session_id"] = session_id
        return SimpleNamespace(task_id="task_integration_1")

    learning_agent = SimpleNamespace(insights=[insight], _save_insights=lambda: None)
    orchestrator = SimpleNamespace(
        learning_agent=learning_agent,
        task_manager=SimpleNamespace(add_task=fake_add_task, list_active_tasks=lambda: []),
    )

    monkeypatch.setattr(app_globals, "orchestrator", orchestrator)
    monkeypatch.setattr(app_globals, "task_manager", orchestrator.task_manager, raising=False)

    with app.test_client() as client:
        suggestions = client.get('/api/status/reflection-suggestions')
        assert suggestions.status_code == 200
        item = suggestions.get_json()["items"][0]
        assert item["insight_id"] == "i_integration"

        approved = client.post('/api/status/reflection-suggestions/i_integration/approve', json={"feedback": "looks valid"})
        assert approved.status_code == 200
        assert approved.get_json()["status"] == "APPROVED_BY_USER"

        spawned = client.post('/api/status/reflection-suggestions/i_integration/spawn-specialist', json={})
        assert spawned.status_code == 202
        payload = spawned.get_json()
        assert payload["task_id"] == "task_integration_1"
        assert payload["insight_status"] == "SPECIALIST_SPAWNED"

    assert insight.metadata["specialist_spawn_task_id"] == "task_integration_1"
    assert captured["details"]["template_id"] == "code_reviewer_v1"
    assert captured["details"]["source_insight_id"] == "i_integration"


@pytest.mark.integration
def test_session_continuity_across_simulated_channels(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(api_bp)

    class FakeChatManager:
        def __init__(self):
            self.pointer = {
                "web:user42:chatA": "sess_web_1",
                "telegram:user42:chatA": "sess_tg_1",
            }

        def get_session_for_identity(self, identity_key):
            return self.pointer.get(identity_key)

        def rotate_session_for_identity(self, identity_key):
            new_id = f"rotated_{identity_key.replace(':', '_')}"
            self.pointer[identity_key] = new_id
            return new_id

        def list_identity_pointers(self, limit=200):
            rows = []
            for key, sid in self.pointer.items():
                rows.append({"identity_key": key, "session_id": sid, "session_exists": True})
            return rows[:limit]

        def prune_invalid_identity_pointers(self):
            return 0

    monkeypatch.setattr(app_globals, "chat_manager", FakeChatManager(), raising=False)

    with app.test_client() as client:
        web_ptr = client.get('/api/sessions/identity?platform=web&user_id=user42&chat_id=chatA')
        tg_ptr = client.get('/api/sessions/identity?platform=telegram&user_id=user42&chat_id=chatA')

        assert web_ptr.status_code == 200
        assert tg_ptr.status_code == 200
        assert web_ptr.get_json()["session_id"] == "sess_web_1"
        assert tg_ptr.get_json()["session_id"] == "sess_tg_1"

        reset_tg = client.post('/api/sessions/identity/reset', json={"platform": "telegram", "user_id": "user42", "chat_id": "chatA"})
        assert reset_tg.status_code == 200
        reset_payload = reset_tg.get_json()
        assert reset_payload["previous_session_id"] == "sess_tg_1"
        assert reset_payload["session_id"].startswith("rotated_telegram")

        web_ptr_again = client.get('/api/sessions/identity?platform=web&user_id=user42&chat_id=chatA')
        assert web_ptr_again.status_code == 200
        assert web_ptr_again.get_json()["session_id"] == "sess_web_1"
