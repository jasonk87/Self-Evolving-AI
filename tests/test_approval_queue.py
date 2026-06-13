from types import SimpleNamespace
from unittest.mock import MagicMock

from ai_assistant.core.reflection import ActionableInsight, InsightType
from ai_assistant.core.task_manager import ActiveTaskStatus
from routes import approvals


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
