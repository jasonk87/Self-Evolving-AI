import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from ai_assistant.core import background_service
from ai_assistant.core.reflection import InsightType
from ai_assistant.llm_interface.deepseek_client import DeepseekError


def test_provider_failure_is_not_classified_as_tool_bug():
    notification_manager = SimpleNamespace(add_notification=MagicMock())
    learning_agent = SimpleNamespace(
        insights=[],
        _save_insights=MagicMock(),
        notification_manager=notification_manager,
    )
    error = DeepseekError("Deepseek request timed out after 60 seconds (3 attempts).")

    insight = background_service._record_dream_mode_failure(
        learning_agent,
        "search_web",
        error,
    )

    assert insight.type == InsightType.DREAM_EXPERIMENT
    assert insight.type != InsightType.TOOL_BUG_SUSPECTED
    assert insight.status == "ACTION_FAILED"
    assert insight.related_tool_name == "search_web"
    assert insight.metadata["failure_scope"] == "provider"
    assert insight.metadata["repairable_tool_failure"] is False
    assert learning_agent.insights == [insight]
    learning_agent._save_insights.assert_called_once()
    notification_manager.add_notification.assert_called_once()


def test_runner_failure_preserves_actual_selected_tool():
    learning_agent = SimpleNamespace(
        insights=[],
        _save_insights=MagicMock(),
        notification_manager=None,
    )

    insight = background_service._record_dream_mode_failure(
        learning_agent,
        "read_text_from_file",
        ValueError("bad generated scenario"),
    )

    assert insight.related_tool_name == "read_text_from_file"
    assert insight.metadata["failure_scope"] == "dream_runner"
    assert insight.metadata["selected_tool"] == "read_text_from_file"
    assert "dream_mode_runner" not in insight.description


def test_empty_exception_text_still_has_useful_details():
    assert background_service._format_exception_details(asyncio.TimeoutError()) == "TimeoutError"
