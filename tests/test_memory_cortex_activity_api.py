from types import SimpleNamespace
import datetime

from flask import Flask

import app_globals
from ai_assistant.core import memory_manager as memory_module
from ai_assistant.core.memory_manager import MemoryManager
from routes import api_bp


def _build_app():
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    return app


def test_cortex_activity_endpoint_returns_schema(monkeypatch):
    app = _build_app()

    captured = {}

    def fake_summary(**kwargs):
        captured.update(kwargs)
        return {
            "lookback_hours": kwargs.get("lookback_hours", 24),
            "generated_at": "2025-01-01T00:00:00+00:00",
            "filters": {
                "kinds": ["facts"],
                "source": "user_interface",
                "permanence": "permanent",
            },
            "totals": {"facts": 3, "insights": 0, "episodes": 0},
            "recent": {
                "added": {"facts": 1, "insights": 0, "episodes": 0},
                "updated": {"facts": 0, "insights": 0, "episodes": 0},
            },
            "total_recent_changes": 1,
            "anomalies": [],
            "latest_changes": [],
        }

    fake_manager = SimpleNamespace(get_cortex_activity_summary=fake_summary)
    monkeypatch.setattr(app_globals, "memory_manager", fake_manager)

    with app.test_client() as client:
        response = client.get('/api/memory/cortex-activity?hours=12&kinds=facts&source=user_interface&permanence=permanent')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["schema_version"] == 2
    assert payload["activity"]["totals"]["facts"] == 3
    assert captured["lookback_hours"] == 12
    assert captured["kinds"] == ["facts"]
    assert captured["source"] == "user_interface"
    assert captured["permanence"] == "permanent"


def test_memory_manager_cortex_activity_summary_counts_recent(monkeypatch):
    current = datetime.datetime.now(datetime.timezone.utc)
    now = current.isoformat()
    old = (current - datetime.timedelta(days=12)).isoformat()

    monkeypatch.setattr(memory_module, "load_learned_facts", lambda: [
        {
            "fact_id": "fact_1",
            "text": "Recent fact",
            "created_at": now,
            "updated_at": now,
            "source": "user_interface",
            "permanence": "permanent",
        },
        {
            "fact_id": "fact_2",
            "text": "Old fact",
            "created_at": old,
            "updated_at": old,
            "source": "background",
            "permanence": "transient",
        },
    ])
    monkeypatch.setattr(memory_module, "load_actionable_insights", lambda: [
        {
            "insight_id": "ins_1",
            "description": "Insight updated recently",
            "created_at": old,
            "updated_at": now,
        }
    ])
    monkeypatch.setattr(memory_module, "load_episodic_memories", lambda: [
        {"episode_id": "ep_1", "title": "Recent episode", "created_at": now, "updated_at": now}
    ])

    manager = MemoryManager.__new__(MemoryManager)
    summary = manager.get_cortex_activity_summary(lookback_hours=24, limit=5)

    assert summary["totals"] == {"facts": 2, "insights": 1, "episodes": 1}
    assert summary["recent"]["added"]["facts"] == 1
    assert summary["recent"]["added"]["episodes"] == 1
    assert summary["recent"]["updated"]["insights"] == 1
    assert summary["total_recent_changes"] >= 1
    assert len(summary["latest_changes"]) >= 1


def test_memory_manager_cortex_activity_summary_applies_filters(monkeypatch):
    current = datetime.datetime.now(datetime.timezone.utc)
    now = current.isoformat()

    monkeypatch.setattr(memory_module, "load_learned_facts", lambda: [
        {
            "fact_id": "fact_1",
            "text": "Wanted fact",
            "created_at": now,
            "updated_at": now,
            "source": "user_interface",
            "permanence": "permanent",
        },
        {
            "fact_id": "fact_2",
            "text": "Filtered fact",
            "created_at": now,
            "updated_at": now,
            "source": "background",
            "permanence": "transient",
        },
    ])
    monkeypatch.setattr(memory_module, "load_actionable_insights", lambda: [])
    monkeypatch.setattr(memory_module, "load_episodic_memories", lambda: [])

    manager = MemoryManager.__new__(MemoryManager)
    summary = manager.get_cortex_activity_summary(
        lookback_hours=24,
        kinds=["facts"],
        source="user_interface",
        permanence="permanent",
    )

    assert summary["totals"] == {"facts": 1, "insights": 0, "episodes": 0}
    assert summary["filters"]["kinds"] == ["facts"]
    assert summary["filters"]["source"] == "user_interface"
    assert summary["filters"]["permanence"] == "permanent"
