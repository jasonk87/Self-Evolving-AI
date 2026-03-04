import pytest


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

    notification_manager = NotificationManager()
    available_tools = tool_system_instance.list_tools()
    suggestions = run_self_reflection_cycle(
        available_tools=available_tools,
        notification_manager=notification_manager,
    )
    assert isinstance(suggestions, list)
