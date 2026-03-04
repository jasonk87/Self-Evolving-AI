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
from routes import approvals


@pytest.mark.smoke
def test_mission_control_operator_smoke_snapshot_and_specialist_actions(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(api_bp)

    insight = SimpleNamespace(
        insight_id="i_smoke",
        type=SimpleNamespace(name="TOOL_BUG_SUSPECTED"),
        status="NEW",
        description="Smoke test reflection item",
        creation_timestamp=999,
        metadata={},
    )

    spawned = {"count": 0}

    def fake_add_task(description, task_type, details=None, session_id=None, related_item_id=None):
        spawned["count"] += 1
        return SimpleNamespace(task_id=f"task_smoke_{spawned['count']}")

    orchestrator = SimpleNamespace(
        task_manager=SimpleNamespace(add_task=fake_add_task, list_active_tasks=lambda: [SimpleNamespace(task_id="task_a")]),
        learning_agent=SimpleNamespace(insights=[insight], _save_insights=lambda: None),
    )

    monkeypatch.setattr(app_globals, "orchestrator", orchestrator)
    monkeypatch.setattr(app_globals, "task_manager", orchestrator.task_manager, raising=False)
    monkeypatch.setattr(
        approvals,
        "get_status_snapshot",
        lambda active_tasks_count, active_tasks=None: {
            "background_tasks": active_tasks_count,
            "delegation_topology": [],
        },
    )

    with app.test_client() as client:
        snapshot = client.get('/api/status/snapshot?summary_only=1')
        assert snapshot.status_code == 200
        assert snapshot.get_json()["success"] is True

        suggestions = client.get('/api/status/reflection-suggestions')
        assert suggestions.status_code == 200
        assert suggestions.get_json()["count"] == 1

        approve = client.post('/api/status/reflection-suggestions/i_smoke/approve', json={})
        assert approve.status_code == 200
        assert approve.get_json()["status"] == "APPROVED_BY_USER"

        spawn = client.post('/api/status/reflection-suggestions/i_smoke/spawn-specialist', json={})
        assert spawn.status_code == 202
        assert spawn.get_json()["task_id"] == "task_smoke_1"
