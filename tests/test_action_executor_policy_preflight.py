import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from ai_assistant.execution.action_executor import ActionExecutor


@patch('ai_assistant.execution.action_executor.global_reflection_log.log_execution')
def test_execute_action_blocks_when_policy_preflight_blocks(mock_log_execution):
    executor = ActionExecutor(learning_agent=MagicMock())
    executor._run_execution_policy_preflight = MagicMock(return_value={
        "checked": True,
        "allowed": False,
        "blocked": True,
        "reasons": ["token:hard_stop_block"],
    })
    executor._execute_ephemeral_agent_task = AsyncMock(return_value=True)

    proposed_action = {
        "source_insight_id": "insight_blocked",
        "action_type": "EXECUTE_EPHEMERAL_AGENT",
        "details": {
            "task_description": "Run autonomous task",
            "projected_cost_usd": 1.0,
        },
    }

    result = asyncio.run(executor.execute_action(proposed_action))

    assert result is False
    executor._execute_ephemeral_agent_task.assert_not_called()
    assert mock_log_execution.called


@patch('ai_assistant.execution.action_executor.global_reflection_log.log_execution')
def test_execute_action_continues_when_policy_preflight_allows(mock_log_execution):
    executor = ActionExecutor(learning_agent=MagicMock())
    executor._run_execution_policy_preflight = MagicMock(return_value={
        "checked": True,
        "allowed": True,
        "blocked": False,
        "reasons": [],
    })
    executor._execute_ephemeral_agent_task = AsyncMock(return_value=True)

    proposed_action = {
        "source_insight_id": "insight_allowed",
        "action_type": "EXECUTE_EPHEMERAL_AGENT",
        "details": {
            "task_description": "Run autonomous task",
            "projected_cost_usd": 0.1,
        },
    }

    result = asyncio.run(executor.execute_action(proposed_action))

    assert result is True
    executor._execute_ephemeral_agent_task.assert_called_once()
