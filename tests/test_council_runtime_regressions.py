import json

import pytest

from ai_assistant.core.reflection import ActionableInsight, InsightType
from ai_assistant.learning.conversation_analyst import ConversationalAnalyst
from ai_assistant.learning.learning import LearningAgent
from ai_assistant.tools.tool_system import list_tools


def test_learning_agent_add_insight_persists_without_duplicate(tmp_path):
    insights_path = tmp_path / "insights.json"
    agent = LearningAgent(insights_filepath=str(insights_path))
    insight = ActionableInsight(
        type=InsightType.TOOL_BUG_SUSPECTED,
        description="Tool 'google_search' was quarantined.",
        source_reflection_entry_ids=[],
        related_tool_name="google_search",
    )

    assert agent.add_insight(insight) is True
    assert agent.add_insight(insight) is False

    data = json.loads(insights_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["related_tool_name"] == "google_search"


def test_conversational_analyst_extracts_json_from_chatter():
    response = """
    Here is the analysis:
    ```json
    {"insights": [{"type": "USER_FRUSTRATION", "description": "User was blocked.", "evidence": "failed", "suggestion": "Fix fallback."}]}
    ```
    Thanks.
    """

    parsed = ConversationalAnalyst()._extract_json_object(response)

    assert parsed["insights"][0]["type"] == "USER_FRUSTRATION"


@pytest.mark.parametrize("alias", ["search_web", "web_search", "google_search", "news_search"])
def test_search_aliases_are_registered(alias):
    assert alias in list_tools()
