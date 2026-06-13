import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_assistant.core import patch_memory
from ai_assistant.core.experiment_scoreboard import record_experiment_scorecard
from ai_assistant.core.patch_memory import (
    add_patch_lesson,
    format_patch_lessons_for_prompt,
    get_patch_memory_path,
    search_patch_lessons,
)
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.execution.swarm.agents.coder import CoderAgent
from ai_assistant.execution.swarm.blackboard import Blackboard, BlackboardEvent
from ai_assistant.execution.swarm.protocol import (
    ExperimentScorecard,
    SwarmContract,
    classify_failure,
)


class RecordingProvider:
    def __init__(self, response="fixed_code"):
        self.response = response
        self.prompts = []

    async def invoke_ollama_model_async(self, prompt, temperature=0.2):
        self.prompts.append(prompt)
        return self.response


def test_patch_memory_stores_searches_and_formats_lessons(monkeypatch, tmp_path):
    monkeypatch.setattr(patch_memory, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("PATCH_MEMORY_ALLOW_PYTEST", "1")

    lesson = add_patch_lesson(
        problem="Dependency was missing during validation.",
        failure_class="dependency_missing",
        fix="Add package to requirements-core.txt.",
        rule="Every new import must be declared in a dependency file.",
        applies_to=["sub_swarm"],
        source_scorecard_id="score_a",
        metadata={"files_touched": ["requirements-core.txt"]},
    )

    results = search_patch_lessons(
        failure_class="dependency_missing",
        action_type="sub_swarm",
        file_path="requirements-core.txt",
    )
    prompt_text = format_patch_lessons_for_prompt(results)

    assert get_patch_memory_path().endswith("patch_memory.json")
    assert results[0]["lesson_id"] == lesson["lesson_id"]
    assert "Prior repair lessons" in prompt_text
    assert "Every new import" in prompt_text


def test_patch_memory_returns_recent_lessons_without_search_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(patch_memory, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("PATCH_MEMORY_ALLOW_PYTEST", "1")

    older = add_patch_lesson(
        problem="Older problem",
        failure_class="unknown",
        fix="Older fix",
        rule="Older rule",
        applies_to=["general"],
    )
    newer = add_patch_lesson(
        problem="Newer problem",
        failure_class="dependency_missing",
        fix="Newer fix",
        rule="Newer rule",
        applies_to=["general"],
    )

    recent = patch_memory.get_recent_patch_lessons(limit=2)

    assert [lesson["lesson_id"] for lesson in recent] == [
        newer["lesson_id"],
        older["lesson_id"],
    ]


def test_failed_scorecard_derives_patch_lesson(monkeypatch, tmp_path):
    from ai_assistant.core import experiment_scoreboard

    monkeypatch.setattr(experiment_scoreboard, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(patch_memory, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("EXPERIMENT_SCOREBOARD_ALLOW_PYTEST", "1")
    monkeypatch.setenv("PATCH_MEMORY_ALLOW_PYTEST", "1")

    scorecard = ExperimentScorecard(
        task_id="score_dep",
        files_touched=["main.py", "requirements-core.txt"],
        failure_reason=classify_failure("ModuleNotFoundError: No module named 'requests'"),
        accepted=False,
        suggested_route="route_to_dependency_fix",
    )

    record_experiment_scorecard(
        scorecard,
        actor="test",
        experiment_type="sub_swarm",
        source="swarm_a",
    )
    lessons = search_patch_lessons(
        failure_class="dependency_missing",
        action_type="sub_swarm",
        file_path="requirements-core.txt",
    )

    assert lessons
    assert lessons[0]["rule"] == "Every new external import must be represented in a dependency file."


def test_duplicate_patch_lessons_merge_without_incrementing_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(patch_memory, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("PATCH_MEMORY_ALLOW_PYTEST", "1")

    first = add_patch_lesson(
        problem="Dependency was missing during validation.",
        failure_class="dependency_missing",
        fix="Add package to requirements-core.txt.",
        rule="Every new import must be declared in a dependency file.",
        applies_to=["sub_swarm", "general"],
        source_scorecard_id="score_a",
        metadata={"files_touched": ["requirements-core.txt"], "source": "swarm_a"},
    )
    second = add_patch_lesson(
        problem="Dependency   was missing during validation!",
        failure_class="dependency_missing",
        fix="Update dependency manifest.",
        rule="Every new import must be declared in a dependency file",
        applies_to=["general", "sub_swarm"],
        source_scorecard_id="score_b",
        metadata={"files_touched": ["requirements.txt"], "source": "swarm_b"},
    )

    lessons = search_patch_lessons(failure_class="dependency_missing", action_type="sub_swarm", limit=10)
    stored = patch_memory._load_lessons()

    assert first["lesson_id"] == second["lesson_id"]
    assert len(stored) == 1
    assert lessons[0]["occurrence_count"] == 2
    assert lessons[0]["times_reused"] == 0
    assert sorted(lessons[0]["metadata"]["files_touched"]) == ["requirements-core.txt", "requirements.txt"]
    assert sorted(lessons[0]["metadata"]["sources"]) == ["swarm_a", "swarm_b"]


def test_duplicate_derived_lessons_merge_instead_of_bloating_file(monkeypatch, tmp_path):
    from ai_assistant.core import experiment_scoreboard

    monkeypatch.setattr(experiment_scoreboard, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(patch_memory, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("EXPERIMENT_SCOREBOARD_ALLOW_PYTEST", "1")
    monkeypatch.setenv("PATCH_MEMORY_ALLOW_PYTEST", "1")

    scorecard = ExperimentScorecard(
        task_id="score_dep",
        files_touched=["main.py", "requirements-core.txt"],
        failure_reason=classify_failure("ModuleNotFoundError: No module named 'requests'"),
        accepted=False,
        suggested_route="route_to_dependency_fix",
    )

    record_experiment_scorecard(scorecard, actor="test", experiment_type="sub_swarm", source="swarm_a")
    record_experiment_scorecard(scorecard, actor="test", experiment_type="sub_swarm", source="swarm_b")

    stored = patch_memory._load_lessons()
    lessons = search_patch_lessons(
        failure_class="dependency_missing",
        action_type="sub_swarm",
        file_path="requirements-core.txt",
    )

    assert len(stored) == 1
    assert lessons[0]["occurrence_count"] == 2
    assert lessons[0]["times_reused"] == 0


def test_action_executor_injects_patch_memory_into_modification_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(patch_memory, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("PATCH_MEMORY_ALLOW_PYTEST", "1")

    add_patch_lesson(
        problem="Import path failed.",
        failure_class="import_path_issue",
        fix="Repair module boundaries first.",
        rule="Do not rewrite behavior for import failures.",
        applies_to=["PROPOSE_TOOL_MODIFICATION"],
        metadata={"files_touched": ["ai_assistant/custom_tools/example.py"]},
    )

    executor = ActionExecutor(learning_agent=MagicMock())
    executor._run_execution_policy_preflight = MagicMock(return_value={
        "checked": True,
        "allowed": True,
        "blocked": False,
        "reasons": [],
    })
    executor.code_service.modify_code = AsyncMock(return_value={
        "status": "FAILED",
        "error": "generation stopped for test",
    })

    result = asyncio.run(executor.execute_action({
        "source_insight_id": "insight_patch",
        "action_type": "PROPOSE_TOOL_MODIFICATION",
        "details": {
            "tool_name": "example",
            "module_path": "ai_assistant/custom_tools/example.py",
            "function_name": "example",
            "suggested_change_description": "Fix import failure.",
            "failure_class": "import_path_issue",
        },
    }))

    assert result is False
    prompt = executor.code_service.modify_code.call_args.kwargs["modification_instruction"]
    assert "PATCH MEMORY" in prompt
    assert "Do not rewrite behavior for import failures" in prompt


@pytest.mark.asyncio
async def test_coder_agent_injects_patch_memory_into_repair_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(patch_memory, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("PATCH_MEMORY_ALLOW_PYTEST", "1")

    add_patch_lesson(
        problem="Dependency was missing.",
        failure_class="dependency_missing",
        fix="Update requirements-core.txt.",
        rule="Every new external import must be declared.",
        applies_to=["sub_swarm"],
        metadata={"files_touched": ["requirements-core.txt"]},
    )

    contract = SwarmContract(
        task_id="patch_swarm",
        description="Patch memory prompt test",
        deliverables=["main.py", "requirements-core.txt"],
    )
    provider = RecordingProvider("requests")
    blackboard = Blackboard("patch_memory_test")
    coder = CoderAgent("coder", contract, blackboard, provider)
    coder.drafts["main.py"] = "old"
    coder.start_working()
    coder.wait_for_tests()
    classification = classify_failure("ModuleNotFoundError: No module named 'requests'")

    await coder.handle_test_failure(BlackboardEvent(
        topic="test_results_failed",
        source_agent="tester",
        data={
            "filename": "main.py",
            "logs": "ModuleNotFoundError: No module named 'requests'",
            "failure_classification": classification.model_dump(),
        },
    ))

    assert provider.prompts
    assert "Prior repair lessons" in provider.prompts[-1]
    assert "Every new external import must be declared" in provider.prompts[-1]
    assert coder.drafts["requirements-core.txt"] == "requests"


def test_patch_memory_endpoint_returns_lessons(monkeypatch, tmp_path):
    monkeypatch.setattr(patch_memory, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("PATCH_MEMORY_ALLOW_PYTEST", "1")
    add_patch_lesson(
        problem="Capability violation.",
        failure_class="capability_violation",
        fix="Route to capability policy review.",
        rule="Do not retry code generation for capability violations.",
        applies_to=["sub_swarm"],
    )

    import sys
    import types
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
    from routes import api_bp  # noqa: PLC0415

    app_globals.orchestrator = MagicMock(get_blocked_tools=lambda: [])
    app = Flask(__name__)
    app.register_blueprint(api_bp)

    with app.test_client() as client:
        response = client.get("/api/system/patch-memory?failure_class=capability_violation&limit=3")

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["lessons"][0]["failure_class"] == "capability_violation"
