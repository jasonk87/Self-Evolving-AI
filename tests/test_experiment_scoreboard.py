import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from flask import Flask

import app_globals
from ai_assistant.core import experiment_scoreboard
from ai_assistant.core.experiment_scoreboard import (
    get_recent_experiment_scorecards,
    record_experiment_scorecard,
)
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.execution.swarm.protocol import ExperimentScorecard, SwarmContract


def test_experiment_scoreboard_records_recent_scorecards(monkeypatch, tmp_path):
    monkeypatch.setattr(experiment_scoreboard, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("EXPERIMENT_SCOREBOARD_ALLOW_PYTEST", "1")

    scorecard = ExperimentScorecard(
        task_id="task_score",
        tests_run=2,
        tests_passed=1,
        risk_level="medium",
        files_touched=["main.py"],
        accepted=False,
    )

    record = record_experiment_scorecard(
        scorecard,
        actor="test",
        experiment_type="unit",
        source="source_a",
    )
    records = get_recent_experiment_scorecards(limit=1)

    assert records[0]["record_id"] == record["record_id"]
    assert records[0]["scorecard"]["tests_run"] == 2
    assert records[0]["scorecard"]["tests_passed"] == 1
    assert records[0]["scorecard"]["accepted"] is False
    assert records[0]["scorecard"]["files_touched"] == ["main.py"]


def test_action_executor_records_scorecard_for_action(monkeypatch, tmp_path):
    monkeypatch.setattr(experiment_scoreboard, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("EXPERIMENT_SCOREBOARD_ALLOW_PYTEST", "1")

    executor = ActionExecutor(learning_agent=MagicMock())
    executor._run_execution_policy_preflight = MagicMock(return_value={
        "checked": True,
        "allowed": True,
        "blocked": False,
        "reasons": [],
    })
    executor._execute_ephemeral_agent_task = AsyncMock(return_value=True)

    result = asyncio.run(executor.execute_action({
        "source_insight_id": "insight_score",
        "action_type": "EXECUTE_EPHEMERAL_AGENT",
        "details": {"task_description": "Score this action"},
    }))

    records = get_recent_experiment_scorecards(limit=1)
    assert result is True
    assert records[0]["actor"] == "action_executor"
    assert records[0]["experiment_type"] == "EXECUTE_EPHEMERAL_AGENT"
    assert records[0]["scorecard"]["task_id"] == "insight_score"
    assert records[0]["scorecard"]["accepted"] is True


def test_action_executor_scorecard_preserves_policy_block_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(experiment_scoreboard, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("EXPERIMENT_SCOREBOARD_ALLOW_PYTEST", "1")

    executor = ActionExecutor(learning_agent=MagicMock())
    executor._run_execution_policy_preflight = MagicMock(return_value={
        "checked": True,
        "allowed": False,
        "blocked": True,
        "reasons": ["token:hard_stop_block"],
    })
    executor._execute_ephemeral_agent_task = AsyncMock(return_value=True)

    result = asyncio.run(executor.execute_action({
        "source_insight_id": "insight_policy_block",
        "action_type": "EXECUTE_EPHEMERAL_AGENT",
        "details": {"task_description": "Do not run", "projected_cost_usd": 10.0},
    }))

    records = get_recent_experiment_scorecards(limit=1)
    scorecard = records[0]["scorecard"]
    assert result is False
    executor._execute_ephemeral_agent_task.assert_not_called()
    assert scorecard["accepted"] is False
    assert scorecard["blocked"] is True
    assert scorecard["suggested_route"] == "route_to_human_review"
    assert "token:hard_stop_block" in scorecard["failure_reason"]["reason"]
    assert records[0]["metadata"]["failure_message"].startswith("Execution blocked by policy preflight")


def test_action_executor_scorecard_preserves_tool_precondition_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(experiment_scoreboard, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("EXPERIMENT_SCOREBOARD_ALLOW_PYTEST", "1")

    executor = ActionExecutor(learning_agent=MagicMock())
    executor._run_execution_policy_preflight = MagicMock(return_value={
        "checked": True,
        "allowed": True,
        "blocked": False,
        "reasons": [],
    })

    result = asyncio.run(executor.execute_action({
        "source_insight_id": "insight_bad_tool_mod",
        "action_type": "PROPOSE_TOOL_MODIFICATION",
        "details": {
            "tool_name": "/task-action",
            "suggested_change_description": "Fix tool action routing",
        },
    }))

    records = get_recent_experiment_scorecards(limit=1)
    scorecard = records[0]["scorecard"]
    assert result is False
    assert scorecard["accepted"] is False
    assert scorecard["blocked"] is True
    assert scorecard["suggested_route"] == "route_to_human_review"
    assert "Missing module_path or function_name" in scorecard["failure_reason"]["reason"]


@pytest.mark.asyncio
async def test_swarm_coordinator_records_existing_scorecard(monkeypatch, tmp_path):
    from ai_assistant.execution.swarm.coordinator import SubSwarmCoordinator

    monkeypatch.setattr(experiment_scoreboard, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("EXPERIMENT_SCOREBOARD_ALLOW_PYTEST", "1")

    contract = SwarmContract(
        task_id="swarm_score",
        description="Score swarm",
        deliverables=["main.py"],
    )
    coordinator = SubSwarmCoordinator(contract, MagicMock())
    scorecard = coordinator._build_scorecard(accepted=True)
    coordinator._record_scorecard(scorecard, "success")

    records = get_recent_experiment_scorecards(limit=1)
    assert records[0]["actor"] == "sub_swarm_coordinator"
    assert records[0]["experiment_type"] == "sub_swarm"
    assert records[0]["scorecard"]["task_id"] == "swarm_score"
    assert records[0]["scorecard"]["files_touched"] == ["main.py"]


def test_experiment_scoreboard_endpoint_returns_records(monkeypatch, tmp_path):
    monkeypatch.setattr(experiment_scoreboard, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("EXPERIMENT_SCOREBOARD_ALLOW_PYTEST", "1")
    record_experiment_scorecard(
        ExperimentScorecard(task_id="endpoint_score", accepted=True),
        actor="test",
        experiment_type="endpoint",
    )

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
        response = client.get("/api/system/experiment-scoreboard?limit=1")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["records"][0]["scorecard"]["task_id"] == "endpoint_score"
