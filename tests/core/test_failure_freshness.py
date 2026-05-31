import datetime
from unittest.mock import patch

from ai_assistant.core.failure_freshness import (
    FAILURE_RUNTIME_REVISION_KEY,
    SUPERSEDED_FAILURE_STATUS,
    annotate_failure_metadata,
    is_failure_stale,
)
from ai_assistant.core.reflection import ActionableInsight, InsightType, ReflectionLogEntry
from ai_assistant.learning.learning import LearningAgent


def test_annotated_failure_becomes_stale_after_runtime_update():
    observed = datetime.datetime.fromtimestamp(100.0, tz=datetime.timezone.utc).isoformat()
    with patch("ai_assistant.core.failure_freshness.get_runtime_revision_timestamp", return_value=90.0):
        metadata = annotate_failure_metadata({}, observed)

    assert metadata[FAILURE_RUNTIME_REVISION_KEY] == 90.0
    assert is_failure_stale(metadata, current_runtime_revision=101.0)
    assert not is_failure_stale(metadata, current_runtime_revision=90.0)


def test_old_failure_without_snapshot_uses_observed_timestamp():
    metadata = {"timestamp": datetime.datetime.fromtimestamp(100.0, tz=datetime.timezone.utc).isoformat()}
    assert is_failure_stale(metadata, metadata["timestamp"], current_runtime_revision=101.0)


def test_learning_agent_marks_stale_bug_insight_as_superseded():
    agent = LearningAgent.__new__(LearningAgent)
    insight = ActionableInsight(
        type=InsightType.TOOL_BUG_SUSPECTED,
        description="Historical failure",
        source_reflection_entry_ids=["reflection-1"],
        metadata={
            FAILURE_RUNTIME_REVISION_KEY: 100.0,
            "failure_observed_at": datetime.datetime.fromtimestamp(
                100.0, tz=datetime.timezone.utc
            ).isoformat(),
        },
    )
    agent.insights = [insight]
    saved = []
    agent._save_insights = lambda: saved.append(True)

    with patch("ai_assistant.core.failure_freshness.get_runtime_revision_timestamp", return_value=101.0):
        count = agent._supersede_stale_failure_insights()

    assert count == 1
    assert insight.status == SUPERSEDED_FAILURE_STATUS
    assert saved == [True]


def test_reflection_entry_failure_revision_round_trips():
    entry = ReflectionLogEntry(
        goal_description="Historical task",
        plan=[],
        execution_results=[],
        status="FAILURE",
        runtime_revision_at_failure=123.0,
    )

    restored = ReflectionLogEntry.from_serializable_dict(entry.to_serializable_dict())

    assert restored.runtime_revision_at_failure == 123.0
