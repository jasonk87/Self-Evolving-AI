from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

import app_globals
from ai_assistant.core.task_manager import ActiveTaskStatus

if TYPE_CHECKING:
    from ai_assistant.core.task_manager import ActiveTask


_FAILURE_STATUS_NAMES = {
    "FAILED_PRE_REVIEW",
    "FAILED_DURING_APPLY",
    "FAILED_UNKNOWN",
    "CRITIC_REVIEW_REJECTED",
    "POST_MOD_TEST_FAILED",
    "FAILED_CODE_GENERATION",
    "FAILED_INTERRUPTED",
    "PROJECT_PLAN_FAILED_STEP",
}


def should_send_failure_alert(status_name: str) -> bool:
    return status_name in _FAILURE_STATUS_NAMES


def _suggested_actions(task: "ActiveTask") -> List[str]:
    return [
        f"Retry with fallback (`/task-action {task.task_id} retry`).",
        f"Summarize failure clues (`/task-action {task.task_id} summarize`).",
        f"Pause autonomous retries (`/task-action {task.task_id} pause`).",
    ]


def build_failure_alert_message(task: "ActiveTask") -> str:
    reason = task.status_reason or "No detailed reason was captured."
    step = task.current_step_description or "Unknown step"
    options = _suggested_actions(task)
    return (
        f"⚠️ Heads up — a background mission hit an issue in my system.\n\n"
        f"Mission: {task.description}\n"
        f"Task ID: {task.task_id}\n"
        f"Status: {task.status.name}\n"
        f"Last step: {step}\n"
        f"Reason: {reason}\n\n"
        f"Recommendation: Retry with fallback first (usually fastest recovery).\n"
        f"Would you like me to do that now?\n\n"
        f"Other options:\n"
        f"1) {options[0]}\n"
        f"2) {options[1]}\n"
        f"3) {options[2]}"
    )


def _resolve_session_id(task: "ActiveTask") -> str:
    if task.session_id:
        return task.session_id

    if app_globals.chat_manager:
        sessions = app_globals.chat_manager.list_sessions()
        if sessions:
            return sessions[0]["id"]
        return app_globals.chat_manager.create_session("System Alerts")

    return "system_alert_session"




def execute_alert_action(task_id: str, action: str) -> Dict[str, Any]:
    if not app_globals.orchestrator or not app_globals.orchestrator.task_manager:
        return {"success": False, "error": "Task manager unavailable."}

    tm = app_globals.orchestrator.task_manager
    task = tm.get_task_including_archive(task_id)
    if not task:
        return {"success": False, "error": f"Task {task_id} not found."}

    normalized = (action or "").strip().lower()

    if normalized == "retry":
        retry_task = tm.add_task(
            description=f"Retry: {task.description}",
            task_type=task.task_type,
            related_item_id=task.related_item_id,
            details=dict(task.details or {}),
            session_id=task.session_id,
        )
        tm.update_task_status(
            retry_task.task_id,
            ActiveTaskStatus.PLANNING,
            reason=f"Retry requested for failed task {task.task_id}",
            step_desc="Retry requested by user via conversational alert",
        )
        return {
            "success": True,
            "message": f"Started retry task {retry_task.task_id} for original task {task.task_id}.",
            "new_task_id": retry_task.task_id,
        }

    if normalized == "summarize":
        summary = build_failure_alert_message(task)
        return {"success": True, "message": summary}

    if normalized == "pause":
        if not isinstance(task.details, dict):
            task.details = {}
        task.details["autonomous_retry_paused"] = True
        tm._save_active_tasks()
        return {
            "success": True,
            "message": f"Autonomous retries paused for task {task.task_id}. You can handle it manually or ask me to retry later.",
        }

    return {"success": False, "error": f"Unknown action '{action}'. Supported: retry, summarize, pause."}

def emit_task_failure_alert(task: "ActiveTask") -> Optional[Dict[str, Any]]:
    if not should_send_failure_alert(task.status.name):
        return None

    message = build_failure_alert_message(task)
    session_id = _resolve_session_id(task)

    if app_globals.chat_manager:
        app_globals.chat_manager.add_message(session_id, "assistant", message)

    payload = {
        "session_id": session_id,
        "task_id": task.task_id,
        "message": message,
        "status": task.status.name,
        "suggested_actions": _suggested_actions(task),
    }

    if app_globals.socketio:
        app_globals.socketio.emit("assistant_alert", payload)

    return payload
