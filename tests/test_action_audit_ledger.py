import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

from flask import Flask

import app_globals
from ai_assistant.core import action_audit_ledger
from ai_assistant.core.action_audit_ledger import (
    append_action_audit_event,
    get_recent_action_audit_events,
)
from ai_assistant.core.task_manager import ActiveTaskStatus, ActiveTaskType, TaskManager
from ai_assistant.execution.action_executor import ActionExecutor


def test_action_audit_ledger_appends_recent_events_and_redacts_sensitive_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(action_audit_ledger, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("ACTION_AUDIT_LEDGER_ALLOW_PYTEST", "1")

    append_action_audit_event(
        "ACTION_RECEIVED",
        "test_actor",
        "Sensitive action",
        action_type="PROPOSE_TOOL_MODIFICATION",
        metadata={
            "suggested_code_change": "def dangerous(): pass",
            "api_key": "secret-value",
            "safe": "visible",
        },
    )

    events = get_recent_action_audit_events(limit=1)

    assert len(events) == 1
    assert events[0]["event_type"] == "ACTION_RECEIVED"
    assert events[0]["metadata"]["suggested_code_change"] == "[redacted]"
    assert events[0]["metadata"]["api_key"] == "[redacted]"
    assert events[0]["metadata"]["safe"] == "visible"


def test_task_manager_writes_lifecycle_events_to_action_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(action_audit_ledger, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("ACTION_AUDIT_LEDGER_ALLOW_PYTEST", "1")

    task_manager = TaskManager(filepath=str(tmp_path / "active_tasks.json"))
    task = task_manager.add_task(
        description="Audit lifecycle",
        task_type=ActiveTaskType.AGENT_TOOL_EXECUTION,
        related_item_id="tool_a",
        details={"tool_name": "tool_a"},
        session_id="session_a",
    )
    task_manager.update_task_status(task.task_id, ActiveTaskStatus.RUNNING, step_desc="Running tool")

    events = get_recent_action_audit_events(limit=5)
    event_types = [event["event_type"] for event in events]

    assert "TASK_CREATED" in event_types
    assert "TASK_STATUS_CHANGED" in event_types
    status_event = next(event for event in events if event["event_type"] == "TASK_STATUS_CHANGED")
    assert status_event["task_id"] == task.task_id
    assert status_event["status"] == "RUNNING"
    assert status_event["metadata"]["from_status"] == "INITIALIZING"


def test_action_executor_writes_action_and_policy_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(action_audit_ledger, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("ACTION_AUDIT_LEDGER_ALLOW_PYTEST", "1")

    executor = ActionExecutor(learning_agent=MagicMock())
    executor._run_execution_policy_preflight = MagicMock(return_value={
        "checked": True,
        "allowed": True,
        "blocked": False,
        "reasons": [],
    })
    executor._execute_ephemeral_agent_task = AsyncMock(return_value=True)

    result = asyncio.run(executor.execute_action({
        "source_insight_id": "insight_a",
        "action_type": "EXECUTE_EPHEMERAL_AGENT",
        "details": {
            "task_description": "Try an audited action",
            "projected_cost_usd": 0.1,
        },
    }))

    assert result is True
    events = get_recent_action_audit_events(limit=10)
    assert any(event["event_type"] == "ACTION_RECEIVED" for event in events)
    assert any(
        event["event_type"] == "POLICY_PREFLIGHT_EVALUATED" and event["status"] == "allowed"
        for event in events
    )


def test_action_audit_endpoint_returns_recent_events(monkeypatch, tmp_path):
    monkeypatch.setattr(action_audit_ledger, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("ACTION_AUDIT_LEDGER_ALLOW_PYTEST", "1")
    append_action_audit_event("TASK_CREATED", "test", "Endpoint event")

    sys.modules.setdefault("pyaudio", types.SimpleNamespace())
    sys.modules.setdefault(
        "ai_live_link",
        types.SimpleNamespace(
            toggle_live_mode=lambda: None,
            get_status=lambda: "inactive",
            stop_live_mode=lambda: None,
        ),
    )
    from routes import api_bp  # noqa: PLC0415

    app_globals.orchestrator = MagicMock(get_blocked_tools=lambda: [])
    app = Flask(__name__)
    app.register_blueprint(api_bp)

    with app.test_client() as client:
        response = client.get("/api/system/action-audit?limit=1")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["events"][0]["summary"] == "Endpoint event"
