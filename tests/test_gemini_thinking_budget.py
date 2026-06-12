import ai_assistant.config as config_module
from ai_assistant.core.config_manager import ConfigManager
from ai_assistant.core.telemetry import TokenUsageTracker
from ai_assistant.llm_interface import gemini_client


def test_gemini_25_payload_gets_thinking_budget():
    expected_budget = getattr(config_module, "GEMINI_THINKING_BUDGET", 24576)
    payload = {"generationConfig": {"temperature": 0.2}}

    budget = gemini_client._apply_thinking_config(payload, "gemini-2.5-flash-lite")

    assert budget == expected_budget
    assert payload["generationConfig"]["thinkingConfig"] == {
        "thinkingBudget": expected_budget
    }


def test_non_25_model_does_not_get_thinking_budget(monkeypatch):
    monkeypatch.setattr(config_module, "GEMINI_THINKING_BUDGET", 2048)
    payload = {"generationConfig": {"temperature": 0.2}}

    budget = gemini_client._apply_thinking_config(payload, "gemini-3.5-flash")

    assert budget is None
    assert "thinkingConfig" not in payload["generationConfig"]


def test_config_manager_exposes_thinking_budget():
    manager = ConfigManager()

    assert "GEMINI_THINKING_BUDGET" in manager.get_all_settings()
    assert manager.get_settings_schema()["GEMINI_THINKING_BUDGET"]["type"] == "integer"
    assert manager.coerce_setting_value("GEMINI_THINKING_BUDGET", "512") == 512
    assert manager.coerce_setting_value("GEMINI_THINKING_BUDGET", "-1") == -1

    try:
        manager.coerce_setting_value("GEMINI_THINKING_BUDGET", "128")
        assert False, "Expected ValueError for invalid Flash-Lite thinking budget"
    except ValueError:
        assert True


def test_telemetry_tracks_thinking_tokens():
    TokenUsageTracker._total_input_tokens = 0
    TokenUsageTracker._total_output_tokens = 0
    TokenUsageTracker._total_thinking_tokens = 0
    TokenUsageTracker._total_calls = 0
    TokenUsageTracker._history = []

    TokenUsageTracker.track_call(
        "gemini-2.5-flash-lite",
        prompt_len=400,
        response_len=200,
        task="gemini_async_chat",
        usage_metadata={
            "promptTokenCount": 10,
            "candidatesTokenCount": 20,
            "thoughtsTokenCount": 512,
        },
        thinking_budget=2048,
    )

    usage = TokenUsageTracker.get_usage()

    assert usage["total_input_tokens"] == 10
    assert usage["total_output_tokens"] == 20
    assert usage["total_thinking_tokens"] == 512
    assert usage["total_tokens"] == 542
    model_usage = usage["model_usage"]["gemini-2.5-flash-lite"]
    assert model_usage["thinking_tokens"] == 512
    assert model_usage["latest_thinking_budget"] == 2048
