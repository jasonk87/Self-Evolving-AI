from typing import Any, Dict, Tuple

CONTRACT_VERSION = "2026-03-r3-v1"

REQUIRED_FIELDS = ("scope_type", "capability_profile", "retention_policy", "worker_profile")

ALLOWED_SCOPE_TYPES = {"session", "user"}
ALLOWED_CAPABILITY_PROFILES = {
    "workspace_code_generation",
    "review_only",
    "ops_diagnostics",
}
ALLOWED_RETENTION_POLICIES = {
    "drop_task_memory_on_completion_keep_artifacts",
    "keep_summary_only",
    "retain_user_profile_with_provenance",
}

DEFAULT_SCOPE_TYPE = "session"
DEFAULT_CAPABILITY_PROFILE = "workspace_code_generation"
DEFAULT_RETENTION_POLICY = "drop_task_memory_on_completion_keep_artifacts"

_SCOPE_ALIASES = {
    "session": "session",
    "sess": "session",
    "user": "user",
    "personal": "user",
}
_CAPABILITY_ALIASES = {
    "workspace_code_generation": "workspace_code_generation",
    "code_generation": "workspace_code_generation",
    "codegen": "workspace_code_generation",
    "review_only": "review_only",
    "review": "review_only",
    "ops_diagnostics": "ops_diagnostics",
    "ops": "ops_diagnostics",
}
_RETENTION_ALIASES = {
    "drop_task_memory_on_completion_keep_artifacts": "drop_task_memory_on_completion_keep_artifacts",
    "keep_artifacts_only": "drop_task_memory_on_completion_keep_artifacts",
    "keep_summary_only": "keep_summary_only",
    "retain_user_profile_with_provenance": "retain_user_profile_with_provenance",
}


def _normalize(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _coerce_contract_field(value: Any, aliases: Dict[str, str], default_value: str) -> Tuple[str, str]:
    normalized = _normalize(value)
    if not normalized:
        return default_value, "defaulted"
    coerced = aliases.get(normalized)
    if not coerced:
        return "", "invalid"
    if coerced != normalized:
        return coerced, "coerced"
    return coerced, "explicit"


def normalize_and_validate_agent_policy(details: Dict[str, Any], strict: bool = True) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    normalized = dict(details or {})
    issues = []
    coercions = []

    scope, scope_mode = _coerce_contract_field(normalized.get("scope_type"), _SCOPE_ALIASES, DEFAULT_SCOPE_TYPE)
    capability, capability_mode = _coerce_contract_field(
        normalized.get("capability_profile"), _CAPABILITY_ALIASES, DEFAULT_CAPABILITY_PROFILE
    )
    retention, retention_mode = _coerce_contract_field(
        normalized.get("retention_policy"), _RETENTION_ALIASES, DEFAULT_RETENTION_POLICY
    )

    if not scope:
        issues.append("scope_type")
    if not capability:
        issues.append("capability_profile")
    if not retention:
        issues.append("retention_policy")

    if scope and scope not in ALLOWED_SCOPE_TYPES:
        issues.append("scope_type")
    if capability and capability not in ALLOWED_CAPABILITY_PROFILES:
        issues.append("capability_profile")
    if retention and retention not in ALLOWED_RETENTION_POLICIES:
        issues.append("retention_policy")

    worker_profile = str(normalized.get("worker_profile") or "").strip()
    if not worker_profile:
        issues.append("worker_profile")

    if scope_mode == "coerced":
        coercions.append("scope_type")
    if capability_mode == "coerced":
        coercions.append("capability_profile")
    if retention_mode == "coerced":
        coercions.append("retention_policy")

    normalized["scope_type"] = scope
    normalized["capability_profile"] = capability
    normalized["retention_policy"] = retention

    metadata = {
        "contract_version": CONTRACT_VERSION,
        "coercions": coercions,
        "issues": sorted(set(issues)),
    }

    if strict and metadata["issues"]:
        raise ValueError(f"Agent scope contract validation failed: {', '.join(metadata['issues'])}")

    return normalized, metadata
