from dataclasses import asdict, dataclass
from typing import Dict, Any, Tuple, Optional, Iterable, List

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
    delegation_active_tasks: int
    delegation_chat_delegate_active_tasks: int
    delegation_by_worker: Dict[str, int]
    delegation_by_scope: Dict[str, int]
    delegation_by_state: Dict[str, int]
    delegation_topology: List[Dict[str, Any]]


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




def _get_task_value(task: Any, field: str, default: Any = None) -> Any:
    """Read task fields from either object attributes or dict keys."""
    if isinstance(task, dict):
        return task.get(field, default)
    return getattr(task, field, default)


def _summarize_delegation(active_tasks: Optional[Iterable[Any]]) -> Tuple[int, int, Dict[str, int], Dict[str, int], Dict[str, int], List[Dict[str, Any]]]:
    if not active_tasks:
        return 0, 0, {}, {}, {}, []

    delegated_total = 0
    delegated_chat = 0
    by_worker: Dict[str, int] = {}
    by_scope: Dict[str, int] = {}
    by_state: Dict[str, int] = {}
    topology: List[Dict[str, Any]] = []

    for task in active_tasks:
        task_type = _get_task_value(task, "task_type")
        details = _get_task_value(task, "details") or {}
        task_status = _get_task_value(task, "status")

        if isinstance(task_type, dict):
            task_type_name = task_type.get("name")
        elif hasattr(task_type, "name"):
            task_type_name = task_type.name
        else:
            task_type_name = str(task_type)

        if task_type_name == "EPHEMERAL_AGENT_TASK":
            delegated_total += 1
            if isinstance(details, dict) and details.get("source") == "chat_delegate":
                delegated_chat += 1

            worker = details.get("worker_profile", "coder_worker") if isinstance(details, dict) else "coder_worker"
            scope = details.get("scope_type", "session") if isinstance(details, dict) else "session"

            if hasattr(task_status, "name"):
                state = task_status.name
            else:
                state = str(task_status or "UNKNOWN")

            by_worker[worker] = by_worker.get(worker, 0) + 1
            by_scope[scope] = by_scope.get(scope, 0) + 1
            by_state[state] = by_state.get(state, 0) + 1
            capability_profile = details.get("capability_profile", "unknown") if isinstance(details, dict) else "unknown"
            retention_policy = details.get("retention_policy", "unknown") if isinstance(details, dict) else "unknown"

            topology.append({
                "task_id": _get_task_value(task, "task_id", ""),
                "worker_profile": worker,
                "scope_type": scope,
                "capability_profile": capability_profile,
                "retention_policy": retention_policy,
                "state": state,
                "source": details.get("source", "unknown") if isinstance(details, dict) else "unknown",
                "origin_session_id": _get_task_value(task, "session_id", ""),
                "description": _get_task_value(task, "description", ""),
            })

    return delegated_total, delegated_chat, dict(sorted(by_worker.items())), dict(sorted(by_scope.items())), dict(sorted(by_state.items())), topology

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


def get_status_snapshot(active_tasks_count: int, active_tasks: Optional[Iterable[Any]] = None) -> Dict[str, Any]:
    """Return structured status data for UI/API use."""
    detailed_tools = _get_tool_registry_details()
    tools_total, tool_types = _summarize_tools(detailed_tools)

    delegated_total, delegated_chat, by_worker, by_scope, by_state, topology = _summarize_delegation(active_tasks)

    snapshot = StatusSnapshot(
        tools_total=tools_total,
        tools_by_type=tool_types,
        projects_summary=get_projects_status(),
        suggestions_summary=get_suggestions_status(),
        background_tasks=active_tasks_count,
        debug_mode_enabled=is_debug_mode(),
        autonomous_learning_enabled=AUTONOMOUS_LEARNING_ENABLED,
        delegation_active_tasks=delegated_total,
        delegation_chat_delegate_active_tasks=delegated_chat,
        delegation_by_worker=by_worker,
        delegation_by_scope=by_scope,
        delegation_by_state=by_state,
        delegation_topology=topology,
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
