
from flask import request, jsonify, Response
from . import api_bp
import logging
import os
import sys
import subprocess
import threading
import app_globals
import ai_assistant.config as config
from ai_assistant.core.approval_manager import approval_manager
from ai_assistant.core.project_manager import find_project
from ai_assistant.core.action_audit_ledger import get_recent_action_audit_events
from ai_assistant.core.experiment_scoreboard import get_recent_experiment_scorecards
from ai_assistant.core.patch_memory import get_recent_patch_lessons, search_patch_lessons
from ai_assistant.core.tool_lifecycle import list_tool_lifecycle_records
from ai_assistant.core.background_service import report_user_activity
from ai_assistant.core.shutdown_manager import shutdown_manager
from ai_assistant.voice.tts import generate_speech

logger = logging.getLogger(__name__)


def _coerce_limit(raw_value, default: int, maximum: int) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _safe_task_dict(task) -> dict:
    try:
        return task.to_dict()
    except Exception:  # pragma: no cover
        return {}


def _pending_approval_summaries(limit: int) -> list[dict]:
    items: list[dict] = []
    try:
        for req in approval_manager.get_pending_requests()[:limit]:
            items.append({
                "id": req.get("id"),
                "type": req.get("type"),
                "source": "approval_manager",
                "description": req.get("description") or req.get("message") or "",
                "created_at": req.get("created_at"),
                "status": "PENDING",
            })
    except Exception as exc:  # pragma: no cover
        logger.warning("Run spine could not read approval manager requests: %s", exc)

    learning_agent = getattr(getattr(app_globals, "orchestrator", None), "learning_agent", None)
    for insight in list(getattr(learning_agent, "insights", []) or []):
        status = str(getattr(insight, "status", "") or "")
        if status not in {"NEW", "SELF_HEALING_PROPOSED", "APPROVED_BY_USER", "APPROVED_QUEUED"}:
            continue
        insight_type = getattr(getattr(insight, "type", None), "name", None) or str(getattr(insight, "type", "UNKNOWN"))
        items.append({
            "id": getattr(insight, "insight_id", None),
            "type": insight_type,
            "source": "learning_agent",
            "description": getattr(insight, "description", "") or "",
            "created_at": getattr(insight, "creation_timestamp", None),
            "status": status,
            "related_tool_name": getattr(insight, "related_tool_name", None),
            "approval_task_id": (getattr(insight, "metadata", {}) or {}).get("approval_task_id"),
        })
        if len(items) >= limit:
            break
    return items[:limit]


def build_run_spine_snapshot(limit: int = 12) -> dict:
    """Join the main autonomous-work evidence streams into one compact snapshot."""
    bounded_limit = max(1, min(int(limit), 50))
    task_manager = getattr(getattr(app_globals, "orchestrator", None), "task_manager", None) or getattr(app_globals, "task_manager", None)
    active_tasks = []
    if task_manager:
        active_tasks = [_safe_task_dict(task) for task in task_manager.list_active_tasks()]
        active_tasks = [task for task in active_tasks if task]

    audit_events = get_recent_action_audit_events(limit=bounded_limit * 3)
    scorecards = get_recent_experiment_scorecards(limit=bounded_limit)
    approvals = _pending_approval_summaries(limit=bounded_limit)
    lessons = get_recent_patch_lessons(limit=min(bounded_limit, 10))
    lifecycle_records = list_tool_lifecycle_records(limit=min(bounded_limit, 10))

    blocked_scorecards = [
        record for record in scorecards
        if isinstance(record.get("scorecard"), dict) and record["scorecard"].get("blocked")
    ]
    failed_tasks = [
        task for task in active_tasks
        if "FAIL" in str(task.get("status") or "") or str(task.get("status") or "") == "USER_CANCELLED"
    ]
    queued_approvals = [
        item for item in approvals
        if str(item.get("status") or "") == "APPROVED_QUEUED"
    ]

    work_items: list[dict] = []
    for task in active_tasks[:bounded_limit]:
        task_id = task.get("task_id")
        related_id = task.get("related_item_id")
        related_events = [
            event for event in audit_events
            if event.get("task_id") == task_id or (related_id and event.get("source") == related_id)
        ][:5]
        related_scorecards = [
            record for record in scorecards
            if (record.get("scorecard") or {}).get("task_id") == task_id
            or record.get("source") == task_id
            or (related_id and record.get("source") == related_id)
        ][:3]
        related_approvals = [
            item for item in approvals
            if item.get("id") == related_id or item.get("approval_task_id") == task_id
        ][:3]
        work_items.append({
            "kind": "task",
            "id": task_id,
            "title": task.get("description") or task_id,
            "status": task.get("status"),
            "task_type": task.get("task_type"),
            "related_item_id": related_id,
            "current_step": task.get("current_step_description"),
            "updated_at": task.get("last_updated_at") or task.get("created_at"),
            "audit_events": related_events,
            "scorecards": related_scorecards,
            "approvals": related_approvals,
        })

    if not work_items:
        for record in scorecards[:min(5, bounded_limit)]:
            scorecard = record.get("scorecard") or {}
            work_items.append({
                "kind": "scorecard",
                "id": scorecard.get("task_id") or record.get("record_id"),
                "title": record.get("experiment_type") or "experiment",
                "status": "accepted" if scorecard.get("accepted") else "blocked" if scorecard.get("blocked") else "rejected",
                "updated_at": record.get("timestamp"),
                "scorecards": [record],
                "audit_events": [],
                "approvals": [],
            })

    return {
        "schema_version": 1,
        "counts": {
            "active_tasks": len(active_tasks),
            "pending_approvals": len(approvals),
            "queued_approvals": len(queued_approvals),
            "audit_events": len(audit_events),
            "scorecards": len(scorecards),
            "blocked_scorecards": len(blocked_scorecards),
            "patch_lessons": len(lessons),
            "tool_lifecycle_records": len(lifecycle_records),
            "failed_active_tasks": len(failed_tasks),
        },
        "work_items": work_items[:bounded_limit],
        "active_tasks": active_tasks[:bounded_limit],
        "approvals": approvals[:bounded_limit],
        "audit_events": audit_events[:bounded_limit],
        "scorecards": scorecards[:bounded_limit],
        "patch_lessons": lessons[:bounded_limit],
        "tool_lifecycle": lifecycle_records[:bounded_limit],
    }

@api_bp.route('/config', methods=['GET'])
def get_config():
    """Returns the current system configuration."""
    return jsonify(app_globals.config_manager.get_all_settings())

@api_bp.route('/config/schema', methods=['GET'])
def get_config_schema():
    """Returns metadata for editable configuration settings."""
    return jsonify({
        "success": True,
        "schema_version": 1,
        "settings": app_globals.config_manager.get_settings_schema(),
    })

@api_bp.route('/config', methods=['POST'])
def update_config():
    """Updates system configuration."""
    data = request.json or {}
    if not isinstance(data, dict):
        return jsonify({"success": False, "errors": ["Payload must be a JSON object."]}), 400

    success = True
    errors = []
    updated = {}

    for key, raw_value in data.items():
        try:
            value = app_globals.config_manager.coerce_setting_value(key, raw_value)
        except Exception as e:
            success = False
            errors.append(f"{key}: {e}")
            continue

        if not app_globals.config_manager.update_setting(key, value):
            success = False
            errors.append(f"Failed to update {key}")
            continue

        updated[key] = value

    return jsonify({"success": success, "errors": errors, "updated": updated}), (200 if success else 400)

@api_bp.route('/run', methods=['POST'])
def run_script():
    """Executes a Python script."""
    report_user_activity() # Signal user activity
    data = request.json
    path = data.get('path')

    if not path or not path.startswith('projects/'):
        return jsonify({"error": "Invalid path format. Must start with 'projects/'", "success": False}), 400

    # Parse project name and relative path
    # Expected format: "projects/<project_name>/<relative_path>"
    try:
        parts = path.split('/', 2)
        if len(parts) < 3:
             return jsonify({"error": "Invalid path format. Missing project name or file path.", "success": False}), 400
        
        project_name = parts[1]
        file_relative_path = parts[2]
    except Exception as e:
        return jsonify({"error": f"Failed to parse path: {e}", "success": False}), 400

    try:
        # Use find_project to get the true root path
        # from ai_assistant.core.project_manager import find_project
        project = find_project(project_name)

        if not project:
            return jsonify({"error": f"Project '{project_name}' not found.", "success": False}), 404
        
        root_path = project.get('root_path')
        if not root_path or not os.path.exists(root_path):
            return jsonify({"error": f"Project root path invalid for '{project_name}'.", "success": False}), 500

        # Construct full path
        full_path = os.path.abspath(os.path.join(root_path, file_relative_path))

        # Security check: ensure path is within root_path
        if not full_path.startswith(os.path.abspath(root_path)):
             return jsonify({"error": "Access denied: Path traversal detected.", "success": False}), 403

        if not os.path.exists(full_path):
            return jsonify({"error": f"File not found: {full_path}", "success": False}), 404

        # determine cwd (script's directory)
        cwd = os.path.dirname(full_path)

        # Execute
        result = subprocess.run(
            [sys.executable, full_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr

        if not output:
             output = "Script finished with no output."

        return jsonify({"output": output, "success": True})

    except subprocess.TimeoutExpired:
        return jsonify({"output": "Error: Execution timed out (limit: 60s)", "success": False}), 200
    except Exception as e:
        logger.error(f"Error executing script {path}: {e}")
        return jsonify({"output": f"Error: {str(e)}", "success": False}), 500

@api_bp.route('/terminal/exec', methods=['POST'])
def exec_terminal_command():
    """Executes a shell command directly."""
    report_user_activity() # Signal user activity
    
    # Security Check: SAFE_MODE
    if config.SAFE_MODE:
        return jsonify({"error": "Safe Mode is ENABLED. Arbitrary command execution is blocked.", "success": False}), 403

    data = request.json
    command = data.get('command')
    project_name = data.get('project_name')
    
    if not command:
        return jsonify({"error": "Command is required", "success": False}), 400

    # Default to project root equivalent logic (cwd)
    # We can't easily get 'project_root' of the main app here without importing from config or something
    # But usually this is for projects.
    cwd = os.getcwd() # Default
    
    if project_name:
         try:
             project = find_project(project_name)
             if project and project.get('root_path'):
                 cwd = project.get('root_path')
         except Exception as e:
             logger.warning(f"Could not resolve project path for {project_name}: {e}")

    try:
        # Use shell=True to allow complex commands (pipes, etc.) - Security Risk if public, but this is local user app.
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd
        )
        
        return jsonify({
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "cwd": cwd,
            "success": True
        })

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Execution timed out", "success": False}), 408
    except Exception as e:
        logger.error(f"Error executing command '{command}': {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/speak', methods=['POST'])
def api_speak():
    data = request.json
    text = data.get('text')
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    audio_data = generate_speech(text)
    if audio_data:
        return Response(audio_data, mimetype="audio/mpeg")
    else:
        return jsonify({"error": "TTS generation failed"}), 500

@api_bp.route('/system/quarantine', methods=['GET'])
def get_quarantine_status():
    """Returns the current list of blocked tools."""
    if not app_globals.orchestrator:
        return jsonify({"error": "Orchestrator not initialized", "success": False}), 500

    blocked_tools = app_globals.orchestrator.get_blocked_tools()
    return jsonify({"success": True, "blocked_tools": blocked_tools})

@api_bp.route('/system/action-audit', methods=['GET'])
def get_action_audit():
    """Returns recent autonomous action audit events."""
    raw_limit = request.args.get("limit", 100)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = 100

    events = get_recent_action_audit_events(limit=limit)
    return jsonify({
        "success": True,
        "events": events,
        "count": len(events),
    })

@api_bp.route('/system/experiment-scoreboard', methods=['GET'])
def get_experiment_scoreboard():
    """Returns recent experiment scorecards."""
    raw_limit = request.args.get("limit", 100)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = 100

    records = get_recent_experiment_scorecards(limit=limit)
    return jsonify({
        "success": True,
        "records": records,
        "count": len(records),
    })

@api_bp.route('/system/patch-memory', methods=['GET'])
def get_patch_memory():
    """Returns matching patch-memory lessons."""
    raw_limit = request.args.get("limit", 20)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = 20

    lessons = search_patch_lessons(
        failure_class=request.args.get("failure_class"),
        action_type=request.args.get("action_type"),
        file_path=request.args.get("file_path"),
        limit=limit,
    )
    return jsonify({
        "success": True,
        "lessons": lessons,
        "count": len(lessons),
    })

@api_bp.route('/system/tool-lifecycle', methods=['GET'])
def get_tool_lifecycle():
    """Returns generated/dynamic tool lifecycle records."""
    raw_limit = request.args.get("limit", 100)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = 100

    records = list_tool_lifecycle_records(
        state=request.args.get("state"),
        limit=limit,
    )
    return jsonify({
        "success": True,
        "records": records,
        "count": len(records),
    })

@api_bp.route('/system/run-spine', methods=['GET'])
def get_run_spine():
    """Returns one joined view of tasks, approvals, audit, scorecards, and lessons."""
    limit = _coerce_limit(request.args.get("limit"), default=12, maximum=50)
    try:
        snapshot = build_run_spine_snapshot(limit=limit)
        return jsonify({
            "success": True,
            "snapshot": snapshot,
        })
    except Exception as exc:
        logger.exception("Run spine snapshot failed")
        return jsonify({"success": False, "error": str(exc)}), 500

@api_bp.route('/system/quarantine/unblock', methods=['POST'])
def unblock_quarantined_tool():
    """Manually unblocks a specific quarantined tool."""
    if not app_globals.orchestrator:
        return jsonify({"error": "Orchestrator not initialized", "success": False}), 500

    data = request.json or {}
    tool_name = data.get('tool_name')

    if not tool_name:
        return jsonify({"error": "tool_name is required", "success": False}), 400

    success = app_globals.orchestrator.unblock_tool(tool_name)
    if success:
        return jsonify({"success": True, "message": f"Tool '{tool_name}' unblocked."})
    else:
        return jsonify({"success": False, "error": f"Tool '{tool_name}' not found in quarantine."}), 404

@api_bp.route('/system/shutdown', methods=['POST'])
def system_shutdown():
    """Shuts down the server gracefully."""
    logger.info("Shutdown requested via API.")
    
    def shutdown_server():
        # Delay slightly to allow response to be sent
        import time
        time.sleep(1)
        
        # Signal shutdown to stop new tasks
        shutdown_manager.request_shutdown(timeout_seconds=5)
        
        # We rely on TaskManager's synchronous WAL (Write-Ahead Log) to persist state safely.
        # Wait just a moment for the response to clear and background threads to catch the signal.
        logger.info("Shutdown: Tasks are persisted to WAL. Exiting immediately...")
        time.sleep(2)

        # Force exit
        os._exit(0)

    # Run shutdown in a separate thread so this request can return 200 OK
    threading.Thread(target=shutdown_server).start()
    return jsonify({"success": True, "message": "System shutting down gracefully..."})
