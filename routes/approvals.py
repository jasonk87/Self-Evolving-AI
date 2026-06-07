
from flask import request, jsonify
from . import api_bp
import logging
import app_globals
from ai_assistant.core.telemetry import telemetry_tracker
from ai_assistant.core.approval_manager import approval_manager
from ai_assistant.learning.learning import ActionableInsight, InsightType
from ai_assistant.core.task_manager import ActiveTaskStatus, ActiveTaskType
from ai_assistant.core.status_reporting import get_status_snapshot
from ai_assistant.core.conversational_alerts import execute_alert_action
from ai_assistant.core.background_service import get_service_status
from ai_assistant.core.agent_scope_contracts import (
    CONTRACT_VERSION as AGENT_SCOPE_CONTRACT_VERSION,
    REQUIRED_FIELDS as AGENT_SCOPE_REQUIRED_FIELDS,
    ALLOWED_SCOPE_TYPES as AGENT_SCOPE_ALLOWED_SCOPE_TYPES,
    ALLOWED_CAPABILITY_PROFILES as AGENT_SCOPE_ALLOWED_CAPABILITY_PROFILES,
    ALLOWED_RETENTION_POLICIES as AGENT_SCOPE_ALLOWED_RETENTION_POLICIES,
    normalize_and_validate_agent_policy,
)
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
import importlib.util
import os
import json
import uuid
import asyncio

logger = logging.getLogger(__name__)


def _run_async(coro):
    return asyncio.run(coro)

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


DYNAMIC_SPECIALIST_PROPOSAL_STATUSES = {
    "PENDING_REVIEW",
    "APPROVED",
    "REJECTED",
}

_DYNAMIC_SPECIALIST_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ai_assistant",
    "core",
    "data",
    "dynamic_specialist_proposals.json",
)

_TOKEN_BUDGET_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ai_assistant",
    "core",
    "data",
    "token_budget_settings.json",
)

_OPERATOR_POLICY_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ai_assistant",
    "core",
    "data",
    "operator_policy_settings.json",
)

TOKEN_CATEGORY_RESEARCH = "research"
TOKEN_CATEGORY_CODING = "coding"
TOKEN_CATEGORY_AUTONOMOUS = "autonomous"
TOKEN_CATEGORY_GENERAL = "general"
_ALLOWED_TOKEN_BUDGET_CATEGORIES = {
    TOKEN_CATEGORY_RESEARCH,
    TOKEN_CATEGORY_CODING,
    TOKEN_CATEGORY_AUTONOMOUS,
    TOKEN_CATEGORY_GENERAL,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_token_budget_settings() -> dict:
    return {
        "schema_version": 1,
        "default_daily_budget_usd": 1.0,
        "hard_stop_enabled": False,
        "category_budgets": {
            TOKEN_CATEGORY_RESEARCH: 1.0,
            TOKEN_CATEGORY_CODING: 5.0,
            TOKEN_CATEGORY_AUTONOMOUS: 2.0,
            TOKEN_CATEGORY_GENERAL: 1.0,
        },
        "updated_at": _utc_now_iso(),
    }


def _load_token_budget_settings() -> dict:
    try:
        if not os.path.exists(_TOKEN_BUDGET_STORE_PATH):
            return _default_token_budget_settings()
        with open(_TOKEN_BUDGET_STORE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return _default_token_budget_settings()
        settings = _default_token_budget_settings()
        settings.update(raw)
        category_budgets = settings.get("category_budgets")
        if not isinstance(category_budgets, dict):
            settings["category_budgets"] = _default_token_budget_settings()["category_budgets"]
        return settings
    except Exception:
        logger.exception("Failed to load token budget settings")
        return _default_token_budget_settings()


def _save_token_budget_settings(settings: dict):
    os.makedirs(os.path.dirname(_TOKEN_BUDGET_STORE_PATH), exist_ok=True)
    payload = settings if isinstance(settings, dict) else _default_token_budget_settings()
    payload["updated_at"] = _utc_now_iso()
    with open(_TOKEN_BUDGET_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _default_operator_policy_settings() -> dict:
    return {
        "schema_version": 1,
        "dream_mode_enabled": True,
        "host_automation_enabled": False,
        "host_automation_kill_switch": True,
        "require_provenance": True,
        "allowlisted_roots": [],
        "updated_at": _utc_now_iso(),
    }


def _load_operator_policy_settings() -> dict:
    try:
        if not os.path.exists(_OPERATOR_POLICY_STORE_PATH):
            return _default_operator_policy_settings()
        with open(_OPERATOR_POLICY_STORE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return _default_operator_policy_settings()
        settings = _default_operator_policy_settings()
        settings.update(raw)
        roots = settings.get("allowlisted_roots")
        if not isinstance(roots, list):
            settings["allowlisted_roots"] = []
        else:
            settings["allowlisted_roots"] = [str(item).strip() for item in roots if str(item).strip()]
        return settings
    except Exception:
        logger.exception("Failed to load operator policy settings")
        return _default_operator_policy_settings()


def _save_operator_policy_settings(settings: dict):
    os.makedirs(os.path.dirname(_OPERATOR_POLICY_STORE_PATH), exist_ok=True)
    payload = settings if isinstance(settings, dict) else _default_operator_policy_settings()
    payload["updated_at"] = _utc_now_iso()
    with open(_OPERATOR_POLICY_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _normalize_allowlisted_roots(values) -> list:
    roots = values if isinstance(values, list) else []
    normalized = []
    for value in roots:
        path = os.path.abspath(os.path.expanduser(str(value or "").strip()))
        if path and path not in normalized:
            normalized.append(path)
    return normalized


def _evaluate_host_automation_preflight(policy: dict, payload: dict) -> dict:
    policy_settings = policy if isinstance(policy, dict) else _default_operator_policy_settings()
    request_data = payload if isinstance(payload, dict) else {}
    reasons = []

    if not bool(policy_settings.get("host_automation_enabled", False)):
        reasons.append("host_automation_disabled")
    if bool(policy_settings.get("host_automation_kill_switch", False)):
        reasons.append("host_automation_kill_switch_active")

    requested_path = os.path.abspath(os.path.expanduser(str(request_data.get("target_path") or "").strip()))
    allowlisted_roots = _normalize_allowlisted_roots(policy_settings.get("allowlisted_roots"))
    if requested_path and allowlisted_roots:
        if not any(requested_path.startswith(root) for root in allowlisted_roots):
            reasons.append("target_path_not_allowlisted")

    if bool(policy_settings.get("require_provenance", True)):
        for field in ("actor", "rationale", "requested_at"):
            if not str(request_data.get(field) or "").strip():
                reasons.append(f"missing_provenance:{field}")

    allowed = len(reasons) == 0
    return {
        "allowed": allowed,
        "reasons": reasons,
        "policy_snapshot": {
            "host_automation_enabled": bool(policy_settings.get("host_automation_enabled", False)),
            "host_automation_kill_switch": bool(policy_settings.get("host_automation_kill_switch", False)),
            "require_provenance": bool(policy_settings.get("require_provenance", True)),
            "allowlisted_roots": allowlisted_roots,
        },
        "requested_path": requested_path,
    }


def _coerce_non_negative_float(value, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    if parsed < 0:
        return 0.0
    return round(parsed, 4)


def _estimate_cost_for_entry(input_tokens: int, output_tokens: int, thinking_tokens: int = 0) -> float:
    input_cost = (max(0, int(input_tokens)) / 1_000_000) * 0.075
    output_cost = (max(0, int(output_tokens) + int(thinking_tokens)) / 1_000_000) * 0.30
    return round(input_cost + output_cost, 6)


def _classify_token_task_category(task_name: str) -> str:
    lowered = str(task_name or "").lower()
    if any(keyword in lowered for keyword in ("research", "web", "browse", "search")):
        return TOKEN_CATEGORY_RESEARCH
    if any(keyword in lowered for keyword in ("code", "review", "refactor", "lint", "test")):
        return TOKEN_CATEGORY_CODING
    if any(keyword in lowered for keyword in ("dream", "self_heal", "self-heal", "reflection", "autonomous")):
        return TOKEN_CATEGORY_AUTONOMOUS
    return TOKEN_CATEGORY_GENERAL


def _build_token_dashboard_payload(window_hours: int = 24, history_limit: int = 500) -> dict:
    now_ts = datetime.now(timezone.utc).timestamp()
    window_start_ts = now_ts - (window_hours * 3600)
    history = telemetry_tracker.get_history(limit=history_limit)
    settings = _load_token_budget_settings()
    category_budgets = settings.get("category_budgets") if isinstance(settings.get("category_budgets"), dict) else {}

    totals = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
    }
    by_category = {key: {"calls": 0, "tokens": 0, "estimated_cost_usd": 0.0} for key in _ALLOWED_TOKEN_BUDGET_CATEGORIES}
    by_model = {}

    for entry in history:
        if not isinstance(entry, dict):
            continue
        entry_ts = float(entry.get("timestamp") or 0)
        if entry_ts < window_start_ts:
            continue

        model = str(entry.get("model") or "unknown")
        task_name = str(entry.get("task") or "unknown")
        category = _classify_token_task_category(task_name)
        input_tokens = max(0, int(entry.get("input_tokens") or 0))
        output_tokens = max(0, int(entry.get("output_tokens") or 0))
        thinking_tokens = max(0, int(entry.get("thinking_tokens") or 0))
        total_tokens = max(0, int(entry.get("total_tokens") or (input_tokens + output_tokens)))
        entry_cost = _estimate_cost_for_entry(input_tokens, output_tokens, thinking_tokens)

        totals["calls"] += 1
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["total_tokens"] += total_tokens
        totals["estimated_cost_usd"] = round(totals["estimated_cost_usd"] + entry_cost, 6)

        by_category[category]["calls"] += 1
        by_category[category]["tokens"] += total_tokens
        by_category[category]["estimated_cost_usd"] = round(by_category[category]["estimated_cost_usd"] + entry_cost, 6)

        if model not in by_model:
            by_model[model] = {"calls": 0, "tokens": 0, "estimated_cost_usd": 0.0}
        by_model[model]["calls"] += 1
        by_model[model]["tokens"] += total_tokens
        by_model[model]["estimated_cost_usd"] = round(by_model[model]["estimated_cost_usd"] + entry_cost, 6)

    category_budget_status = []
    for category in sorted(_ALLOWED_TOKEN_BUDGET_CATEGORIES):
        budget_usd = _coerce_non_negative_float(category_budgets.get(category), settings.get("default_daily_budget_usd", 1.0))
        spent_usd = round(by_category.get(category, {}).get("estimated_cost_usd", 0.0), 6)
        usage_percent = _safe_ratio_percent(spent_usd, budget_usd) if budget_usd > 0 else 0.0
        category_budget_status.append(
            {
                "category": category,
                "daily_budget_usd": budget_usd,
                "spent_usd": spent_usd,
                "usage_percent": usage_percent,
                "within_budget": spent_usd <= budget_usd if budget_usd > 0 else True,
            }
        )

    over_budget_categories = [
        item.get("category")
        for item in category_budget_status
        if isinstance(item, dict) and not bool(item.get("within_budget", True))
    ]

    hard_stop_enabled = bool(settings.get("hard_stop_enabled", False))
    enforcement = {
        "over_budget_categories": over_budget_categories,
        "hard_stop_enabled": hard_stop_enabled,
        "hard_stop_triggered": hard_stop_enabled and len(over_budget_categories) > 0,
        "blocked_categories": over_budget_categories if hard_stop_enabled else [],
    }

    return {
        "window_hours": window_hours,
        "history_limit": history_limit,
        "totals": totals,
        "by_category": by_category,
        "by_model": by_model,
        "budget": {
            "default_daily_budget_usd": _coerce_non_negative_float(settings.get("default_daily_budget_usd"), 1.0),
            "hard_stop_enabled": hard_stop_enabled,
            "category_status": category_budget_status,
            "enforcement": enforcement,
        },
    }


def _evaluate_token_budget_preflight(
    category: str,
    projected_cost_usd: float,
    window_hours: int = 24,
) -> dict:
    normalized_category = str(category or TOKEN_CATEGORY_GENERAL).strip().lower()
    if normalized_category not in _ALLOWED_TOKEN_BUDGET_CATEGORIES:
        normalized_category = TOKEN_CATEGORY_GENERAL

    projected = _coerce_non_negative_float(projected_cost_usd, 0.0)
    dashboard = _build_token_dashboard_payload(window_hours=window_hours, history_limit=2000)
    budget = dashboard.get("budget", {}) if isinstance(dashboard, dict) else {}
    category_status = budget.get("category_status", []) if isinstance(budget.get("category_status"), list) else []

    status_for_category = next(
        (item for item in category_status if isinstance(item, dict) and str(item.get("category")) == normalized_category),
        {
            "category": normalized_category,
            "daily_budget_usd": _coerce_non_negative_float(budget.get("default_daily_budget_usd"), 1.0),
            "spent_usd": 0.0,
        },
    )

    daily_budget_usd = _coerce_non_negative_float(status_for_category.get("daily_budget_usd"), 1.0)
    spent_usd = _coerce_non_negative_float(status_for_category.get("spent_usd"), 0.0)
    projected_after_usd = round(spent_usd + projected, 6)

    hard_stop_enabled = bool(budget.get("hard_stop_enabled", False))
    would_exceed_category_budget = projected_after_usd > daily_budget_usd if daily_budget_usd > 0 else False
    blocked = hard_stop_enabled and would_exceed_category_budget

    reasons = []
    if would_exceed_category_budget:
        reasons.append("projected_category_budget_exceeded")
    if blocked:
        reasons.append("hard_stop_block")

    return {
        "category": normalized_category,
        "window_hours": int(window_hours),
        "hard_stop_enabled": hard_stop_enabled,
        "current_spent_usd": spent_usd,
        "projected_additional_cost_usd": projected,
        "projected_spent_usd": projected_after_usd,
        "daily_budget_usd": daily_budget_usd,
        "would_exceed_category_budget": would_exceed_category_budget,
        "allowed": not blocked,
        "blocked": blocked,
        "reasons": reasons,
    }


def _evaluate_execution_preflight(payload: dict) -> dict:
    request_data = payload if isinstance(payload, dict) else {}
    action_type = str(request_data.get("action_type") or "general").strip().lower()
    token_category = str(request_data.get("token_category") or TOKEN_CATEGORY_GENERAL).strip().lower()
    projected_cost_usd = _coerce_non_negative_float(request_data.get("projected_cost_usd"), 0.0)

    token_eval = _evaluate_token_budget_preflight(
        category=token_category,
        projected_cost_usd=projected_cost_usd,
        window_hours=_coerce_window_hours(request_data.get("window_hours"), default=24),
    )

    host_eval = None
    requires_host_checks = action_type in {"host_automation", "desktop", "filesystem"}
    if requires_host_checks:
        policy = _load_operator_policy_settings()
        host_eval = _evaluate_host_automation_preflight(policy, request_data)

    blocked = bool(token_eval.get("blocked", False)) or bool(host_eval and not host_eval.get("allowed", False))
    reasons = []
    if bool(token_eval.get("blocked", False)):
        reasons.extend([f"token:{r}" for r in token_eval.get("reasons", [])])
    if host_eval and not host_eval.get("allowed", False):
        reasons.extend([f"host:{r}" for r in host_eval.get("reasons", [])])

    return {
        "action_type": action_type,
        "allowed": not blocked,
        "blocked": blocked,
        "reasons": reasons,
        "token_budget": token_eval,
        "host_automation": host_eval,
    }


def _default_dynamic_specialist_store() -> dict:
    return {
        "schema_version": 1,
        "proposals": [],
        "audit_trail": [],
        "updated_at": _utc_now_iso(),
    }


def _load_dynamic_specialist_store() -> dict:
    try:
        if not os.path.exists(_DYNAMIC_SPECIALIST_STORE_PATH):
            return _default_dynamic_specialist_store()
        with open(_DYNAMIC_SPECIALIST_STORE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return _default_dynamic_specialist_store()
        store = _default_dynamic_specialist_store()
        store.update(raw)
        if not isinstance(store.get("proposals"), list):
            store["proposals"] = []
        if not isinstance(store.get("audit_trail"), list):
            store["audit_trail"] = []
        return store
    except Exception:
        logger.exception("Failed to load dynamic specialist proposal store")
        return _default_dynamic_specialist_store()


def _save_dynamic_specialist_store(store: dict):
    os.makedirs(os.path.dirname(_DYNAMIC_SPECIALIST_STORE_PATH), exist_ok=True)
    store = store if isinstance(store, dict) else _default_dynamic_specialist_store()
    store["updated_at"] = _utc_now_iso()
    with open(_DYNAMIC_SPECIALIST_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


def _normalize_nonempty_string(raw_value, field_name: str):
    value = str(raw_value or "").strip()
    if not value:
        return "", f"{field_name} is required"
    if _contains_control_chars(value):
        return "", f"{field_name} contains invalid control characters"
    return value, ""


def _validate_dynamic_proposal_payload(data: dict):
    payload = data if isinstance(data, dict) else {}
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    provenance = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}

    required_profile_fields = ["label", "worker_profile", "description"]

    normalized_profile = {}
    for key in required_profile_fields:
        value, error = _normalize_nonempty_string(profile.get(key), f"profile.{key}")
        if error:
            return None, None, error
        normalized_profile[key] = value

    try:
        normalized_policy, _ = normalize_and_validate_agent_policy(profile, strict=True)
    except ValueError as e:
        return None, None, str(e)

    normalized_profile["scope_type"] = normalized_policy.get("scope_type")
    normalized_profile["capability_profile"] = normalized_policy.get("capability_profile")
    normalized_profile["retention_policy"] = normalized_policy.get("retention_policy")

    requested_by, error = _normalize_nonempty_string(provenance.get("requested_by"), "provenance.requested_by")
    if error:
        return None, None, error
    rationale, error = _normalize_nonempty_string(provenance.get("rationale"), "provenance.rationale")
    if error:
        return None, None, error
    rollback_plan, error = _normalize_nonempty_string(provenance.get("rollback_plan"), "provenance.rollback_plan")
    if error:
        return None, None, error
    retirement_policy, error = _normalize_nonempty_string(provenance.get("retirement_policy"), "provenance.retirement_policy")
    if error:
        return None, None, error

    normalized_provenance = {
        "requested_by": requested_by,
        "rationale": rationale,
        "rollback_plan": rollback_plan,
        "retirement_policy": retirement_policy,
    }

    return normalized_profile, normalized_provenance, ""


def _append_dynamic_proposal_audit_event(store: dict, proposal_id: str, event_type: str, actor: str, details: dict = None):
    event = {
        "event_id": f"audit_{uuid.uuid4().hex[:12]}",
        "proposal_id": proposal_id,
        "event_type": event_type,
        "actor": str(actor or "system"),
        "created_at": _utc_now_iso(),
        "details": details if isinstance(details, dict) else {},
    }
    store.setdefault("audit_trail", []).append(event)
    return event


def _find_dynamic_proposal(store: dict, proposal_id: str):
    proposals = store.get("proposals") if isinstance(store, dict) else []
    if not isinstance(proposals, list):
        return None
    return next((item for item in proposals if str(item.get("proposal_id")) == str(proposal_id)), None)



def _build_operator_lifecycle_view(proposal: dict, audit_events: list = None) -> dict:
    proposal = proposal if isinstance(proposal, dict) else {}
    provenance = proposal.get("provenance") if isinstance(proposal.get("provenance"), dict) else {}
    review = proposal.get("review") if isinstance(proposal.get("review"), dict) else {}
    status = str(proposal.get("status") or "").upper()
    events = audit_events if isinstance(audit_events, list) else []

    return {
        "why_this_specialist_exists": str(provenance.get("rationale") or ""),
        "retirement_policy": str(provenance.get("retirement_policy") or ""),
        "rollback_plan": str(provenance.get("rollback_plan") or ""),
        "requested_by": str(provenance.get("requested_by") or ""),
        "review_status": status,
        "reviewed_by": str(review.get("reviewed_by") or ""),
        "reviewed_at": str(review.get("reviewed_at") or "") or None,
        "audit_event_count": len(events),
        "last_audit_event_type": str(events[-1].get("event_type") or "") if events else None,
    }


def _serialize_dynamic_proposal_for_mission_control(proposal: dict, audit_trail: list = None) -> dict:
    item = dict(proposal) if isinstance(proposal, dict) else {}
    proposal_id = str(item.get("proposal_id") or "")
    events = []
    if isinstance(audit_trail, list) and proposal_id:
        events = [ev for ev in audit_trail if isinstance(ev, dict) and str(ev.get("proposal_id") or "") == proposal_id]
    item["operator_lifecycle"] = _build_operator_lifecycle_view(item, events)
    return item


def _safe_ratio_percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 2)


def _parse_iso_datetime(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None



def _coerce_window_hours(value, default: int = 24, minimum: int = 1, maximum: int = 24 * 30) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return minimum
    return min(parsed, maximum)


def _select_archived_tasks_in_window(archived_tasks: list, now_dt: datetime, window_hours: int) -> list:
    items = []
    if not isinstance(archived_tasks, list):
        return items

    window_start = now_dt - timedelta(hours=window_hours)
    for task in archived_tasks:
        details = getattr(task, "details", {}) if isinstance(getattr(task, "details", {}), dict) else {}
        lifecycle = details.get("lifecycle", {}) if isinstance(details.get("lifecycle", {}), dict) else {}
        terminal_at = _parse_iso_datetime(lifecycle.get("terminal_at"))
        if terminal_at is None:
            terminal_at = getattr(task, "last_updated_at", None)
        if terminal_at is None:
            continue
        if terminal_at >= window_start:
            items.append(task)
    return items


def _build_archived_slo_window_metrics(archived_tasks: list, window_hours: int, now_dt: datetime) -> dict:
    tasks = _select_archived_tasks_in_window(archived_tasks, now_dt=now_dt, window_hours=window_hours)

    completion_latency_samples_ms = []
    completed = []
    completed_with_retry = 0
    completed_with_manual_retry = 0
    completed_with_automatic_retry = 0
    recovered = 0
    diagnosis_minutes_samples = []

    for task in tasks:
        status_name = str(getattr(getattr(task, "status", None), "name", "") or "")
        details = getattr(task, "details", {}) if isinstance(getattr(task, "details", {}), dict) else {}
        lifecycle = details.get("lifecycle", {}) if isinstance(details.get("lifecycle", {}), dict) else {}

        first_failed_at = _parse_iso_datetime(lifecycle.get("first_failed_at"))
        diagnosed_at = _parse_iso_datetime(lifecycle.get("diagnosed_at"))
        if first_failed_at and diagnosed_at and diagnosed_at >= first_failed_at:
            diagnosis_minutes_samples.append((diagnosed_at - first_failed_at).total_seconds() / 60.0)

        if status_name != "COMPLETED_SUCCESSFULLY":
            continue

        completed.append(task)
        retry_events = lifecycle.get("retry_events", []) if isinstance(lifecycle.get("retry_events", []), list) else []
        retry_sources = {str(ev.get("source") or "").lower() for ev in retry_events if isinstance(ev, dict)}
        retry_attempts = int(lifecycle.get("retry_attempts", 0) or 0)
        if retry_attempts > 0 or retry_sources:
            completed_with_retry += 1
            if "manual" in retry_sources:
                completed_with_manual_retry += 1
            elif "automatic" in retry_sources or retry_attempts > 0:
                completed_with_automatic_retry += 1
        if lifecycle.get("recovered_after_failure"):
            recovered += 1

        created_at = getattr(task, "created_at", None)
        terminal_at = _parse_iso_datetime(lifecycle.get("terminal_at")) or getattr(task, "last_updated_at", None)
        if created_at and terminal_at:
            duration_ms = int((terminal_at - created_at).total_seconds() * 1000)
            if duration_ms >= 0:
                completion_latency_samples_ms.append(duration_ms)

    completion_latency_samples_ms.sort()
    diagnosis_minutes_samples.sort()

    p50_latency_ms = completion_latency_samples_ms[len(completion_latency_samples_ms) // 2] if completion_latency_samples_ms else None
    retry_rate_pct = _safe_ratio_percent(completed_with_retry, len(completed)) if completed else None
    manual_retry_rate_pct = _safe_ratio_percent(completed_with_manual_retry, len(completed)) if completed else None
    automatic_retry_rate_pct = _safe_ratio_percent(completed_with_automatic_retry, len(completed)) if completed else None
    recovery_rate_pct = _safe_ratio_percent(recovered, len(completed)) if completed else None
    mttd_minutes = round(diagnosis_minutes_samples[len(diagnosis_minutes_samples) // 2], 2) if diagnosis_minutes_samples else None

    failure_candidates = [
        t for t in tasks
        if isinstance(getattr(t, "details", {}), dict)
        and isinstance(getattr(t, "details", {}).get("lifecycle", {}), dict)
        and getattr(t, "details", {}).get("lifecycle", {}).get("first_failed_at")
    ]
    retry_candidates = [
        t for t in completed
        if int((getattr(t, "details", {}) or {}).get("lifecycle", {}).get("retry_attempts", 0) or 0) > 0
    ]

    return {
        "window_hours": window_hours,
        "window_start": (now_dt - timedelta(hours=window_hours)).isoformat(),
        "window_end": now_dt.isoformat(),
        "tasks_considered": len(tasks),
        "completed_tasks_considered": len(completed),
        "metrics": {
            "delegated_completion_latency_p50_ms": p50_latency_ms,
            "manual_retry_rate_pct": retry_rate_pct,
            "manual_retry_source_rate_pct": manual_retry_rate_pct,
            "automatic_retry_source_rate_pct": automatic_retry_rate_pct,
            "failed_delegation_recovery_rate_pct": recovery_rate_pct,
            "mean_time_to_diagnose_minutes": mttd_minutes,
        },
        "contributors": {
            "failed_tasks": _build_window_contributors(failure_candidates),
            "retries": _build_window_contributors(retry_candidates),
        },
    }



def _build_window_contributors(tasks: list) -> dict:
    by_worker = {}
    by_scope = {}
    by_source = {}

    def _inc(bucket: dict, key: str):
        bucket[key] = bucket.get(key, 0) + 1

    for task in tasks or []:
        details = getattr(task, "details", {}) if isinstance(getattr(task, "details", {}), dict) else {}
        worker = str(details.get("worker_profile") or "unknown")
        scope = str(details.get("scope_type") or "unknown")
        source = str(details.get("source") or "unknown")
        _inc(by_worker, worker)
        _inc(by_scope, scope)
        _inc(by_source, source)

    def _sorted_top(d: dict, limit: int = 5):
        return [
            {"key": k, "count": v}
            for k, v in sorted(d.items(), key=lambda item: (-item[1], item[0]))[:limit]
        ]

    return {
        "by_worker": _sorted_top(by_worker),
        "by_scope": _sorted_top(by_scope),
        "by_source": _sorted_top(by_source),
    }


def _severity_from_breach(comparator: str, target: float, value):
    if value is None:
        return "unknown"
    v = float(value)
    if comparator == "lte":
        if v <= target:
            return "ok"
        if v <= target * 1.2:
            return "warning"
        return "critical"
    if comparator == "gte":
        if v >= target:
            return "ok"
        if v >= target * 0.8:
            return "warning"
        return "critical"
    return "unknown"


def _compute_slo_trend_analysis(primary_metrics: dict, compare_metrics: dict) -> dict:
    target_specs = {
        "delegated_completion_latency_p50_ms": {"target": 15000, "comparator": "lte"},
        "manual_retry_rate_pct": {"target": 10.0, "comparator": "lte"},
        "failed_delegation_recovery_rate_pct": {"target": 90.0, "comparator": "gte"},
        "mean_time_to_diagnose_minutes": {"target": 30.0, "comparator": "lte"},
    }

    deltas = {}
    breaches = {}

    for key, spec in target_specs.items():
        p_val = primary_metrics.get(key)
        c_val = compare_metrics.get(key)

        deltas[key] = None
        if p_val is not None and c_val is not None:
            deltas[key] = round(float(p_val) - float(c_val), 2)

        comparator = spec["comparator"]
        target = spec["target"]

        p_breach = None if p_val is None else (float(p_val) > target if comparator == "lte" else float(p_val) < target)
        c_breach = None if c_val is None else (float(c_val) > target if comparator == "lte" else float(c_val) < target)

        if p_breach is True and c_breach is True:
            streak = 2
        elif p_breach is True:
            streak = 1
        elif p_breach is False:
            streak = 0
        else:
            streak = None

        breaches[key] = {
            "primary_breached": p_breach,
            "compare_breached": c_breach,
            "breach_streak_windows": streak,
            "target": target,
            "comparator": comparator,
            "severity": _severity_from_breach(comparator, target, p_val),
        }

    overall_severity = "ok"
    if any(item.get("severity") == "critical" for item in breaches.values()):
        overall_severity = "critical"
    elif any(item.get("severity") == "warning" for item in breaches.values()):
        overall_severity = "warning"

    return {
        "deltas": deltas,
        "breaches": breaches,
        "overall_severity": overall_severity,
    }

def _build_slo_metric(name: str, value, target, comparator: str, data_status: str = "measured", unit: str = None):
    item = {
        "name": name,
        "value": value,
        "target": target,
        "comparator": comparator,
        "data_status": data_status,
    }
    if unit:
        item["unit"] = unit

    if data_status != "measured" or value is None:
        item["meeting_target"] = None
    else:
        if comparator == "lte":
            item["meeting_target"] = bool(value <= target)
        elif comparator == "gte":
            item["meeting_target"] = bool(value >= target)
        else:
            item["meeting_target"] = None
    return item


def _build_slo_dashboard_payload(topology: list, inbox_summary: dict, archived_tasks: list = None) -> dict:
    edges = topology if isinstance(topology, list) else []
    by_state = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        state = str(edge.get("state") or "unknown")
        by_state[state] = by_state.get(state, 0) + 1

    total_active = sum(by_state.values())
    failed_active = by_state.get("FAILED", 0)
    waiting_review = by_state.get("WAITING_FOR_REVIEW", 0)
    unread_inbox = int((inbox_summary or {}).get("unread", 0) or 0)
    archived = archived_tasks if isinstance(archived_tasks, list) else []
    completed_archived = []
    recovered_archived = 0
    completed_with_retry = 0
    diagnosis_minutes_samples = []
    for task in archived:
        details = getattr(task, "details", {}) if isinstance(getattr(task, "details", {}), dict) else {}
        lifecycle = details.get("lifecycle", {}) if isinstance(details.get("lifecycle", {}), dict) else {}

        first_failed_at = _parse_iso_datetime(lifecycle.get("first_failed_at"))
        diagnosed_at = _parse_iso_datetime(lifecycle.get("diagnosed_at"))
        if first_failed_at and diagnosed_at and diagnosed_at >= first_failed_at:
            diagnosis_minutes_samples.append((diagnosed_at - first_failed_at).total_seconds() / 60.0)

        status_name = str(getattr(getattr(task, "status", None), "name", "") or "")
        if status_name == "COMPLETED_SUCCESSFULLY":
            completed_archived.append(task)
            if isinstance(lifecycle, dict) and lifecycle.get("recovered_after_failure"):
                recovered_archived += 1
            if int(lifecycle.get("retry_attempts", 0) or 0) > 0:
                completed_with_retry += 1

    completion_latency_samples_ms = []
    for task in completed_archived:
        created_at = getattr(task, "created_at", None)
        terminal_at = None
        details = getattr(task, "details", {}) if isinstance(getattr(task, "details", {}), dict) else {}
        lifecycle = details.get("lifecycle", {}) if isinstance(details.get("lifecycle", {}), dict) else {}
        terminal_iso = lifecycle.get("terminal_at") if isinstance(lifecycle, dict) else None
        terminal_at = _parse_iso_datetime(terminal_iso)
        if terminal_at is None:
            terminal_at = getattr(task, "last_updated_at", None)
        if created_at and terminal_at:
            completion_latency_samples_ms.append(int((terminal_at - created_at).total_seconds() * 1000))

    completion_latency_samples_ms = sorted([x for x in completion_latency_samples_ms if x >= 0])
    completion_latency_p50_ms = None
    if completion_latency_samples_ms:
        completion_latency_p50_ms = completion_latency_samples_ms[len(completion_latency_samples_ms) // 2]

    recovery_rate_pct = None
    retry_rate_pct = None
    if completed_archived:
        recovery_rate_pct = _safe_ratio_percent(recovered_archived, len(completed_archived))
        retry_rate_pct = _safe_ratio_percent(completed_with_retry, len(completed_archived))

    mttd_minutes = None
    if diagnosis_minutes_samples:
        diagnosis_minutes_samples = sorted(diagnosis_minutes_samples)
        mttd_minutes = round(diagnosis_minutes_samples[len(diagnosis_minutes_samples) // 2], 2)

    optional_checks = {
        "playwright": _is_optional_dependency_available('playwright'),
        "chromadb": _is_optional_dependency_available('chromadb'),
        "pyaudio": _is_optional_dependency_available('pyaudio'),
    }
    installed_optional = sum(1 for ok in optional_checks.values() if ok)
    optional_availability_pct = _safe_ratio_percent(installed_optional, len(optional_checks))

    metrics = [
        _build_slo_metric(
            "delegation_failed_active_rate_pct",
            _safe_ratio_percent(failed_active, total_active),
            5.0,
            "lte",
            unit="percent",
        ),
        _build_slo_metric(
            "delegation_review_backlog_rate_pct",
            _safe_ratio_percent(waiting_review, total_active),
            25.0,
            "lte",
            unit="percent",
        ),
        _build_slo_metric(
            "work_inbox_unread_count",
            unread_inbox,
            20,
            "lte",
            unit="count",
        ),
        _build_slo_metric(
            "optional_dependency_availability_pct",
            optional_availability_pct,
            66.0,
            "gte",
            unit="percent",
        ),
        _build_slo_metric("delegated_completion_latency_p50_ms", completion_latency_p50_ms, 15000, "lte", data_status="measured" if completion_latency_p50_ms is not None else "not_instrumented", unit="ms"),
        _build_slo_metric("manual_retry_rate_pct", retry_rate_pct, 10.0, "lte", data_status="measured" if retry_rate_pct is not None else "not_instrumented", unit="percent"),
        _build_slo_metric("failed_delegation_recovery_rate_pct", recovery_rate_pct, 90.0, "gte", data_status="measured" if recovery_rate_pct is not None else "not_instrumented", unit="percent"),
        _build_slo_metric("mean_time_to_diagnose_minutes", mttd_minutes, 30, "lte", data_status="measured" if mttd_minutes is not None else "not_instrumented", unit="minutes"),
    ]

    measured = [m for m in metrics if m.get("data_status") == "measured"]
    measured_meeting = [m for m in measured if m.get("meeting_target") is True]

    return {
        "schema_version": 1,
        "window": "active_snapshot",
        "targets_version": "2026-q2-week7-8-v1",
        "snapshot": {
            "active_delegated_tasks": total_active,
            "by_state": by_state,
            "work_inbox_unread": unread_inbox,
        },
        "metrics": metrics,
        "coverage": {
            "measured_metrics": len(measured),
            "not_instrumented_metrics": len([m for m in metrics if m.get("data_status") != "measured"]),
            "measured_meeting_target": len(measured_meeting),
        },
        "notes": [
            "Completion latency, manual retry rate, failed recovery rate, and MTTD require persisted lifecycle timestamps and retry-event instrumentation.",
            "Current dashboard exposes measurable active-state SLO proxies plus explicit non-instrumented gaps.",
        ],
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
    items = []
    
    if app_globals.orchestrator:
        learning_agent = getattr(app_globals.orchestrator, "learning_agent", None)
        insights = getattr(learning_agent, "insights", None)
        if insights:
            for insight in insights:
                status = str(getattr(insight, "status", "") or "")
                
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
        
                insight_id = str(getattr(insight, "insight_id", "") or "").strip()
                if not insight_id:
                    continue
        
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

    try:
        from ai_assistant.core.suggestion_manager import list_suggestions
        from datetime import datetime
        for sugg in list_suggestions(create_dummy=False):
            created_iso = sugg.get("created_at")
            created_ts = 0.0
            if created_iso:
                try:
                    created_ts = datetime.fromisoformat(created_iso.replace('Z', '+00:00')).timestamp()
                except Exception:
                    pass
            
            insight_id = sugg.get("suggestion_id", "")
            if not insight_id:
                continue

            # Avoid appending if already exists
            if not any(item["insight_id"] == insight_id for item in items):
                items.append({
                    "insight_id": insight_id,
                    "type": sugg.get("type", "UNKNOWN"),
                    "status": sugg.get("status", "UNKNOWN"),
                    "description": sugg.get("description", ""),
                    "created_at": created_ts,
                    "created_at_ts": created_ts,
                    "recommended_specialist_templates": [],
                    "actions": {
                        "approve": f"/api/status/reflection-suggestions/{insight_id}/approve",
                        "reject": f"/api/status/reflection-suggestions/{insight_id}/reject",
                        "spawn_specialist": f"/api/status/reflection-suggestions/{insight_id}/spawn-specialist",
                    }
                })
    except Exception as e:
        logger.error(f"Error loading suggestions for mission control: {e}")

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
    data = request.get_json(silent=True) or {}
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

    data = request.get_json(silent=True) or {}
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


@api_bp.route('/status/dynamic-specialist-proposals', methods=['GET'])
def mission_control_list_dynamic_specialist_proposals():
    """Lists dynamic specialist proposals and their review state."""
    store = _load_dynamic_specialist_store()
    status_filter = str(request.args.get('status') or '').strip().upper()
    items = store.get('proposals', []) if isinstance(store.get('proposals'), list) else []
    if status_filter:
        items = [item for item in items if str(item.get('status', '')).upper() == status_filter]

    limit = _coerce_positive_int(request.args.get('limit'), default=50, maximum=200)
    sorted_items = sorted(items, key=lambda item: str(item.get('created_at', '')), reverse=True)[:limit]
    audit_trail = store.get('audit_trail', []) if isinstance(store.get('audit_trail', []), list) else []
    enriched_items = [
        _serialize_dynamic_proposal_for_mission_control(item, audit_trail=audit_trail)
        for item in sorted_items
    ]

    return jsonify({
        "success": True,
        "schema_version": 1,
        "count": len(enriched_items),
        "items": enriched_items,
        "status_filter": status_filter or None,
        "audit_trail_count": len(audit_trail),
    })


@api_bp.route('/status/dynamic-specialist-proposals', methods=['POST'])
def mission_control_create_dynamic_specialist_proposal():
    """Creates a review-gated dynamic specialist proposal (no automatic spawn)."""
    data = request.get_json(silent=True) or {}
    profile, provenance, error = _validate_dynamic_proposal_payload(data)
    if error:
        return jsonify({"success": False, "error": error}), 400

    store = _load_dynamic_specialist_store()
    proposal_id = f"dsp_{uuid.uuid4().hex[:12]}"
    created_at = _utc_now_iso()
    profile_id = f"dyn_{uuid.uuid4().hex[:12]}"

    proposal = {
        "proposal_id": proposal_id,
        "profile": {
            "profile_id": profile_id,
            **profile,
            "is_dynamic": True,
        },
        "provenance": provenance,
        "status": "PENDING_REVIEW",
        "created_at": created_at,
        "updated_at": created_at,
        "review": None,
    }

    store.setdefault("proposals", []).append(proposal)
    _append_dynamic_proposal_audit_event(
        store,
        proposal_id=proposal_id,
        event_type="PROPOSAL_CREATED",
        actor=provenance.get("requested_by", "unknown"),
        details={"status": "PENDING_REVIEW", "profile_id": profile_id},
    )
    _save_dynamic_specialist_store(store)

    return jsonify({
        "success": True,
        "schema_version": 1,
        "proposal": _serialize_dynamic_proposal_for_mission_control(proposal, audit_trail=store.get("audit_trail", [])),
    }), 202


@api_bp.route('/status/dynamic-specialist-proposals/<proposal_id>/approve', methods=['POST'])
def mission_control_approve_dynamic_specialist_proposal(proposal_id):
    """Approves a pending dynamic specialist proposal without auto-spawning."""
    store = _load_dynamic_specialist_store()
    proposal = _find_dynamic_proposal(store, proposal_id)
    if not proposal:
        return jsonify({"success": False, "error": "Proposal not found"}), 404

    if str(proposal.get("status", "")).upper() != "PENDING_REVIEW":
        return jsonify({"success": False, "error": f"Proposal is not pending review (status={proposal.get('status')})"}), 400

    data = request.get_json(silent=True) or {}
    reviewed_by, error = _normalize_nonempty_string(data.get("reviewed_by"), "reviewed_by")
    if error:
        return jsonify({"success": False, "error": error}), 400

    review_notes = str(data.get("review_notes") or "").strip()
    proposal["status"] = "APPROVED"
    proposal["updated_at"] = _utc_now_iso()
    proposal["review"] = {
        "decision": "APPROVED",
        "reviewed_by": reviewed_by,
        "review_notes": review_notes,
        "reviewed_at": _utc_now_iso(),
    }

    _append_dynamic_proposal_audit_event(
        store,
        proposal_id=proposal_id,
        event_type="PROPOSAL_APPROVED",
        actor=reviewed_by,
        details={"review_notes": review_notes},
    )
    _save_dynamic_specialist_store(store)

    return jsonify({"success": True, "proposal": _serialize_dynamic_proposal_for_mission_control(proposal, audit_trail=store.get("audit_trail", [])), "auto_spawned": False})


@api_bp.route('/status/dynamic-specialist-proposals/<proposal_id>/reject', methods=['POST'])
def mission_control_reject_dynamic_specialist_proposal(proposal_id):
    """Rejects a pending dynamic specialist proposal and records reviewer metadata."""
    store = _load_dynamic_specialist_store()
    proposal = _find_dynamic_proposal(store, proposal_id)
    if not proposal:
        return jsonify({"success": False, "error": "Proposal not found"}), 404

    if str(proposal.get("status", "")).upper() != "PENDING_REVIEW":
        return jsonify({"success": False, "error": f"Proposal is not pending review (status={proposal.get('status')})"}), 400

    data = request.get_json(silent=True) or {}
    reviewed_by, error = _normalize_nonempty_string(data.get("reviewed_by"), "reviewed_by")
    if error:
        return jsonify({"success": False, "error": error}), 400
    rejection_reason, error = _normalize_nonempty_string(data.get("rejection_reason"), "rejection_reason")
    if error:
        return jsonify({"success": False, "error": error}), 400

    proposal["status"] = "REJECTED"
    proposal["updated_at"] = _utc_now_iso()
    proposal["review"] = {
        "decision": "REJECTED",
        "reviewed_by": reviewed_by,
        "rejection_reason": rejection_reason,
        "reviewed_at": _utc_now_iso(),
    }

    _append_dynamic_proposal_audit_event(
        store,
        proposal_id=proposal_id,
        event_type="PROPOSAL_REJECTED",
        actor=reviewed_by,
        details={"rejection_reason": rejection_reason},
    )
    _save_dynamic_specialist_store(store)

    return jsonify({"success": True, "proposal": _serialize_dynamic_proposal_for_mission_control(proposal, audit_trail=store.get("audit_trail", [])), "auto_spawned": False})



@api_bp.route('/status/slo-dashboard', methods=['GET'])
def mission_control_slo_dashboard():
    """Returns Mission Control SLO targets + current measured status and instrumentation gaps."""
    if not app_globals.orchestrator:
        return jsonify({"error": "System starting up...", "success": False}), 503

    try:
        active_tasks = app_globals.orchestrator.task_manager.list_active_tasks()
        snapshot = get_status_snapshot(active_tasks_count=len(active_tasks), active_tasks=active_tasks)
        topology = snapshot.get('delegation_topology', []) if isinstance(snapshot, dict) else []
        inbox_summary = _get_work_inbox_summary()
        archived_tasks_loader = getattr(app_globals.orchestrator.task_manager, "list_archived_tasks", None)
        archived_tasks = archived_tasks_loader(limit=500) if callable(archived_tasks_loader) else []
        payload = _build_slo_dashboard_payload(topology=topology, inbox_summary=inbox_summary, archived_tasks=archived_tasks)

        generated_at = datetime.now(timezone.utc)
        return jsonify({
            "success": True,
            "generated_at": generated_at.isoformat(),
            "generated_at_ms": int(generated_at.timestamp() * 1000),
            "slo": payload,
        })
    except Exception as e:
        logger.error(f"Error generating SLO dashboard: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route('/status/slo-trends', methods=['GET'])
def mission_control_slo_trends():
    """Returns rolling-window SLO metrics derived from archived task lifecycle data."""
    if not app_globals.orchestrator:
        return jsonify({"error": "System starting up...", "success": False}), 503

    try:
        tm = app_globals.orchestrator.task_manager
        archived_loader = getattr(tm, "list_archived_tasks", None)
        archived_tasks = archived_loader(limit=5000) if callable(archived_loader) else []

        now_dt = datetime.now(timezone.utc)
        primary_window = _coerce_window_hours(request.args.get('window_hours'), default=24)
        compare_window = _coerce_window_hours(request.args.get('compare_window_hours'), default=24 * 7)

        primary = _build_archived_slo_window_metrics(archived_tasks, window_hours=primary_window, now_dt=now_dt)
        compare = _build_archived_slo_window_metrics(archived_tasks, window_hours=compare_window, now_dt=now_dt)

        analysis = _compute_slo_trend_analysis(primary.get("metrics", {}), compare.get("metrics", {}))

        return jsonify({
            "success": True,
            "schema_version": 1,
            "generated_at": now_dt.isoformat(),
            "windows": {
                "primary": primary,
                "compare": compare,
            },
            "analysis": analysis,
        })
    except Exception as e:
        logger.error(f"Error generating SLO trends: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route('/status/token-dashboard', methods=['GET'])
def mission_control_token_dashboard():
    """Returns token usage trends + budget state for Mission Control controls."""
    window_hours = _coerce_window_hours(request.args.get('window_hours'), default=24)
    history_limit = _coerce_positive_int(request.args.get('history_limit'), default=500, maximum=2000)
    payload = _build_token_dashboard_payload(window_hours=window_hours, history_limit=history_limit)

    generated_at = datetime.now(timezone.utc)
    return jsonify({
        "success": True,
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "generated_at_ms": int(generated_at.timestamp() * 1000),
        "token_dashboard": payload,
    })


@api_bp.route('/status/token-budget', methods=['GET'])
def mission_control_token_budget_get():
    """Returns persisted token budget controls for Mission Control operators."""
    settings = _load_token_budget_settings()
    return jsonify({"success": True, "schema_version": 1, "settings": settings})


@api_bp.route('/status/token-budget', methods=['POST'])
def mission_control_token_budget_update():
    """Updates token budget controls used by token dashboard and policy surfaces."""
    data = request.get_json(silent=True) or {}
    settings = _load_token_budget_settings()

    if "default_daily_budget_usd" in data:
        settings["default_daily_budget_usd"] = _coerce_non_negative_float(
            data.get("default_daily_budget_usd"),
            _coerce_non_negative_float(settings.get("default_daily_budget_usd"), 1.0),
        )

    if "hard_stop_enabled" in data:
        settings["hard_stop_enabled"] = bool(data.get("hard_stop_enabled"))

    incoming_category_budgets = data.get("category_budgets") if isinstance(data.get("category_budgets"), dict) else None
    if incoming_category_budgets is not None:
        current = settings.get("category_budgets") if isinstance(settings.get("category_budgets"), dict) else {}
        for category, raw_value in incoming_category_budgets.items():
            category_key = str(category or "").strip().lower()
            if category_key not in _ALLOWED_TOKEN_BUDGET_CATEGORIES:
                continue
            current[category_key] = _coerce_non_negative_float(raw_value, _coerce_non_negative_float(current.get(category_key), 0.0))
        settings["category_budgets"] = current

    _save_token_budget_settings(settings)
    return jsonify({"success": True, "schema_version": 1, "settings": settings})


@api_bp.route('/status/token-budget/preflight', methods=['POST'])
def mission_control_token_budget_preflight():
    """Evaluates whether projected spend fits current token budget policy."""
    data = request.get_json(silent=True) or {}
    category = str(data.get("category") or TOKEN_CATEGORY_GENERAL).strip().lower()
    projected_cost_usd = _coerce_non_negative_float(data.get("projected_cost_usd"), 0.0)
    window_hours = _coerce_window_hours(data.get("window_hours"), default=24)

    evaluation = _evaluate_token_budget_preflight(
        category=category,
        projected_cost_usd=projected_cost_usd,
        window_hours=window_hours,
    )
    return jsonify({
        "success": True,
        "schema_version": 1,
        "evaluation": evaluation,
    })


@api_bp.route('/status/operator-policy', methods=['GET'])
def mission_control_operator_policy_get():
    """Returns policy toggles controlling dream mode and host automation guardrails."""
    settings = _load_operator_policy_settings()
    return jsonify({"success": True, "schema_version": 1, "settings": settings})


@api_bp.route('/status/operator-policy', methods=['POST'])
def mission_control_operator_policy_update():
    """Updates operator policy toggles and host automation allowlist controls."""
    data = request.get_json(silent=True) or {}
    settings = _load_operator_policy_settings()

    for key in ("dream_mode_enabled", "host_automation_enabled", "host_automation_kill_switch", "require_provenance"):
        if key in data:
            settings[key] = bool(data.get(key))

    if "allowlisted_roots" in data:
        settings["allowlisted_roots"] = _normalize_allowlisted_roots(data.get("allowlisted_roots"))

    _save_operator_policy_settings(settings)
    return jsonify({"success": True, "schema_version": 1, "settings": settings})


@api_bp.route('/status/host-automation/preflight', methods=['POST'])
def mission_control_host_automation_preflight():
    """Evaluates whether a host automation request passes current policy controls."""
    data = request.get_json(silent=True) or {}
    policy = _load_operator_policy_settings()
    evaluation = _evaluate_host_automation_preflight(policy, data)

    return jsonify({
        "success": True,
        "schema_version": 1,
        "evaluation": evaluation,
    })


@api_bp.route('/status/execution-preflight', methods=['POST'])
def mission_control_execution_preflight():
    """Evaluates combined token budget + host automation policy before execution."""
    data = request.get_json(silent=True) or {}
    evaluation = _evaluate_execution_preflight(data)
    return jsonify({
        "success": True,
        "schema_version": 1,
        "evaluation": evaluation,
    })



def _build_next_sprint_payload() -> dict:
    """Returns a structured, operator-facing next sprint plan."""
    return {
        "sprint_id": "MILESTONE-D-TOKEN-GOVERNANCE-01",
        "title": "Mission Control token guardrails + host automation policy gates",
        "focus": [
            "Convert token-dashboard insights into enforceable runtime guardrails.",
            "Ship policy-gated host automation controls for safe desktop-level operations.",
        ],
        "deliverables": [
            {
                "id": "D1",
                "name": "Budget enforcement hooks",
                "details": "Apply per-category budget checks before high-cost autonomous/research actions and emit budget events.",
            },
            {
                "id": "D2",
                "name": "Mission Control policy controls",
                "details": "Expose enable/disable toggles for dream mode and host automation kill switch in status API payloads.",
            },
            {
                "id": "D3",
                "name": "Desktop scope guardrails",
                "details": "Add allowlisted roots + provenance fields for filesystem automation requests.",
            },
        ],
        "acceptance_checks": [
            "Budget-overrun requests return deterministic policy responses.",
            "All host automation actions include actor + rationale + timestamp metadata.",
            "Mission Control snapshot surfaces current policy toggle state.",
        ],
    }


@api_bp.route('/status/next-sprint', methods=['GET'])
def mission_control_next_sprint():
    """Returns the prioritized next sprint plan after the current delivery round."""
    generated_at = datetime.now(timezone.utc)
    return jsonify({
        "success": True,
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "generated_at_ms": int(generated_at.timestamp() * 1000),
        "next_sprint": _build_next_sprint_payload(),
    })


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
    operator_policy = _load_operator_policy_settings()

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
        "operator_policy": {
            "dream_mode_enabled": bool(operator_policy.get("dream_mode_enabled", True)),
            "host_automation_enabled": bool(operator_policy.get("host_automation_enabled", False)),
            "host_automation_kill_switch": bool(operator_policy.get("host_automation_kill_switch", True)),
            "require_provenance": bool(operator_policy.get("require_provenance", True)),
            "allowlisted_roots": _normalize_allowlisted_roots(operator_policy.get("allowlisted_roots")),
        },
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
    required_fields = list(AGENT_SCOPE_REQUIRED_FIELDS)
    allowed_scope_types = set(AGENT_SCOPE_ALLOWED_SCOPE_TYPES)

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
        invalid_scope = bool(scope) and scope not in allowed_scope_types
        invalid_capability = bool(capability) and capability not in AGENT_SCOPE_ALLOWED_CAPABILITY_PROFILES
        invalid_retention = bool(retention) and retention not in AGENT_SCOPE_ALLOWED_RETENTION_POLICIES

        if not missing and not invalid_scope and not invalid_capability and not invalid_retention:
            compliant_edges += 1
            continue

        violations.append({
            "task_id": str(edge.get("task_id", "")),
            "worker_profile": worker,
            "missing_fields": missing,
            "invalid_scope_type": scope if invalid_scope else None,
            "invalid_capability_profile": capability if invalid_capability else None,
            "invalid_retention_policy": retention if invalid_retention else None,
        })

    violation_count = len(violations)
    return {
        "total_edges": total_edges,
        "compliant_edges": compliant_edges,
        "violation_count": violation_count,
        "compliance_rate": (compliant_edges / total_edges) if total_edges else 1.0,
        "required_fields": required_fields,
        "allowed_scope_types": sorted(allowed_scope_types),
        "allowed_capability_profiles": sorted(AGENT_SCOPE_ALLOWED_CAPABILITY_PROFILES),
        "allowed_retention_policies": sorted(AGENT_SCOPE_ALLOWED_RETENTION_POLICIES),
        "contract_version": AGENT_SCOPE_CONTRACT_VERSION,
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
        'contract_version': AGENT_SCOPE_CONTRACT_VERSION,
        'required_fields': list(AGENT_SCOPE_REQUIRED_FIELDS),
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

        # 2. Source-changing architect proposals require a human click in this UI.
        from ai_assistant.goals.goal_management import list_goals
        for goal in list_goals(status="PENDING_APPROVAL"):
            if goal.get("metadata", {}).get("type") != "architect_source_change":
                continue
            serialized_requests.append({
                "id": goal["id"],
                "type": "architect_source_change",
                "description": goal.get("description") or goal.get("title"),
                "timestamp": goal.get("metadata", {}).get("created_at", 0),
                "data": serialize_approval_data(goal),
                "source": "goal_management",
            })

        # 3. Get persistent 'NEW' insights from LearningAgent
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
def approve_request(req_id):
    """Approves a request (either transient or persistent insight)."""
    try:
        feedback = request.json.get('feedback') if request.json else None

        # 1. Try ApprovalManager first
        if approval_manager.get_request(req_id):
            if feedback:
                logger.info(f"User approved request {req_id} with feedback: {feedback}")
            success = _run_async(approval_manager.approve_request(req_id))
            if success: return jsonify({"success": True})

        # 2. Architect proposals can only be released from this user-facing route.
        from ai_assistant.custom_tools.agent_tools import _approve_source_change_proposal_from_ui
        from ai_assistant.goals.goal_management import get_goal
        goal = get_goal(req_id)
        if goal and goal.get("metadata", {}).get("type") == "architect_source_change":
            result = _approve_source_change_proposal_from_ui(req_id)
            if result.startswith("Approved "):
                return jsonify({"success": True, "message": result})
            return jsonify({"success": False, "error": result}), 400

        # 3. Try LearningAgent Insights
        if app_globals.orchestrator and app_globals.orchestrator.learning_agent:
            insight = next((i for i in app_globals.orchestrator.learning_agent.insights if i.insight_id == req_id), None)
            if insight:
                if feedback:
                    if not insight.metadata: insight.metadata = {}
                    insight.metadata['user_feedback_on_approval'] = feedback
                    app_globals.memory_manager.add_fact(f"User Approved Insight {req_id} with feedback: {feedback}")

                if insight.type in [InsightType.TOOL_BUG_SUSPECTED, InsightType.TOOL_ENHANCEMENT_SUGGESTED]:
                     success = _run_async(
                         app_globals.orchestrator.learning_agent.execute_self_healing_for_insight(
                             insight,
                             apply_immediately=True,
                         )
                     )
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

        # 2. Denying an architect proposal is also an explicit human UI action.
        from ai_assistant.goals.goal_management import get_goal, update_goal_status
        goal = get_goal(req_id)
        if goal and goal.get("metadata", {}).get("type") == "architect_source_change":
            if update_goal_status(req_id, "failed"):
                return jsonify({"success": True})
            return jsonify({"success": False, "error": "Could not deny architect proposal."}), 500

        # 3. Try LearningAgent Insights
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
