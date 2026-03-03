from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.core import project_manager
from ai_assistant.core import suggestion_manager
from ai_assistant.config import is_debug_mode, AUTONOMOUS_LEARNING_ENABLED # Assuming active_tasks is managed in CLI

def get_tools_status() -> str:
    """Returns a summary string of tool status."""
    tools = tool_system_instance.list_tools()
    if not tools:
        return "No tools registered."
    
    tool_types: dict[str, int] = {}
    for tool_info_str in tools.values(): # list_tools returns Dict[str, str] where value is description
        # We need to get the full tool info to get the type
        # This is a bit inefficient, might need a list_tools_with_details in ToolSystem
        # For now, let's assume a basic count or improve ToolSystem later.
        # As a placeholder for type, we'll just count total.
        pass # Cannot easily get type from current list_tools() output

    # Let's get detailed tool info for type counting
    detailed_tools = tool_system_instance._tool_registry # Accessing protected member for detailed info
    
    for tool_data in detailed_tools.values():
        tool_type = tool_data.get('type', 'Unknown')
        tool_types[tool_type] = tool_types.get(tool_type, 0) + 1

    summary_lines = [f"Total Tools Registered: {len(detailed_tools)}"]
    if tool_types:
        for t_type, count in tool_types.items():
            summary_lines.append(f"  - Type '{t_type}': {count}")
    else:
        summary_lines.append("  No type information available for tools.")
        
    return "\n".join(summary_lines)

def get_projects_status() -> str:
    """Returns a summary string of project status from the project manager."""
    return project_manager.get_all_projects_summary_status()

def get_suggestions_status() -> str:
    """Returns a summary string of suggestion status from the suggestion manager."""
    return suggestion_manager.get_suggestions_summary_status()

def get_system_status(active_tasks_count: int) -> str:
    """Returns a summary string of overall system status."""
    status_lines = [
        f"Background Tasks: {active_tasks_count}",
        f"Debug Mode: {'Enabled' if is_debug_mode() else 'Disabled'}",
        f"Autonomous Learning: {'Enabled' if AUTONOMOUS_LEARNING_ENABLED else 'Disabled'}"
    ]
    return "\n".join(status_lines)

def get_all_status_info(active_tasks_count: int) -> str:
    """Returns a comprehensive status report."""
    report = [
        "--- Tools Status ---",
        get_tools_status(),
        "\n--- Projects Status ---",
        get_projects_status(),
        "\n--- Suggestions Status ---",
        get_suggestions_status(),
        "\n--- System Status ---",
        get_system_status(active_tasks_count)
    ]
    return "\n".join(report)
