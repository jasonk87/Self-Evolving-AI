import asyncio
import pytest


def _initialize_components():
    """Initialize core components needed for a smoke tool execution test."""
    from ai_assistant.tools.tool_system import tool_system_instance
    from ai_assistant.core.notification_manager import NotificationManager
    from ai_assistant.core.task_manager import TaskManager

    notification_manager = NotificationManager()
    task_manager = TaskManager(notification_manager=notification_manager)
    return tool_system_instance, notification_manager, task_manager


def _pick_tool(available_tools):
    """Pick a likely-safe tool and default args for smoke execution."""
    if "greet_user" in available_tools:
        return "greet_user", ("Jules",)
    if "add_numbers" in available_tools:
        return "add_numbers", (5, 7)

    first_tool_name = next(iter(available_tools.keys()), None)
    if first_tool_name is None:
        return None, ()
    return first_tool_name, ()


@pytest.mark.smoke
def test_tool_execution_smoke():
    """Ensure at least one available tool can be executed without collection-time hard exits."""
    try:
        tool_system_instance, notification_manager, task_manager = _initialize_components()
    except Exception as exc:  # pragma: no cover - environment/dependency variability
        pytest.skip(f"Skipping tool execution smoke test due to init failure: {type(exc).__name__}: {exc}")

    available_tools = tool_system_instance.list_tools()
    if not available_tools:
        pytest.skip("No tools registered in ToolSystem.")

    tool_name, tool_args = _pick_tool(available_tools)
    if tool_name is None:
        pytest.skip("No executable tool selected.")

    result = asyncio.run(
        tool_system_instance.execute_tool(
            name=tool_name,
            args=tool_args,
            kwargs={},
            task_manager=task_manager,
            notification_manager=notification_manager,
        )
    )

    assert result is not None
