import pytest


@pytest.mark.integration
def test_cli_equivalents_smoke():
    """Basic smoke check for key CLI-equivalent service entry points."""
    try:
        from ai_assistant.tools.tool_system import tool_system_instance
        from ai_assistant.core import project_manager
        from ai_assistant.core import suggestion_manager
        from ai_assistant.custom_tools import awareness_tools
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"CLI equivalent imports unavailable: {exc}")

    tools = tool_system_instance.list_tools()
    assert isinstance(tools, dict)

    projects = project_manager.list_projects()
    assert isinstance(projects, list)

    status = suggestion_manager.get_suggestions_summary_status()
    assert isinstance(status, str)

    try:
        pending = awareness_tools.list_formatted_suggestions(status_filter="pending")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Suggestion integration unavailable in environment: {exc}")
    assert isinstance(pending, list)
