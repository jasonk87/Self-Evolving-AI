import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

from flask import Flask

import app_globals
from ai_assistant.core import action_audit_ledger, experiment_scoreboard, patch_memory, tool_lifecycle
from ai_assistant.core.action_audit_ledger import append_action_audit_event
from ai_assistant.core.patch_memory import add_patch_lesson
from ai_assistant.core.reflection import ActionableInsight, InsightType
from ai_assistant.core.task_manager import ActiveTaskStatus, ActiveTaskType, TaskManager
from ai_assistant.core.tool_lifecycle import record_tool_candidate
from ai_assistant.execution.swarm.protocol import ExperimentScorecard
from ai_assistant.core.experiment_scoreboard import record_experiment_scorecard


def test_run_spine_endpoint_joins_governance_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(action_audit_ledger, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(experiment_scoreboard, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(patch_memory, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(tool_lifecycle, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("ACTION_AUDIT_LEDGER_ALLOW_PYTEST", "1")
    monkeypatch.setenv("EXPERIMENT_SCOREBOARD_ALLOW_PYTEST", "1")
    monkeypatch.setenv("PATCH_MEMORY_ALLOW_PYTEST", "1")
    from routes import system as system_routes

    monkeypatch.setattr(system_routes.approval_manager, "get_pending_requests", lambda: [])

    task_manager = TaskManager(filepath=str(tmp_path / "active_tasks.json"))
    task = task_manager.add_task(
        description="Execute approved insight",
        task_type=ActiveTaskType.AGENT_TOOL_EXECUTION,
        related_item_id="insight_a",
    )
    task_manager.update_task_status(task.task_id, ActiveTaskStatus.RUNNING, step_desc="Executing approved insight")
    append_action_audit_event(
        "POLICY_PREFLIGHT_EVALUATED",
        "action_executor",
        "Policy allowed generated tool lane",
        task_id=task.task_id,
        source="insight_a",
        status="allowed",
    )
    record_experiment_scorecard(
        ExperimentScorecard(
            task_id=task.task_id,
            accepted=False,
            blocked=True,
            tests_run=1,
            tests_passed=0,
            suggested_route="route_to_human_review",
        ),
        actor="test",
        experiment_type="agent_tool_execution",
        source="insight_a",
    )
    add_patch_lesson(
        problem="A failure required human review.",
        failure_class="unknown",
        fix="Stop and ask for review.",
        rule="Unknown failures should not rewrite code.",
        applies_to=["agent_tool_execution"],
    )
    record_tool_candidate(
        tool_name="reverse_image_search",
        module_path="ai_assistant.custom_tools.reverse_image_search",
        function_name="reverse_image_search",
        file_path="ai_assistant/custom_tools/reverse_image_search.py",
        tool_type="generated",
    )
    insight = ActionableInsight(
        insight_id="insight_a",
        type=InsightType.TOOL_BUG_SUSPECTED,
        description="ISSUE DETECTED: approved repair",
        source_reflection_entry_ids=[],
        status="APPROVED_QUEUED",
        metadata={"approval_task_id": task.task_id},
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

    app_globals.orchestrator = SimpleNamespace(
        task_manager=task_manager,
        learning_agent=SimpleNamespace(insights=[insight]),
        get_blocked_tools=MagicMock(return_value=[]),
    )
    app_globals.task_manager = task_manager
    app = Flask(__name__)
    app.register_blueprint(api_bp)

    with app.test_client() as client:
        response = client.get("/api/system/run-spine?limit=5")

    payload = response.get_json()
    snapshot = payload["snapshot"]

    assert response.status_code == 200
    assert payload["success"] is True
    assert snapshot["counts"]["active_tasks"] == 1
    assert snapshot["counts"]["pending_approvals"] == 1
    assert snapshot["counts"]["queued_approvals"] == 1
    assert snapshot["counts"]["scorecards"] == 1
    assert snapshot["counts"]["blocked_scorecards"] == 1
    assert snapshot["counts"]["patch_lessons"] >= 1
    assert snapshot["counts"]["tool_lifecycle_records"] == 1
    assert snapshot["work_items"][0]["id"] == task.task_id
    assert snapshot["work_items"][0]["approvals"][0]["id"] == "insight_a"
    assert snapshot["work_items"][0]["scorecards"][0]["scorecard"]["task_id"] == task.task_id
    assert snapshot["work_items"][0]["audit_events"]
