
from flask import request, jsonify
from . import api_bp
import logging
import app_globals
from ai_assistant.core.approval_manager import approval_manager
from ai_assistant.learning.learning import ActionableInsight, InsightType
from ai_assistant.core.task_manager import ActiveTaskStatus, ActiveTaskType
from ai_assistant.core.status_reporting import get_status_snapshot
from ai_assistant.core.conversational_alerts import execute_alert_action
from ai_assistant.core.background_service import get_service_status
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.util

logger = logging.getLogger(__name__)

DEFAULT_NOTICE_SCOPE = "local_default"
MAX_IDENTITY_COMPONENT_LENGTH = 256
MAX_IDENTITY_KEY_LENGTH = 512

REFLECTION_TYPE_TEMPLATE_MAP = {
    "TOOL_BUG_SUSPECTED": ["code_reviewer_v1"],
    "TOOL_ENHANCEMENT_SUGGESTED": ["code_reviewer_v1", "ops_assistant_v1"],
    "UNKNOWN": ["code_reviewer_v1"],
}

SPECIALIST_TEMPLATE_REGISTRY = {
    "code_reviewer_v1": {
        "template_id": "code_reviewer_v1",
        "label": "Code Reviewer",
        "worker_profile": "task_reviewer_worker",
        "scope_type": "session",
        "capability_profile": "review_only",
        "retention_policy": "keep_summary_only",
        "description": "Reviews generated code for correctness and maintainability.",
    },
    "ops_assistant_v1": {
        "template_id": "ops_assistant_v1",
        "label": "Ops Assistant",
        "worker_profile": "ops_assistant_worker",
        "scope_type": "user",
        "capability_profile": "ops_diagnostics",
        "retention_policy": "retain_user_profile_with_provenance",
        "description": "Performs operational diagnostics with user-scoped memory.",
    },
}


def _is_truthy_query_flag(raw_value: str) -> bool:
    return str(raw_value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_include_flag(query_key: str, default: bool = True) -> bool:
    raw = request.args.get(query_key)
    if raw is None:
        return default
    return _is_truthy_query_flag(raw)




def _contains_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 for ch in value)


def _derive_identity_key_from_request(args_or_payload: dict):
    payload = args_or_payload if isinstance(args_or_payload, dict) else {}
    explicit = str(payload.get("identity_key") or "").strip()
    platform = str(payload.get("platform") or "").strip().lower()
    user_id = str(payload.get("user_id") or "").strip()
    chat_id = str(payload.get("chat_id") or "").strip()

    has_tuple_parts = any([platform, user_id, chat_id])
    if explicit and has_tuple_parts:
        return "", "Provide either identity_key or platform+user_id+chat_id, not both"

    if explicit:
        if len(explicit) > MAX_IDENTITY_KEY_LENGTH:
            return "", f"identity_key exceeds max length ({MAX_IDENTITY_KEY_LENGTH})"
        if _contains_control_chars(explicit):
            return "", "identity_key contains invalid control characters"
        return explicit, ""

    if has_tuple_parts and not all([platform, user_id, chat_id]):
        return "", "platform, user_id, and chat_id must all be provided together"

    if not has_tuple_parts:
        return "", ""

    for name, value in (("platform", platform), ("user_id", user_id), ("chat_id", chat_id)):
        if len(value) > MAX_IDENTITY_COMPONENT_LENGTH:
            return "", f"{name} exceeds max length ({MAX_IDENTITY_COMPONENT_LENGTH})"
        if _contains_control_chars(value):
            return "", f"{name} contains invalid control characters"
        if ':' in value:
            return "", f"{name} cannot contain ':'"

    return f"{platform}:{user_id}:{chat_id}", ""


def _resolve_notice_scope_from_request(args_or_payload: dict):
    identity_key, error = _derive_identity_key_from_request(args_or_payload)
    if error:
        return "", error
    return identity_key or DEFAULT_NOTICE_SCOPE, ""

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



def _get_task_manager_for_specialist_spawn():
    if app_globals.task_manager:
        return app_globals.task_manager
    if app_globals.orchestrator and getattr(app_globals.orchestrator, "task_manager", None):
        return app_globals.orchestrator.task_manager
    return None


def _serialize_specialist_template(item: dict) -> dict:
    return {
        "template_id": str(item.get("template_id") or ""),
        "label": str(item.get("label") or ""),
        "description": str(item.get("description") or ""),
        "worker_profile": str(item.get("worker_profile") or ""),
        "scope_type": str(item.get("scope_type") or ""),
        "capability_profile": str(item.get("capability_profile") or ""),
        "retention_policy": str(item.get("retention_policy") or ""),
    }



def _recommend_specialist_templates(insight_type_name: str) -> list:
    candidates = REFLECTION_TYPE_TEMPLATE_MAP.get(str(insight_type_name or "").upper(), [])
    if not candidates:
        candidates = REFLECTION_TYPE_TEMPLATE_MAP.get("UNKNOWN", [])
    return [item for item in candidates if item in SPECIALIST_TEMPLATE_REGISTRY]


def _create_specialist_spawn_task(template_id: str, task_description: str, session_id: str = None, source_insight_id: str = None, metadata: dict = None):
    template = SPECIALIST_TEMPLATE_REGISTRY.get(template_id)
    if not template:
        return None, None, {"success": False, "error": f"Unknown template_id '{template_id}'"}, 400

    description = str(task_description or "").strip()
    if not description:
        return None, None, {"success": False, "error": "task_description is required"}, 400

    task_manager = _get_task_manager_for_specialist_spawn()
    if not task_manager:
        return None, None, {"success": False, "error": "Task manager unavailable"}, 503

    details = {
        "source": "specialist_template",
        "template_id": template_id,
        "worker_profile": template.get("worker_profile"),
        "scope_type": template.get("scope_type"),
        "capability_profile": template.get("capability_profile"),
        "retention_policy": template.get("retention_policy"),
        "source_insight_id": str(source_insight_id or "").strip() or None,
        "spawn_metadata": metadata if isinstance(metadata, dict) else {},
    }

    task = task_manager.add_task(
        description=f"Specialist ({template_id}): {description[:200]}",
        task_type=ActiveTaskType.EPHEMERAL_AGENT_TASK,
        details=details,
        session_id=(str(session_id).strip() or None) if session_id is not None else None,
    )

    return task, template, details, 202

def _find_reflection_insight(req_id: str):
    if not app_globals.orchestrator:
        return None

    learning_agent = getattr(app_globals.orchestrator, "learning_agent", None)
    insights = getattr(learning_agent, "insights", None)
    if not insights:
        return None

    return next((i for i in insights if getattr(i, "insight_id", None) == req_id), None)


def _collect_reflection_suggestions(limit: int = 20) -> list:
    if not app_globals.orchestrator:
        return []

    learning_agent = getattr(app_globals.orchestrator, "learning_agent", None)
    insights = getattr(learning_agent, "insights", None)
    if not insights:
        return []

    items = []
    for insight in insights:
        status = str(getattr(insight, "status", "") or "")
        if status not in {"NEW", "SELF_HEALING_PROPOSED"}:
            continue

        insight_type = getattr(insight, "type", None)
        if hasattr(insight_type, "name"):
            insight_type_name = insight_type.name
        else:
            insight_type_name = str(insight_type or "UNKNOWN")

        created = getattr(insight, "creation_timestamp", 0)
        try:
            created_ts = float(created)
        except (TypeError, ValueError):
            created_ts = 0.0

        insight_id = getattr(insight, "insight_id", "")
        recommended_templates = _recommend_specialist_templates(insight_type_name)
        items.append({
            "insight_id": insight_id,
            "type": insight_type_name,
            "status": status,
            "description": getattr(insight, "description", ""),
            "created_at": created,
            "created_at_ts": created_ts,
            "recommended_specialist_templates": recommended_templates,
            "actions": {
                "approve": f"/api/status/reflection-suggestions/{insight_id}/approve",
                "reject": f"/api/status/reflection-suggestions/{insight_id}/reject",
                "spawn_specialist": f"/api/status/reflection-suggestions/{insight_id}/spawn-specialist",
            },
        })

    sorted_items = sorted(items, key=lambda i: i.get("created_at_ts", 0), reverse=True)[:max(0, int(limit))]
    for item in sorted_items:
        item.pop("created_at_ts", None)
    return sorted_items


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

        summary_only = _is_truthy_query_flag(request.args.get('summary_only'))
        include_delegation_topology = _resolve_include_flag('include_delegation_topology', default=True)
        include_work_inbox_preview = _resolve_include_flag('include_work_inbox_preview', default=True)
        include_identity_stale_preview = _resolve_include_flag('include_identity_stale_preview', default=True)

        if summary_only:
            include_delegation_topology = False
            include_work_inbox_preview = False
            include_identity_stale_preview = False

        if not include_delegation_topology:
            snapshot["delegation_topology"] = []
        if not include_work_inbox_preview:
            snapshot["work_inbox_preview"] = []
        if not include_identity_stale_preview:
            snapshot["identity_session_pointers_stale_preview"] = []

        generated_at = datetime.now(timezone.utc)
        return jsonify({
            "success": True,
            "schema_version": 1,
            "generated_at": generated_at.isoformat(),
            "generated_at_ms": int(generated_at.timestamp() * 1000),
            "summary_only": summary_only,
            "includes": {
                "delegation_topology": include_delegation_topology,
                "work_inbox_preview": include_work_inbox_preview,
                "identity_session_pointers_stale_preview": include_identity_stale_preview,
            },
            "snapshot": snapshot,
        })
    except Exception as e:
        logger.error(f"Error generating status snapshot: {e}")
        return jsonify({"error": str(e), "success": False}), 500




@api_bp.route('/status/reflection-suggestions', methods=['GET'])
def mission_control_reflection_suggestions():
    """Returns reflection-derived suggestion candidates for Mission Control triage."""
    limit = _coerce_positive_int(request.args.get('limit'), default=20, maximum=100)
    suggestions = _collect_reflection_suggestions(limit=limit)

    generated_at = datetime.now(timezone.utc)
    return jsonify({
        'success': True,
        'schema_version': 1,
        'generated_at': generated_at.isoformat(),
        'generated_at_ms': int(generated_at.timestamp() * 1000),
        'count': len(suggestions),
        'limit': limit,
        'items': suggestions,
    })


@api_bp.route('/status/reflection-suggestions/<req_id>/approve', methods=['POST'])
def mission_control_approve_reflection_suggestion(req_id):
    """Approves a reflection suggestion for operator triage workflows."""
    insight = _find_reflection_insight(req_id)
    if not insight:
        return jsonify({"success": False, "error": "Suggestion not found"}), 404

    status = str(getattr(insight, "status", "") or "")
    if status not in {"NEW", "SELF_HEALING_PROPOSED"}:
        return jsonify({"success": False, "error": f"Suggestion is not pending (status={status})"}), 400

    feedback = (request.json or {}).get("feedback") if request.is_json else None
    if not getattr(insight, "metadata", None):
        insight.metadata = {}
    if feedback:
        insight.metadata["operator_feedback"] = str(feedback)

    insight.status = "APPROVED_BY_USER"
    app_globals.orchestrator.learning_agent._save_insights()
    return jsonify({"success": True, "insight_id": req_id, "status": insight.status})


@api_bp.route('/status/reflection-suggestions/<req_id>/reject', methods=['POST'])
def mission_control_reject_reflection_suggestion(req_id):
    """Rejects a reflection suggestion for operator triage workflows."""
    insight = _find_reflection_insight(req_id)
    if not insight:
        return jsonify({"success": False, "error": "Suggestion not found"}), 404

    status = str(getattr(insight, "status", "") or "")
    if status not in {"NEW", "SELF_HEALING_PROPOSED"}:
        return jsonify({"success": False, "error": f"Suggestion is not pending (status={status})"}), 400

    feedback = (request.json or {}).get("feedback") if request.is_json else None
    if not getattr(insight, "metadata", None):
        insight.metadata = {}
    if feedback:
        insight.metadata["operator_rejection_reason"] = str(feedback)

    insight.status = "REJECTED_BY_USER"
    app_globals.orchestrator.learning_agent._save_insights()
    return jsonify({"success": True, "insight_id": req_id, "status": insight.status})




@api_bp.route('/status/specialist-templates', methods=['GET'])
def mission_control_specialist_templates():
    """Lists approved specialist templates for controlled spawn workflows."""
    templates = [
        _serialize_specialist_template(item)
        for _, item in sorted(SPECIALIST_TEMPLATE_REGISTRY.items(), key=lambda kv: kv[0])
    ]
    return jsonify({
        "success": True,
        "schema_version": 1,
        "count": len(templates),
        "items": templates,
    })


@api_bp.route('/status/specialist-spawns', methods=['POST'])
def mission_control_specialist_spawns():
    """Spawns a specialist worker from an approved template only."""
    data = request.json or {}
    template_id = str(data.get("template_id") or "").strip()
    if not template_id:
        return jsonify({"success": False, "error": "template_id is required"}), 400

    task, template, details, status_code = _create_specialist_spawn_task(
        template_id=template_id,
        task_description=str(data.get("task_description") or "").strip(),
        session_id=data.get("session_id"),
        source_insight_id=data.get("source_insight_id"),
        metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
    )
    if status_code != 202:
        return jsonify(details), status_code

    return jsonify({
        "success": True,
        "schema_version": 1,
        "task_id": task.task_id,
        "template": _serialize_specialist_template(template),
        "details": details,
    }), 202


@api_bp.route('/status/reflection-suggestions/<req_id>/spawn-specialist', methods=['POST'])
def mission_control_spawn_specialist_from_reflection(req_id):
    """Spawns a specialist from a reflection suggestion using approved templates."""
    insight = _find_reflection_insight(req_id)
    if not insight:
        return jsonify({"success": False, "error": "Suggestion not found"}), 404

    status = str(getattr(insight, "status", "") or "")
    if status not in {"NEW", "SELF_HEALING_PROPOSED", "APPROVED_BY_USER"}:
        return jsonify({"success": False, "error": f"Suggestion is not spawnable (status={status})"}), 400

    data = request.json or {}
    insight_type = getattr(getattr(insight, "type", None), "name", None) or str(getattr(insight, "type", "UNKNOWN") or "UNKNOWN")
    recommended = _recommend_specialist_templates(insight_type)
    template_id = str(data.get("template_id") or "").strip() or (recommended[0] if recommended else "")
    if not template_id:
        return jsonify({"success": False, "error": "No recommended template available for this suggestion"}), 400

    description = str(data.get("task_description") or "").strip()
    if not description:
        description = str(getattr(insight, "description", "") or "").strip()

    task, template, details, status_code = _create_specialist_spawn_task(
        template_id=template_id,
        task_description=description,
        session_id=data.get("session_id"),
        source_insight_id=req_id,
        metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
    )
    if status_code != 202:
        return jsonify(details), status_code

    if not getattr(insight, "metadata", None):
        insight.metadata = {}
    insight.metadata["specialist_spawn_task_id"] = task.task_id
    insight.metadata["specialist_spawn_template_id"] = template_id
    insight.status = "SPECIALIST_SPAWNED"
    if app_globals.orchestrator and getattr(app_globals.orchestrator, "learning_agent", None):
        app_globals.orchestrator.learning_agent._save_insights()

    return jsonify({
        "success": True,
        "schema_version": 1,
        "insight_id": req_id,
        "insight_status": insight.status,
        "task_id": task.task_id,
        "template": _serialize_specialist_template(template),
        "details": details,
    }), 202


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




def _sort_delegation_topology(items):
    sort_by_raw = (request.args.get('sort_by') or 'task_id').strip()
    order_raw = (request.args.get('order') or 'asc').strip().lower()

    allowed_sort_keys = {
        'task_id',
        'worker_profile',
        'scope_type',
        'state',
        'source',
        'capability_profile',
        'retention_policy',
    }
    sort_by = sort_by_raw if sort_by_raw in allowed_sort_keys else 'task_id'
    order = order_raw if order_raw in {'asc', 'desc'} else 'asc'
    reverse = order == 'desc'

    def sort_value(edge):
        return str((edge or {}).get(sort_by, '')).lower()

    sorted_items = sorted(items or [], key=sort_value, reverse=reverse)
    return sorted_items, sort_by, order



def _summarize_topology_breakdowns(items):
    by_worker = {}
    by_state = {}
    by_scope = {}
    by_source = {}

    for edge in items or []:
        if not isinstance(edge, dict):
            continue
        worker = str(edge.get('worker_profile', 'unknown') or 'unknown')
        state = str(edge.get('state', 'unknown') or 'unknown')
        scope = str(edge.get('scope_type', 'unknown') or 'unknown')
        source = str(edge.get('source', 'unknown') or 'unknown')

        by_worker[worker] = by_worker.get(worker, 0) + 1
        by_state[state] = by_state.get(state, 0) + 1
        by_scope[scope] = by_scope.get(scope, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1

    return {
        'by_worker': dict(sorted(by_worker.items())),
        'by_state': dict(sorted(by_state.items())),
        'by_scope': dict(sorted(by_scope.items())),
        'by_source': dict(sorted(by_source.items())),
    }



def _summarize_topology_distincts(items):
    workers = set()
    states = set()
    scopes = set()
    sources = set()

    for edge in items or []:
        if not isinstance(edge, dict):
            continue
        workers.add(str(edge.get('worker_profile', 'unknown') or 'unknown'))
        states.add(str(edge.get('state', 'unknown') or 'unknown'))
        scopes.add(str(edge.get('scope_type', 'unknown') or 'unknown'))
        sources.add(str(edge.get('source', 'unknown') or 'unknown'))

    return {
        'worker_profile': sorted(workers),
        'state': sorted(states),
        'scope_type': sorted(scopes),
        'source': sorted(sources),
    }


def _resolve_include_items() -> bool:
    raw = request.args.get('include_items')
    if raw is None:
        return True
    return _is_truthy_query_flag(raw)


def _resolve_include_breakdowns() -> bool:
    return _resolve_include_flag('include_breakdowns', default=True)


def _resolve_include_distincts() -> bool:
    return _resolve_include_flag('include_distincts', default=True)


def _resolve_breakdown_scope() -> str:
    scope = (request.args.get('breakdown_scope') or 'page').strip().lower()
    if scope in {'page', 'filtered'}:
        return scope
    return 'page'

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

    sorted_items, sort_by, order = _sort_delegation_topology(filtered)
    total = len(sorted_items)
    limit = _coerce_positive_int(request.args.get('limit'), default=50, maximum=200)
    offset = _coerce_positive_int(request.args.get('offset'), default=0, maximum=10000)
    items = sorted_items[offset:offset + limit] if limit else []
    returned_count = len(items)
    has_more = (offset + returned_count) < total
    next_offset = (offset + returned_count) if has_more else None

    return {
        'items': items,
        'all_filtered_items': sorted_items,
        'total': total,
        'limit': limit,
        'offset': offset,
        'returned_count': returned_count,
        'has_more': has_more,
        'next_offset': next_offset,
        'filters': {
            'worker_profile': worker_profile or None,
            'scope_type': scope_type or None,
            'state': states or None,
            'source': source or None,
            'task_id_prefix': task_id_prefix or None,
            'sort_by': sort_by,
            'order': order,
        },
    }




def _build_agent_scope_audit(topology):
    required_fields = ["scope_type", "capability_profile", "retention_policy", "worker_profile"]
    allowed_scope_types = {"session", "user"}

    total_edges = 0
    compliant_edges = 0
    violations = []

    by_scope = {"session": 0, "user": 0, "other": 0}
    by_capability = {}
    by_retention = {}
    by_worker = {}

    for edge in topology or []:
        if not isinstance(edge, dict):
            continue
        total_edges += 1

        worker = str(edge.get("worker_profile", "unknown") or "unknown")
        scope = str(edge.get("scope_type", "") or "")
        capability = str(edge.get("capability_profile", "") or "")
        retention = str(edge.get("retention_policy", "") or "")

        by_worker[worker] = by_worker.get(worker, 0) + 1
        if scope in allowed_scope_types:
            by_scope[scope] += 1
        else:
            by_scope["other"] += 1

        if capability:
            by_capability[capability] = by_capability.get(capability, 0) + 1
        if retention:
            by_retention[retention] = by_retention.get(retention, 0) + 1

        missing = [field for field in required_fields if not str(edge.get(field, "") or "").strip()]
        invalid_scope = scope not in allowed_scope_types

        if not missing and not invalid_scope:
            compliant_edges += 1
            continue

        violations.append({
            "task_id": str(edge.get("task_id", "")),
            "worker_profile": worker,
            "missing_fields": missing,
            "invalid_scope_type": scope if invalid_scope else None,
        })

    violation_count = len(violations)
    return {
        "total_edges": total_edges,
        "compliant_edges": compliant_edges,
        "violation_count": violation_count,
        "compliance_rate": (compliant_edges / total_edges) if total_edges else 1.0,
        "required_fields": required_fields,
        "allowed_scope_types": sorted(allowed_scope_types),
        "distribution": {
            "by_scope": by_scope,
            "by_worker": dict(sorted(by_worker.items())),
            "by_capability_profile": dict(sorted(by_capability.items())),
            "by_retention_policy": dict(sorted(by_retention.items())),
        },
        "violations_preview": violations[:20],
    }




def _build_agent_policy_matrix(topology):
    matrix = {}

    for edge in topology or []:
        if not isinstance(edge, dict):
            continue

        worker = str(edge.get('worker_profile', 'unknown') or 'unknown')
        scope = str(edge.get('scope_type', 'unknown') or 'unknown')
        capability = str(edge.get('capability_profile', 'unknown') or 'unknown')
        retention = str(edge.get('retention_policy', 'unknown') or 'unknown')
        source = str(edge.get('source', 'unknown') or 'unknown')

        bucket = matrix.setdefault(worker, {
            'worker_profile': worker,
            'count': 0,
            'scope_types': set(),
            'capability_profiles': set(),
            'retention_policies': set(),
            'sources': set(),
        })

        bucket['count'] += 1
        bucket['scope_types'].add(scope)
        bucket['capability_profiles'].add(capability)
        bucket['retention_policies'].add(retention)
        bucket['sources'].add(source)

    rows = []
    for worker, item in sorted(matrix.items()):
        scope_types = sorted(item['scope_types'])
        capability_profiles = sorted(item['capability_profiles'])
        retention_policies = sorted(item['retention_policies'])
        sources = sorted(item['sources'])

        rows.append({
            'worker_profile': worker,
            'count': item['count'],
            'scope_types': scope_types,
            'capability_profiles': capability_profiles,
            'retention_policies': retention_policies,
            'sources': sources,
            'policy_consistency': {
                'single_scope_type': len(scope_types) <= 1,
                'single_capability_profile': len(capability_profiles) <= 1,
                'single_retention_policy': len(retention_policies) <= 1,
            }
        })

    return {
        'rows': rows,
        'workers_total': len(rows),
    }


@api_bp.route('/status/agent-policy-matrix', methods=['GET'])
def mission_control_agent_policy_matrix():
    """Returns worker-profile policy matrix over active delegation edges."""
    if not app_globals.orchestrator:
        return jsonify({"error": "System starting up...", "success": False}), 503

    try:
        active_tasks = app_globals.orchestrator.task_manager.list_active_tasks()
        snapshot = get_status_snapshot(active_tasks_count=len(active_tasks), active_tasks=active_tasks)
        topology = snapshot.get('delegation_topology', []) if isinstance(snapshot, dict) else []
        matrix = _build_agent_policy_matrix(topology)

        generated_at = datetime.now(timezone.utc)
        return jsonify({
            "success": True,
            "schema_version": 1,
            "generated_at": generated_at.isoformat(),
            "generated_at_ms": int(generated_at.timestamp() * 1000),
            "matrix": matrix,
        })
    except Exception as e:
        logger.error(f"Error generating agent policy matrix: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route('/status/agent-scope-audit', methods=['GET'])
def mission_control_agent_scope_audit():
    """Returns compliance diagnostics for agent scope declarations on active delegation edges."""
    if not app_globals.orchestrator:
        return jsonify({"error": "System starting up...", "success": False}), 503

    try:
        active_tasks = app_globals.orchestrator.task_manager.list_active_tasks()
        snapshot = get_status_snapshot(active_tasks_count=len(active_tasks), active_tasks=active_tasks)
        topology = snapshot.get('delegation_topology', []) if isinstance(snapshot, dict) else []
        audit = _build_agent_scope_audit(topology)

        generated_at = datetime.now(timezone.utc)
        return jsonify({
            "success": True,
            "schema_version": 1,
            "generated_at": generated_at.isoformat(),
            "generated_at_ms": int(generated_at.timestamp() * 1000),
            "audit": audit,
        })
    except Exception as e:
        logger.error(f"Error generating agent scope audit: {e}")
        return jsonify({"error": str(e), "success": False}), 500


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
        include_items = _resolve_include_items()
        include_breakdowns = _resolve_include_breakdowns()
        include_distincts = _resolve_include_distincts()
        breakdown_scope = _resolve_breakdown_scope()
        breakdown_source_items = filtered['all_filtered_items'] if breakdown_scope == 'filtered' else filtered['items']
        breakdowns = _summarize_topology_breakdowns(breakdown_source_items) if include_breakdowns else {}
        distincts = _summarize_topology_distincts(filtered['all_filtered_items']) if include_distincts else {}
        return jsonify({
            'success': True,
            'schema_version': 1,
            'generated_at': generated_at.isoformat(),
            'generated_at_ms': int(generated_at.timestamp() * 1000),
            'total': filtered['total'],
            'limit': filtered['limit'],
            'offset': filtered['offset'],
            'returned_count': filtered['returned_count'],
            'has_more': filtered['has_more'],
            'next_offset': filtered['next_offset'],
            'filters': filtered['filters'],
            'include_items': include_items,
            'include_breakdowns': include_breakdowns,
            'include_distincts': include_distincts,
            'items': filtered['items'] if include_items else [],
            'breakdown_scope': breakdown_scope,
            'breakdowns': breakdowns,
            'distincts': distincts,
        })
    except Exception as e:
        logger.error(f"Error generating delegation topology: {e}")
        return jsonify({"error": str(e), "success": False}), 500



@api_bp.route('/work-inbox', methods=['GET'])
def mission_control_work_inbox():
    """Returns user work-inbox notices with optional state filtering."""
    if not app_globals.chat_manager or not hasattr(app_globals.chat_manager, 'list_user_notices'):
        return jsonify({"error": "Work inbox unavailable", "success": False}), 503

    include_read = _is_truthy_query_flag(request.args.get('include_read'))
    state = (request.args.get('state') or '').strip().lower() or None
    limit = _coerce_positive_int(request.args.get('limit'), default=25, maximum=200)
    notice_scope, scope_error = _resolve_notice_scope_from_request(request.args)
    if scope_error:
        return jsonify({"error": scope_error, "success": False}), 400

    notices = app_globals.chat_manager.list_user_notices(
        notice_scope,
        include_read=include_read,
        limit=limit,
        state=state,
    )
    return jsonify({
        "success": True,
        "schema_version": 1,
        "count": len(notices),
        "include_read": include_read,
        "state": state,
        "items": notices,
        "scope": notice_scope,
    })


@api_bp.route('/work-inbox/<notice_id>/state', methods=['POST'])
def mission_control_work_inbox_set_state(notice_id):
    """Updates a work-inbox notice state (ack/resolve/reopen/snooze)."""
    if not app_globals.chat_manager or not hasattr(app_globals.chat_manager, 'update_user_notice_state'):
        return jsonify({"error": "Work inbox unavailable", "success": False}), 503

    data = request.json or {}
    action = str(data.get('action') or '').strip().lower()
    merged_identity_payload = {
        "identity_key": request.args.get('identity_key'),
        "platform": request.args.get('platform'),
        "user_id": request.args.get('user_id'),
        "chat_id": request.args.get('chat_id'),
    }
    if isinstance(data, dict):
        for key in ("identity_key", "platform", "user_id", "chat_id"):
            if data.get(key) is not None:
                merged_identity_payload[key] = data.get(key)
    notice_scope, scope_error = _resolve_notice_scope_from_request(merged_identity_payload)
    if scope_error:
        return jsonify({"error": scope_error, "success": False}), 400
    if action not in {'ack', 'resolve', 'reopen', 'snooze'}:
        return jsonify({"error": "action must be one of: ack, resolve, reopen, snooze", "success": False}), 400

    snooze_seconds = data.get('snooze_seconds', 3600)
    if action == 'snooze':
        try:
            snooze_seconds = max(1, int(snooze_seconds))
        except (TypeError, ValueError):
            return jsonify({"error": "snooze_seconds must be a positive integer", "success": False}), 400

    updated = app_globals.chat_manager.update_user_notice_state(
        notice_scope,
        notice_id,
        action,
        snooze_seconds=snooze_seconds,
    )
    if not updated:
        return jsonify({"error": "Notice not found or invalid action", "success": False}), 404

    return jsonify({"success": True, "notice": updated, "scope": notice_scope})

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
