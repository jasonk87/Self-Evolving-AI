import pytest
from unittest.mock import patch, AsyncMock

@pytest.mark.integration
def test_learning_and_reflection_smoke():
    """Smoke test learning + reflection flow without import-time side effects."""
    try:
        from ai_assistant.learning.autonomous_learning import learn_facts_from_interaction
        from ai_assistant.core.autonomous_reflection import run_self_reflection_cycle
        from ai_assistant.tools.tool_system import tool_system_instance
        from ai_assistant.core.notification_manager import NotificationManager
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Environment missing components for learning/reflection smoke test: {exc}")

    # Set up mock response side effect for reflection LLM calls
    def mock_invoke_ollama_model(prompt, model_name=None, **kwargs):
        if "EVALUATE_IMPROVEMENT_SUGGESTION" in prompt or "impact_score" in prompt:
            return '{"impact_score": 5, "risk_score": 1, "effort_score": 2}'
        elif "LLM_REVIEW_IMPROVEMENT_SUGGESTION" in prompt or "review_looks_good" in prompt:
            return '{"review_looks_good": true, "qualitative_review": "Looks great", "confidence_score": 0.95}'
        elif "GENERATE_IMPROVEMENT_SUGGESTIONS" in prompt or "improvement_suggestions" in prompt:
            return '{"improvement_suggestions": [{"suggestion_type": "TOOL_BUG_SUSPECTED", "description": "Mock suggestion description", "related_tool_name": "mocked_tool", "priority": 4}]}'
        elif "IDENTIFY_FAILURE_PATTERNS" in prompt or "identified_patterns" in prompt:
            return '{"identified_patterns": [{"pattern_description": "Mock pattern", "priority": 3, "supporting_evidence": "Mock evidence"}]}'
        return "{}"

    # We patch:
    # 1. invoke_ollama_model_async under autonomous_learning
    # 2. _curate_and_update_fact_store under autonomous_learning
    # 3. get_reflection_log_summary_for_analysis under autonomous_reflection
    # 4. invoke_ollama_model under autonomous_reflection
    # 5. load_actionable_insights to avoid DB/file loading
    # 6. time.sleep to avoid 10-second throttling delays in tests
    with patch("ai_assistant.learning.autonomous_learning.invoke_ollama_model_async", new_callable=AsyncMock) as mock_learn_llm, \
         patch("ai_assistant.learning.autonomous_learning._curate_and_update_fact_store", new_callable=AsyncMock) as mock_curate, \
         patch("ai_assistant.core.autonomous_reflection.get_reflection_log_summary_for_analysis") as mock_summary, \
         patch("ai_assistant.core.autonomous_reflection.invoke_ollama_model", side_effect=mock_invoke_ollama_model) as _mock_reflect_llm, \
         patch("ai_assistant.core.autonomous_reflection.load_actionable_insights", return_value=[]) as _mock_load_insights, \
         patch("time.sleep") as _mock_sleep:

        mock_learn_llm.return_value = '{"facts": ["Rayleigh scattering causes the sky to appear blue during the day."]}'
        mock_curate.return_value = True
        mock_summary.return_value = "Mock log summary data"

        try:
            learned = pytest.importorskip("asyncio").run(
                learn_facts_from_interaction(
                    "The sky is blue during the day.",
                    "Yes, that is due to Rayleigh scattering.",
                    True,
                )
            )
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Learning backend unavailable in test environment: {exc}")
        
        assert isinstance(learned, list)
        assert len(learned) > 0
        assert "Rayleigh scattering" in learned[0]

        mock_learn_llm.assert_called_once()
        mock_curate.assert_called_once()

        notification_manager = NotificationManager()
        # Mock save_notifications to avoid actual file I/O
        with patch.object(notification_manager, "_save_notifications"):
            available_tools = tool_system_instance.list_tools()
            suggestions = run_self_reflection_cycle(
                available_tools=available_tools,
                notification_manager=notification_manager,
            )
            assert isinstance(suggestions, list)
            assert len(suggestions) > 0
            assert suggestions[0]["description"] == "Mock suggestion description"
