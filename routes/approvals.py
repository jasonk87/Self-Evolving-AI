
from flask import request, jsonify
from . import api_bp
import logging
import app_globals
from ai_assistant.core.approval_manager import approval_manager
from ai_assistant.learning.learning import ActionableInsight, InsightType
from ai_assistant.core.task_manager import ActiveTaskStatus
from ai_assistant.core.status_reporting import get_status_snapshot
from ai_assistant.core.conversational_alerts import execute_alert_action
from ai_assistant.core.background_service import get_service_status
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.util

logger = logging.getLogger(__name__)

DEFAULT_NOTICE_SCOPE = "local_default"


def _is_optional_dependency_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False



def _get_identity_pointer_summary() -> dict:
    if not app_globals.chat_manager or not hasattr(app_globals.chat_manager, "get_identity_pointer_summary"):
        return {"total": 0, "by_platform": {}, "stale": 0, "stale_preview": []}

    try:
        summary = app_globals.chat_manager.get_identity_pointer_summary() or {}
        stale_preview = []
        stale_count = 0
        if hasattr(app_globals.chat_manager, "list_identity_pointers"):
            pointers = app_globals.chat_manager.list_identity_pointers(limit=200) or []
            stale = [item for item in pointers if isinstance(item, dict) and not bool(item.get("session_exists"))]
            stale_count = len(stale)
            stale_preview = [
                {
                    "identity_key": str(item.get("identity_key", "")),
                    "session_id": str(item.get("session_id", "")),
                }
                for item in stale[:3]
            ]
        return {
            "total": int(summary.get("total", 0)),
            "by_platform": summary.get("by_platform", {}) if isinstance(summary.get("by_platform", {}), dict) else {},
            "stale": stale_count,
            "stale_preview": stale_preview,
        }
    except Exception:
        logger.exception("Failed to compute identity pointer summary")
        return {"total": 0, "by_platform": {}, "stale": 0, "stale_preview": []}


def _get_work_inbox_summary() -> dict:
    if not app_globals.chat_manager or not hasattr(app_globals.chat_manager, "list_user_notices"):
        return {"unread": 0, "total": 0, "preview": []}

    try:
        unread = app_globals.chat_manager.list_user_notices(DEFAULT_NOTICE_SCOPE, include_read=False, limit=200) or []
        total = app_globals.chat_manager.list_user_notices(DEFAULT_NOTICE_SCOPE, include_read=True, limit=200) or []
        preview = []
        for item in unread[:3]:
            preview.append({
                "id": item.get("id", ""),
                "task_id": item.get("task_id", ""),
                "status": item.get("status", "unknown"),
                "message": item.get("message", ""),
                "source_session_id": item.get("source_session_id", ""),
            })
        return {
            "unread": len(unread),
            "total": len(total),
            "preview": preview,
        }
    except Exception:
        logger.exception("Failed to compute work inbox summary")
        return {"unread": 0, "total": 0, "preview": []}

def serialize_approval_data(data):
    if isinstance(data, ActionableInsight):
        d = asdict(data)
        d['type'] = data.type.name # Enum to string
        return d
    return data # Fallback

# --- Mission Control Endpoints ---

@api_bp.route('/status/snapshot', methods=['GET'])
def mission_control_status_snapshot():
    """Returns a structured Mission Control status snapshot."""
    if not app_globals.orchestrator:
        return jsonify({"error": "System starting up...", "success": False}), 503

    try:
        active_tasks = app_globals.orchestrator.task_manager.list_active_tasks()
        snapshot = get_status_snapshot(active_tasks_count=len(active_tasks), active_tasks=active_tasks)
        inbox_summary = _get_work_inbox_summary()
        snapshot["work_inbox_unread"] = inbox_summary.get("unread", 0)
        snapshot["work_inbox_total"] = inbox_summary.get("total", 0)
        snapshot["work_inbox_preview"] = inbox_summary.get("preview", [])
        identity_summary = _get_identity_pointer_summary()
        snapshot["identity_session_pointers_total"] = identity_summary.get("total", 0)
        snapshot["identity_session_pointers_by_platform"] = identity_summary.get("by_platform", {})
        snapshot["identity_session_pointers_stale"] = identity_summary.get("stale", 0)
        snapshot["identity_session_pointers_stale_preview"] = identity_summary.get("stale_preview", [])
        generated_at = datetime.now(timezone.utc)
        return jsonify({
            "success": True,
            "schema_version": 1,
            "generated_at": generated_at.isoformat(),
            "generated_at_ms": int(generated_at.timestamp() * 1000),
            "snapshot": snapshot,
        })
    except Exception as e:
        logger.error(f"Error generating status snapshot: {e}")
        return jsonify({"error": str(e), "success": False}), 500




@api_bp.route('/status/health-audit', methods=['GET'])
def mission_control_health_audit():
    """Returns a lightweight operational health audit for Mission Control."""
    optional_dependencies = {
        "playwright": _is_optional_dependency_available('playwright'),
        "chromadb": _is_optional_dependency_available('chromadb'),
        "pyaudio": _is_optional_dependency_available('pyaudio'),
    }

    generated_at = datetime.now(timezone.utc)

    checks = [
        {
            "key": "orchestrator",
            "label": "Core Orchestrator",
            "ok": bool(app_globals.orchestrator),
            "details": "Initialized" if app_globals.orchestrator else "Not initialized",
        },
        {
            "key": "chat_manager",
            "label": "Chat Manager",
            "ok": bool(app_globals.chat_manager),
            "details": "Available" if app_globals.chat_manager else "Unavailable",
        },
        {
            "key": "playwright",
            "label": "Vision/Browser Automation",
            "ok": optional_dependencies["playwright"],
            "details": "Installed" if optional_dependencies["playwright"] else "Missing optional dependency 'playwright'",
        },
        {
            "key": "chromadb",
            "label": "Vector Memory (ChromaDB)",
            "ok": optional_dependencies["chromadb"],
            "details": "Installed" if optional_dependencies["chromadb"] else "Missing optional dependency 'chromadb'",
        },
        {
            "key": "pyaudio",
            "label": "Live Mode Audio",
            "ok": optional_dependencies["pyaudio"],
            "details": "Installed" if optional_dependencies["pyaudio"] else "Missing optional dependency 'pyaudio'",
        },
    ]

    return jsonify({
        "success": True,
        "schema_version": 1,
        "health": {
            "healthy": all(item["ok"] for item in checks),
            "checks": checks,
            "failing_count": sum(1 for item in checks if not item["ok"]),
            "generated_at": generated_at.isoformat(),
            "generated_at_ms": int(generated_at.timestamp() * 1000),
        }
    })


@api_bp.route('/status/background-cadence', methods=['GET'])
def mission_control_background_cadence():
    """Returns runtime cadence controls and recent scheduler activity."""
    settings = app_globals.config_manager.get_all_settings() if app_globals.config_manager else {}
    service_status = get_service_status()

    cadence = {
        "dream_mode_enabled": bool(settings.get("ENABLE_DREAM_MODE", False)),
        "dream_interval_seconds": int(settings.get("DREAM_INTERVAL_SECONDS", 86400)),
        "reminder_check_interval_seconds": int(settings.get("REMINDER_CHECK_INTERVAL_SECONDS", 10)),
        "auto_web_pip": bool(settings.get("AUTO_WEB_PIP", True)),
    }

    recent = {
        "last_dream_timestamp": service_status.get("last_dream_timestamp", 0),
        "last_visual_audit_timestamp": service_status.get("last_visual_audit_timestamp", 0),
        "last_self_healing_timestamp": service_status.get("last_self_healing_timestamp", 0),
    }

    return jsonify({
        "success": True,
        "schema_version": 1,
        "cadence": cadence,
        "recent": recent,
    })



def _coerce_positive_int(value, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return 0
    return min(parsed, maximum)


def _filter_delegation_topology(topology):
    worker_profile = (request.args.get('worker_profile') or '').strip()
    scope_type = (request.args.get('scope_type') or '').strip()
    source = (request.args.get('source') or '').strip()
    task_id_prefix = (request.args.get('task_id_prefix') or '').strip()

    state_raw = (request.args.get('state') or '').strip()
    states = [part.strip() for part in state_raw.split(',') if part.strip()]
    state_set = set(states)

    filtered = []
    for edge in topology or []:
        if not isinstance(edge, dict):
            continue
        if worker_profile and str(edge.get('worker_profile', '')) != worker_profile:
            continue
        if scope_type and str(edge.get('scope_type', '')) != scope_type:
            continue
        if state_set and str(edge.get('state', '')) not in state_set:
            continue
        if source and str(edge.get('source', '')) != source:
            continue
        task_id_value = str(edge.get('task_id', ''))
        if task_id_prefix and not task_id_value.startswith(task_id_prefix):
            continue
        filtered.append(edge)

    total = len(filtered)
    limit = _coerce_positive_int(request.args.get('limit'), default=50, maximum=200)
    offset = _coerce_positive_int(request.args.get('offset'), default=0, maximum=10000)
    items = filtered[offset:offset + limit] if limit else []
    return {
        'items': items,
        'total': total,
        'limit': limit,
        'offset': offset,
        'filters': {
            'worker_profile': worker_profile or None,
            'scope_type': scope_type or None,
            'state': states or None,
            'source': source or None,
            'task_id_prefix': task_id_prefix or None,
        },
    }


@api_bp.route('/status/delegation-topology', methods=['GET'])
def mission_control_delegation_topology():
    """Returns filtered delegation topology edges for Mission Control diagnostics."""
    if not app_globals.orchestrator:
        return jsonify({"error": "System starting up...", "success": False}), 503

    try:
        active_tasks = app_globals.orchestrator.task_manager.list_active_tasks()
        snapshot = get_status_snapshot(active_tasks_count=len(active_tasks), active_tasks=active_tasks)
        topology = snapshot.get('delegation_topology', []) if isinstance(snapshot, dict) else []
        filtered = _filter_delegation_topology(topology)

        generated_at = datetime.now(timezone.utc)
        return jsonify({
            'success': True,
            'schema_version': 1,
            'generated_at': generated_at.isoformat(),
            'generated_at_ms': int(generated_at.timestamp() * 1000),
            'total': filtered['total'],
            'limit': filtered['limit'],
            'offset': filtered['offset'],
            'filters': filtered['filters'],
            'items': filtered['items'],
        })
    except Exception as e:
        logger.error(f"Error generating delegation topology: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/tasks', methods=['GET'])
def list_active_tasks():
    """Returns a list of all active tasks."""
    if not app_globals.orchestrator:
         return jsonify({"error": "System starting up...", "success": False}), 503

    try:
        tasks = app_globals.orchestrator.task_manager.list_active_tasks()
        tasks_data = [t.to_dict() for t in tasks]
        return jsonify({"tasks": tasks_data, "success": True})
    except Exception as e:
        logger.error(f"Error listing active tasks: {e}")
        return jsonify({"error": str(e), "success": False}), 500



@api_bp.route('/tasks/<task_id>/assistant-action', methods=['POST'])
def task_assistant_action(task_id):
    """Executes a conversationally suggested action for a failed task."""
    data = request.json or {}
    action = data.get('action', '')

    result = execute_alert_action(task_id, action)
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code

@api_bp.route('/tasks/<task_id>/stop', methods=['POST'])
def stop_task(task_id):
    """Cancels a specific task."""
    try:
        reason = request.json.get('reason', 'User cancelled via Mission Control') if request.json else 'User cancelled'
        task = app_globals.orchestrator.task_manager.get_task(task_id)
        if not task:
            return jsonify({"error": "Task not found", "success": False}), 404
            
        app_globals.orchestrator.task_manager.update_task_status(
            task_id, 
            ActiveTaskStatus.USER_CANCELLED, 
            reason=reason
        )
        return jsonify({"success": True, "message": f"Task {task_id} cancelled."})
    except Exception as e:
        logger.error(f"Error stopping task {task_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/tasks/<task_id>/message', methods=['POST'])
def message_task(task_id):
    """Injects a user message into the task's context."""
    try:
        message = request.json.get('message')
        if not message:
            return jsonify({"error": "No message provided", "success": False}), 400
            
        task = app_globals.orchestrator.task_manager.get_task(task_id)
        if not task:
            return jsonify({"error": "Task not found", "success": False}), 404
        
        # We store the feedback in details. Check if 'user_feedback' list exists
        if 'user_feedback' not in task.details or not isinstance(task.details['user_feedback'], list):
            task.details['user_feedback'] = []
            
        task.details['user_feedback'].append({
            "timestamp": app_globals.config_manager.get_time(), # or just datetime.now().isoformat()
            "message": message
        })
        
        # Trigger an update so UI sees it (optional, but good for confirmation)
        app_globals.orchestrator.task_manager._save_active_tasks()
        # Also notify via socket potentially? Or let polling handle it.
        
        return jsonify({"success": True, "message": "Feedback injected."})
    except Exception as e:
        logger.error(f"Error messaging task {task_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/approvals', methods=['GET'])
def get_approvals():
    """Lists all pending approval requests, including persistent Actionable Insights."""
    try:
        # 1. Get transient requests from ApprovalManager
        requests = approval_manager.get_pending_requests()
        serialized_requests = []
        for req in requests:
            req_copy = req.copy()
            if 'execute_func' in req_copy:
                del req_copy['execute_func']
            req_copy['data'] = serialize_approval_data(req_copy['data'])
            # Ensure it has a source tag
            req_copy['source'] = 'approval_manager'
            serialized_requests.append(req_copy)

        # 2. Get persistent 'NEW' insights from LearningAgent
        if app_globals.orchestrator and app_globals.orchestrator.learning_agent:
            pending_insights = [
                i for i in app_globals.orchestrator.learning_agent.insights 
                if i.status in ["NEW", "SELF_HEALING_PROPOSED"] 
                and i.type in [
                    InsightType.TOOL_BUG_SUSPECTED, 
                    InsightType.TOOL_ENHANCEMENT_SUGGESTED
                ]
            ]
            
            for insight in pending_insights:
                # Map Insight to Approval Request Format temporarily for UI
                insight_req = {
                    "id": insight.insight_id, # Standardize on 'id' for frontend
                    "type": insight.type.name.lower(), # Use the actual type name (e.g., 'tool_bug_suspected')
                    "description": insight.description,
                    "created_at": insight.creation_timestamp,
                    "data": serialize_approval_data(insight),
                    "source": "learning_agent"
                }
                serialized_requests.append(insight_req)

        return jsonify({"approvals": serialized_requests, "success": True})
    except Exception as e:
        logger.error(f"Error listing approvals: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/approvals/<req_id>/approve', methods=['POST'])
async def approve_request(req_id):
    """Approves a request (either transient or persistent insight)."""
    try:
        feedback = request.json.get('feedback') if request.json else None

        # 1. Try ApprovalManager first
        if approval_manager.get_request(req_id):
            if feedback:
                logger.info(f"User approved request {req_id} with feedback: {feedback}")
            success = await approval_manager.approve_request(req_id)
            if success: return jsonify({"success": True})

        # 2. Try LearningAgent Insights
        if app_globals.orchestrator and app_globals.orchestrator.learning_agent:
            insight = next((i for i in app_globals.orchestrator.learning_agent.insights if i.insight_id == req_id), None)
            if insight:
                if feedback:
                    if not insight.metadata: insight.metadata = {}
                    insight.metadata['user_feedback_on_approval'] = feedback
                    app_globals.memory_manager.add_fact(f"User Approved Insight {req_id} with feedback: {feedback}")

                if insight.type in [InsightType.TOOL_BUG_SUSPECTED, InsightType.TOOL_ENHANCEMENT_SUGGESTED]:
                     success = await app_globals.orchestrator.learning_agent.execute_self_healing_for_insight(insight, apply_immediately=True)
                else:
                    app_globals.orchestrator.learning_agent._save_insights()
                    success = True # Just mark as saved/approved for now
                
                if success:
                     return jsonify({"success": True, "message": "Insight execution triggered."})
                else:
                     return jsonify({"success": False, "error": "Insight execution failed. The insight might be missing required information."}), 400

        return jsonify({"error": "Request not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error approving request {req_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/approvals/<req_id>/deny', methods=['POST'])
def deny_request(req_id):
    """Denies a request."""
    try:
        feedback = request.json.get('feedback') if request.json else None

        # 1. Try ApprovalManager
        if approval_manager.get_request(req_id):
            if feedback:
                logger.info(f"User denied request {req_id} with feedback: {feedback}")
            success = approval_manager.deny_request(req_id)
            if success: return jsonify({"success": True})

        # 2. Try LearningAgent Insights
        if app_globals.orchestrator and app_globals.orchestrator.learning_agent:
            insight = next((i for i in app_globals.orchestrator.learning_agent.insights if i.insight_id == req_id), None)
            if insight:
                insight.status = "REJECTED_BY_USER"
                if feedback:
                    if not insight.metadata: insight.metadata = {}
                    insight.metadata['user_rejection_reason'] = feedback
                    app_globals.memory_manager.add_fact(f"User rejected insight '{insight.description}' with reason: {feedback}")
                
                app_globals.orchestrator.learning_agent._save_insights()
                return jsonify({"success": True})

        return jsonify({"error": "Request not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error denying request {req_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500
