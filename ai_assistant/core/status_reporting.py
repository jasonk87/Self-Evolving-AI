from dataclasses import asdict, dataclass
from typing import Dict, Any, Tuple

from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.core import project_manager
from ai_assistant.core import suggestion_manager
from ai_assistant.config import is_debug_mode, AUTONOMOUS_LEARNING_ENABLED


@dataclass
class StatusSnapshot:
    tools_total: int
    tools_by_type: Dict[str, int]
    projects_summary: str
    suggestions_summary: str
    background_tasks: int
    debug_mode_enabled: bool
    autonomous_learning_enabled: bool


def _get_tool_registry_details() -> Dict[str, Dict[str, Any]]:
    """Best-effort retrieval of detailed tool metadata.

    Prefers a public API when available, and only falls back to protected internals.
    """
    if hasattr(tool_system_instance, "list_tools_with_details"):
        try:
            detailed = tool_system_instance.list_tools_with_details()
            if isinstance(detailed, dict):
                return detailed
        except Exception:
            pass

    return getattr(tool_system_instance, "_tool_registry", {}) or {}


def _summarize_tools(detailed_tools: Dict[str, Dict[str, Any]]) -> Tuple[int, Dict[str, int]]:
    """Return tool totals and counts-by-type from a detailed tool registry."""
    if not detailed_tools:
        return 0, {}

    tool_types: Dict[str, int] = {}
    for tool_data in detailed_tools.values():
        tool_type = tool_data.get("type", "Unknown") if isinstance(tool_data, dict) else "Unknown"
        tool_types[tool_type] = tool_types.get(tool_type, 0) + 1

    return len(detailed_tools), dict(sorted(tool_types.items()))


def get_tools_status() -> str:
    """Returns a summary string of tool status."""
    detailed_tools = _get_tool_registry_details()
    tools_total, tool_types = _summarize_tools(detailed_tools)
    if not tools_total:
        return "No tools registered."

    summary_lines = [f"Total Tools Registered: {tools_total}"]
    for t_type, count in sorted(tool_types.items()):
        summary_lines.append(f"  - Type '{t_type}': {count}")

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
        f"Autonomous Learning: {'Enabled' if AUTONOMOUS_LEARNING_ENABLED else 'Disabled'}",
    ]
    return "\n".join(status_lines)


def get_status_snapshot(active_tasks_count: int) -> Dict[str, Any]:
    """Return structured status data for UI/API use."""
    detailed_tools = _get_tool_registry_details()
    tools_total, tool_types = _summarize_tools(detailed_tools)

    snapshot = StatusSnapshot(
        tools_total=tools_total,
        tools_by_type=tool_types,
        projects_summary=get_projects_status(),
        suggestions_summary=get_suggestions_status(),
        background_tasks=active_tasks_count,
        debug_mode_enabled=is_debug_mode(),
        autonomous_learning_enabled=AUTONOMOUS_LEARNING_ENABLED,
    )
    return asdict(snapshot)


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
        get_system_status(active_tasks_count),
    ]
    return "\n".join(report)
