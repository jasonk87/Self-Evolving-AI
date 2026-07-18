from types import SimpleNamespace
from unittest.mock import MagicMock

from flask import Flask

import app_globals
from ai_assistant.core.reflection import ActionableInsight, InsightType
from ai_assistant.core.task_manager import ActiveTaskStatus
from routes import api_bp, approvals


def test_queue_insight_execution_returns_before_self_healing_runs(monkeypatch):
    insight = ActionableInsight(
        type=InsightType.TOOL_BUG_SUSPECTED,
        description="ISSUE DETECTED: tool needs repair",
        source_reflection_entry_ids=[],
        related_tool_name="example_tool",
    )
    learning_agent = SimpleNamespace(
        _save_insights=MagicMock(),
        execute_self_healing_for_insight=MagicMock(),
    )
    task = SimpleNamespace(task_id="task_queued_approval")
    task_manager = SimpleNamespace(
        add_task=MagicMock(return_value=task),
        update_task_status=MagicMock(),
    )
    scheduled = []

    monkeypatch.setattr(
        approvals.app_globals,
        "orchestrator",
        SimpleNamespace(learning_agent=learning_agent, task_manager=task_manager),
    )
    monkeypatch.setattr(approvals.app_globals, "learning_agent", learning_agent, raising=False)
    monkeypatch.setattr(approvals.app_globals, "task_manager", task_manager)
    monkeypatch.setattr(approvals.app_globals, "ai_loop", object(), raising=False)
    monkeypatch.setattr(
        approvals.asyncio,
        "run_coroutine_threadsafe",
        lambda coro, loop: scheduled.append((coro, loop)),
    )

    result = approvals._queue_insight_execution(insight)

    assert result == {
        "success": True,
        "task_id": "task_queued_approval",
        "message": "Insight approval queued for background execution.",
    }
    assert insight.status == "APPROVED_QUEUED"
    assert insight.metadata["approval_execution_mode"] == "background"
    assert insight.metadata["approval_task_id"] == "task_queued_approval"
    assert learning_agent._save_insights.call_count == 1
    learning_agent.execute_self_healing_for_insight.assert_not_called()
    assert len(scheduled) == 1
    assert task_manager.update_task_status.call_args_list[0].args[1] == ActiveTaskStatus.RUNNING

    scheduled[0][0].close()


def test_queue_insight_execution_is_idempotent_when_already_queued(monkeypatch):
    insight = ActionableInsight(
        type=InsightType.TOOL_BUG_SUSPECTED,
        description="ISSUE DETECTED: tool needs repair",
        source_reflection_entry_ids=[],
        related_tool_name="example_tool",
        status="APPROVED_QUEUED",
        metadata={"approval_task_id": "task_existing"},
    )
    learning_agent = SimpleNamespace(
        _save_insights=MagicMock(),
        execute_self_healing_for_insight=MagicMock(),
    )
    task_manager = SimpleNamespace(
        add_task=MagicMock(),
        update_task_status=MagicMock(),
    )

    monkeypatch.setattr(
        approvals.app_globals,
        "orchestrator",
        SimpleNamespace(learning_agent=learning_agent, task_manager=task_manager),
    )
    monkeypatch.setattr(approvals.app_globals, "task_manager", task_manager)
    monkeypatch.setattr(approvals.app_globals, "ai_loop", object(), raising=False)
    run_coroutine = MagicMock()
    monkeypatch.setattr(approvals.asyncio, "run_coroutine_threadsafe", run_coroutine)

    result = approvals._queue_insight_execution(insight)

    assert result == {
        "success": True,
        "task_id": "task_existing",
        "message": "Insight approval is already queued for background execution.",
    }
    task_manager.add_task.assert_not_called()
    learning_agent._save_insights.assert_not_called()
    run_coroutine.assert_not_called()


def test_queue_insight_execution_marks_failed_when_scheduling_fails(monkeypatch):
    insight = ActionableInsight(
        type=InsightType.TOOL_BUG_SUSPECTED,
        description="ISSUE DETECTED: tool needs repair",
        source_reflection_entry_ids=[],
        related_tool_name="example_tool",
    )
    learning_agent = SimpleNamespace(
        _save_insights=MagicMock(),
        execute_self_healing_for_insight=MagicMock(),
    )
    task = SimpleNamespace(task_id="task_schedule_failure")
    task_manager = SimpleNamespace(
        add_task=MagicMock(return_value=task),
        update_task_status=MagicMock(),
    )

    monkeypatch.setattr(
        approvals.app_globals,
        "orchestrator",
        SimpleNamespace(learning_agent=learning_agent, task_manager=task_manager),
    )
    monkeypatch.setattr(approvals.app_globals, "task_manager", task_manager)
    monkeypatch.setattr(approvals.app_globals, "ai_loop", object(), raising=False)
    monkeypatch.setattr(
        approvals.asyncio,
        "run_coroutine_threadsafe",
        MagicMock(side_effect=RuntimeError("loop closed")),
    )

    result = approvals._queue_insight_execution(insight)

    assert result == {
        "success": False,
        "task_id": "task_schedule_failure",
        "error": "Could not schedule approved insight execution.",
    }
    assert insight.status == "ACTION_FAILED"
    assert insight.metadata["approval_error"] == "loop closed"
    assert learning_agent._save_insights.call_count == 2
    assert task_manager.update_task_status.call_args_list[-1].args[1] == ActiveTaskStatus.FAILED_UNKNOWN


def test_approval_releases_quarantined_tool_even_when_repair_queue_fails(monkeypatch):
    insight = ActionableInsight(
        type=InsightType.TOOL_BUG_SUSPECTED,
        description="Tool recall_facts was quarantined after repeated failures.",
        source_reflection_entry_ids=[],
        related_tool_name="recall_facts",
    )
    learning_agent = SimpleNamespace(insights=[insight], _save_insights=MagicMock())
    unblocked = []

    orchestrator = SimpleNamespace(
        learning_agent=learning_agent,
        get_blocked_tools=lambda: {"recall_facts": {"reason": "test failure"}},
        unblock_tool=lambda tool_name: unblocked.append(tool_name) or True,
    )
    monkeypatch.setattr(app_globals, "orchestrator", orchestrator)
    monkeypatch.setattr(
        approvals,
        "approval_manager",
        SimpleNamespace(get_request=lambda req_id: None),
    )
    monkeypatch.setattr(
        approvals,
        "_queue_insight_execution",
        lambda approved: {"success": False, "error": "AI event loop is unavailable."},
    )

    from ai_assistant.core import tool_lifecycle
    from ai_assistant.goals import goal_management

    monkeypatch.setattr(tool_lifecycle, "upsert_tool_lifecycle", MagicMock())
    monkeypatch.setattr(goal_management, "get_goal", lambda req_id: None)

    app = Flask(__name__)
    app.register_blueprint(api_bp)
    with app.test_client() as client:
        response = client.post(f"/api/approvals/{insight.insight_id}/approve")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["repair_queued"] is False
    assert payload["quarantine"] == {"released": True, "tool_name": "recall_facts"}
    assert unblocked == ["recall_facts"]


def test_approval_timestamp_normalizes_iso_and_milliseconds():
    iso_seconds = approvals._approval_timestamp_seconds("2026-07-18T13:06:27.256613+00:00")
    millisecond_seconds = approvals._approval_timestamp_seconds(iso_seconds * 1000)

    assert iso_seconds > 0
    assert millisecond_seconds == iso_seconds


def test_learning_insight_api_includes_numeric_timestamp(monkeypatch):
    insight = ActionableInsight(
        type=InsightType.TOOL_BUG_SUSPECTED,
        description="Verified test repair",
        source_reflection_entry_ids=[],
        related_tool_name="example_tool",
        creation_timestamp="2026-07-18T13:06:27.256613+00:00",
    )
    learning_agent = SimpleNamespace(insights=[insight])
    monkeypatch.setattr(
        app_globals,
        "orchestrator",
        SimpleNamespace(learning_agent=learning_agent),
    )
    monkeypatch.setattr(
        approvals,
        "approval_manager",
        SimpleNamespace(get_pending_requests=lambda: []),
    )

    from ai_assistant.goals import goal_management

    monkeypatch.setattr(goal_management, "list_goals", lambda status=None: [])

    app = Flask(__name__)
    app.register_blueprint(api_bp)
    with app.test_client() as client:
        response = client.get("/api/approvals")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["approvals"][0]["created_at"] == insight.creation_timestamp
    assert isinstance(payload["approvals"][0]["timestamp"], float)
    assert payload["approvals"][0]["timestamp"] > 0
