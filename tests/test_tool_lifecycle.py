import sys
import types
from unittest.mock import MagicMock

from flask import Flask

import app_globals
from ai_assistant.core import tool_lifecycle
from ai_assistant.core.tool_lifecycle import (
    ToolLifecycleState,
    get_tool_lifecycle_record,
    list_tool_lifecycle_records,
    mark_tool_registered,
    maybe_graduate_tool_from_scorecard,
    record_tool_candidate,
    record_tool_execution,
)
from ai_assistant.execution.swarm.protocol import ExperimentScorecard


def test_tool_lifecycle_tracks_candidate_registration_and_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_lifecycle, "get_data_dir", lambda: str(tmp_path))

    record_tool_candidate(
        tool_name="reverse_image_search",
        module_path="ai_assistant.custom_tools.generated.reverse_image_search",
        function_name="reverse_image_search",
        file_path="ai_assistant/custom_tools/generated/reverse_image_search.py",
        tool_type="dynamic_generated",
        source="test",
    )
    mark_tool_registered(
        tool_name="reverse_image_search",
        module_path="ai_assistant.custom_tools.generated.reverse_image_search",
        function_name="reverse_image_search",
        file_path="ai_assistant/custom_tools/generated/reverse_image_search.py",
        tool_type="dynamic_generated",
    )
    record_tool_execution("reverse_image_search", success=True)
    record_tool_execution("reverse_image_search", success=False, error_signature="boom")

    record = get_tool_lifecycle_record("reverse_image_search")

    assert record["state"] == ToolLifecycleState.REGISTERED.value
    assert record["usage_count"] == 1
    assert record["failure_count"] == 1
    assert record["last_error_signature"] == "boom"
    assert len(record["events"]) == 4


def test_tool_lifecycle_graduates_only_with_validation_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_lifecycle, "get_data_dir", lambda: str(tmp_path))
    record_tool_candidate(
        tool_name="validated_tool",
        module_path="ai_assistant.custom_tools.generated.validated_tool",
        function_name="validated_tool",
        file_path="ai_assistant/custom_tools/generated/validated_tool.py",
        tool_type="dynamic_generated",
    )

    no_tests = ExperimentScorecard(task_id="score_a", accepted=True, tests_run=0, tests_passed=0)
    failed_tests = ExperimentScorecard(task_id="score_b", accepted=True, tests_run=2, tests_passed=1)
    passed_tests = ExperimentScorecard(task_id="score_c", accepted=True, tests_run=2, tests_passed=2)

    assert maybe_graduate_tool_from_scorecard("validated_tool", no_tests) is None
    assert maybe_graduate_tool_from_scorecard("validated_tool", failed_tests) is None

    graduated = maybe_graduate_tool_from_scorecard("validated_tool", passed_tests)

    assert graduated is not None
    assert get_tool_lifecycle_record("validated_tool")["state"] == ToolLifecycleState.GRADUATED.value


def test_tool_lifecycle_endpoint_returns_records(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_lifecycle, "get_data_dir", lambda: str(tmp_path))
    record_tool_candidate(
        tool_name="endpoint_tool",
        module_path="ai_assistant.custom_tools.generated.endpoint_tool",
        function_name="endpoint_tool",
        file_path="ai_assistant/custom_tools/generated/endpoint_tool.py",
        tool_type="dynamic_generated",
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
        response = client.get("/api/system/tool-lifecycle?limit=1")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["records"][0]["tool_name"] == "endpoint_tool"


def test_list_tool_lifecycle_records_filters_state(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_lifecycle, "get_data_dir", lambda: str(tmp_path))
    record_tool_candidate(
        tool_name="candidate_tool",
        module_path="ai_assistant.custom_tools.generated.candidate_tool",
        function_name="candidate_tool",
        file_path="ai_assistant/custom_tools/generated/candidate_tool.py",
        tool_type="dynamic_generated",
    )
    mark_tool_registered(
        tool_name="registered_tool",
        module_path="ai_assistant.custom_tools.generated.registered_tool",
        function_name="registered_tool",
        file_path="ai_assistant/custom_tools/generated/registered_tool.py",
        tool_type="dynamic_generated",
    )

    registered = list_tool_lifecycle_records(state="registered")

    assert [record["tool_name"] for record in registered] == ["registered_tool"]
