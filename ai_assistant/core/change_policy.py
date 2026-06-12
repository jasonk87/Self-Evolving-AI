from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable


class ChangeAction(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    DISABLE = "disable"
    QUARANTINE = "quarantine"
    CONFIGURE = "configure"


class ChangeZone(str, Enum):
    CORE_SOURCE = "core_source"
    GOVERNANCE_SOURCE = "governance_source"
    EXECUTION_SOURCE = "execution_source"
    TOOL_SOURCE = "tool_source"
    GENERATED_TOOL = "generated_tool"
    DYNAMIC_SPECIALIST = "dynamic_specialist"
    GENERATED_PROJECT = "generated_project"
    RUNTIME_STATE = "runtime_state"
    TEST_SOURCE = "test_source"
    DOCS = "docs"
    UNKNOWN = "unknown"


class GovernanceTier(str, Enum):
    AUTONOMOUS = "autonomous"
    HUMAN_REQUIRED = "human_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class GovernanceDecision:
    zone: ChangeZone
    action: ChangeAction
    tier: GovernanceTier
    requires_human_approval: bool
    allowed_autonomous: bool
    reason: str
    required_gates: tuple[str, ...]


_CORE_SOURCE_PREFIXES = (
    ("ai_assistant", "core"),
    ("ai_assistant", "code_services"),
    ("ai_assistant", "code_synthesis"),
    ("ai_assistant", "llm_interface"),
    ("routes",),
    ("static", "js"),
)

_GOVERNANCE_SOURCE_PARTS = {
    "approval_manager.py",
    "change_policy.py",
    "critical_reviewer.py",
    "reviewer.py",
    "self_modification.py",
    "judge.py",
    "constitution.py",
}

_EXECUTION_SOURCE_PREFIXES = (
    ("ai_assistant", "execution"),
    ("ai_assistant", "tools"),
)

_AUTONOMOUS_GATES = (
    "worthiness",
    "judge",
    "review",
    "syntax_or_import_smoke",
    "scorecard",
)

_HUMAN_GATES = (
    "worthiness",
    "judge",
    "council",
    "review",
    "verification",
    "human_approval",
    "scorecard",
)


def _parts(
    path: str | os.PathLike[str],
    project_root: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    raw = Path(path)
    if project_root:
        try:
            raw = raw.resolve().relative_to(Path(project_root).resolve())
        except ValueError:
            pass
    normalized = raw.as_posix().strip("/")
    return tuple(part for part in normalized.split("/") if part and part != ".")


def _has_prefix(parts: tuple[str, ...], prefixes: Iterable[tuple[str, ...]]) -> bool:
    return any(parts[: len(prefix)] == prefix for prefix in prefixes)


def classify_change_zone(
    path: str | os.PathLike[str],
    project_root: str | os.PathLike[str] | None = None,
) -> ChangeZone:
    parts = _parts(path, project_root)
    if not parts:
        return ChangeZone.UNKNOWN

    filename = parts[-1]

    if parts[:3] == ("ai_assistant", "core", "data"):
        return ChangeZone.RUNTIME_STATE
    if parts[:3] == ("ai_assistant", "custom_tools", "generated"):
        return ChangeZone.GENERATED_TOOL
    if parts[:2] == ("ai_assistant", "custom_tools") and filename.startswith(
        "dynamic_specialist_"
    ):
        return ChangeZone.DYNAMIC_SPECIALIST
    if parts[:2] == ("ai_assistant", "ai_generated_projects"):
        return ChangeZone.GENERATED_PROJECT
    if parts[0] == "tests":
        return ChangeZone.TEST_SOURCE
    if parts[0] == "docs" or filename.lower().endswith((".md", ".rst", ".txt")):
        return ChangeZone.DOCS
    if filename in _GOVERNANCE_SOURCE_PARTS or "safety" in parts:
        return ChangeZone.GOVERNANCE_SOURCE
    if _has_prefix(parts, _EXECUTION_SOURCE_PREFIXES):
        return ChangeZone.EXECUTION_SOURCE
    if parts[:2] == ("ai_assistant", "custom_tools"):
        return ChangeZone.TOOL_SOURCE
    if _has_prefix(parts, _CORE_SOURCE_PREFIXES):
        return ChangeZone.CORE_SOURCE

    return ChangeZone.UNKNOWN


def decide_governance(
    path: str | os.PathLike[str],
    action: ChangeAction | str = ChangeAction.MODIFY,
    project_root: str | os.PathLike[str] | None = None,
) -> GovernanceDecision:
    change_action = (
        action
        if isinstance(action, ChangeAction)
        else ChangeAction(str(action).lower())
    )
    zone = classify_change_zone(path, project_root=project_root)

    if zone == ChangeZone.UNKNOWN:
        return GovernanceDecision(
            zone=zone,
            action=change_action,
            tier=GovernanceTier.BLOCKED,
            requires_human_approval=True,
            allowed_autonomous=False,
            reason="Unknown change zone; route to human review instead of guessing.",
            required_gates=("human_review", "scorecard"),
        )

    if change_action == ChangeAction.DELETE:
        if zone in {ChangeZone.RUNTIME_STATE, ChangeZone.GENERATED_PROJECT}:
            return GovernanceDecision(
                zone=zone,
                action=change_action,
                tier=GovernanceTier.AUTONOMOUS,
                requires_human_approval=False,
                allowed_autonomous=True,
                reason=(
                    "Disposable runtime/generated project cleanup may run "
                    "autonomously with audit."
                ),
                required_gates=("judge", "retention_or_rollback", "scorecard"),
            )
        return GovernanceDecision(
            zone=zone,
            action=change_action,
            tier=GovernanceTier.HUMAN_REQUIRED,
            requires_human_approval=True,
            allowed_autonomous=False,
            reason=(
                "Durable code deletion requires human approval; quarantine or "
                "disable first."
            ),
            required_gates=_HUMAN_GATES,
        )

    if change_action in {ChangeAction.DISABLE, ChangeAction.QUARANTINE}:
        return GovernanceDecision(
            zone=zone,
            action=change_action,
            tier=GovernanceTier.AUTONOMOUS,
            requires_human_approval=False,
            allowed_autonomous=True,
            reason="Disabling or quarantining is reversible and preserves evidence.",
            required_gates=("judge", "audit_log", "scorecard"),
        )

    if zone in {
        ChangeZone.CORE_SOURCE,
        ChangeZone.GOVERNANCE_SOURCE,
        ChangeZone.EXECUTION_SOURCE,
    }:
        return GovernanceDecision(
            zone=zone,
            action=change_action,
            tier=GovernanceTier.HUMAN_REQUIRED,
            requires_human_approval=True,
            allowed_autonomous=False,
            reason=(
                "Base, governance, and execution code can be prepared "
                "autonomously but require human approval to apply."
            ),
            required_gates=_HUMAN_GATES,
        )

    return GovernanceDecision(
        zone=zone,
        action=change_action,
        tier=GovernanceTier.AUTONOMOUS,
        requires_human_approval=False,
        allowed_autonomous=True,
        reason=(
            "Scoped generated/tool/runtime change may proceed autonomously "
            "when required gates pass."
        ),
        required_gates=_AUTONOMOUS_GATES,
    )
