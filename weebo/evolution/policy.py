"""Change policy: which files Weebo may change about itself without a human.

Every file a self-evolution build touches is classified into a zone, and the zone decides the tier:

* autonomous: UI, tests, docs and runtime data. Eligible for automatic merge in ``auto_merge`` mode.
* human_required: core, execution and governance code. Built and verified automatically, merged only by the user.
* blocked: anything unrecognised. Routed to the user rather than guessed at.

Governance covers the code that decides what Weebo may change (this module, the evolution engine, the
supervisor that rolls back bad upgrades) and the configuration that decides what "verified" means (CI,
pytest config). Dependency manifests are core: a new dependency is a new trust decision.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ChangeAction(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class ChangeZone(str, Enum):
    CORE_SOURCE = "core_source"
    GOVERNANCE_SOURCE = "governance_source"
    EXECUTION_SOURCE = "execution_source"
    UI_SOURCE = "ui_source"
    TEST_SOURCE = "test_source"
    DOCS = "docs"
    RUNTIME_STATE = "runtime_state"
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
    reason: str

    @property
    def requires_human_approval(self) -> bool:
        return self.tier != GovernanceTier.AUTONOMOUS


_GOVERNANCE_PREFIXES = (
    ("weebo", "evolution"),
    ("weebo", "supervisor.py"),
    ("weebo", "selftest.py"),
    ("weebo", "brain", "interactions.py"),
    ("weebo", "brain", "tools.py"),
    ("tests", "conftest.py"),
    ("tests", "weebo", "conftest.py"),
    (".github",),
)
_GOVERNANCE_FILES = {"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "conftest.py"}
_EXECUTION_PREFIXES = (
    ("weebo", "codex"),
    ("weebo", "agents"),
    ("weebo", "integrations"),
)
_CORE_FILES = {"Weebo.bat", "weebo.sh", ".gitignore", ".env.example"}

_HUMAN_REASON = "Core, execution and governance code is built and verified automatically but merged only by the user."


def _parts(path: str | os.PathLike[str], project_root: str | os.PathLike[str] | None = None) -> tuple[str, ...]:
    raw = Path(path)
    if project_root and raw.is_absolute():
        try:
            raw = raw.resolve().relative_to(Path(project_root).resolve())
        except ValueError:
            pass
    return tuple(part for part in raw.as_posix().strip("/").split("/") if part and part != ".")


def _has_prefix(parts: tuple[str, ...], prefixes: tuple[tuple[str, ...], ...]) -> bool:
    return any(parts[: len(prefix)] == prefix for prefix in prefixes)


def classify_change_zone(path: str | os.PathLike[str],
                         project_root: str | os.PathLike[str] | None = None) -> ChangeZone:
    parts = _parts(path, project_root)
    if not parts or ".." in parts:
        return ChangeZone.UNKNOWN
    filename = parts[-1]
    if parts[0] == "weebo_data":
        return ChangeZone.RUNTIME_STATE
    if _has_prefix(parts, _GOVERNANCE_PREFIXES) or filename in _GOVERNANCE_FILES:
        return ChangeZone.GOVERNANCE_SOURCE
    if filename.startswith("requirements") and filename.endswith(".txt"):
        return ChangeZone.CORE_SOURCE
    if parts[0] == "tests":
        return ChangeZone.TEST_SOURCE
    if parts[0] == "docs" or filename.lower().endswith((".md", ".rst")):
        return ChangeZone.DOCS
    if parts[:2] == ("weebo", "web"):
        return ChangeZone.UI_SOURCE
    if _has_prefix(parts, _EXECUTION_PREFIXES):
        return ChangeZone.EXECUTION_SOURCE
    if parts[0] == "weebo" or (len(parts) == 1 and filename in _CORE_FILES):
        return ChangeZone.CORE_SOURCE
    return ChangeZone.UNKNOWN


def decide_governance(path: str | os.PathLike[str], action: ChangeAction | str = ChangeAction.MODIFY,
                      project_root: str | os.PathLike[str] | None = None) -> GovernanceDecision:
    change = action if isinstance(action, ChangeAction) else ChangeAction(str(action).lower())
    zone = classify_change_zone(path, project_root)
    if zone == ChangeZone.UNKNOWN:
        return GovernanceDecision(zone, change, GovernanceTier.BLOCKED,
                                  "Unknown change zone; routed to the user instead of guessing.")
    if zone in (ChangeZone.CORE_SOURCE, ChangeZone.GOVERNANCE_SOURCE, ChangeZone.EXECUTION_SOURCE):
        return GovernanceDecision(zone, change, GovernanceTier.HUMAN_REQUIRED, _HUMAN_REASON)
    if change == ChangeAction.DELETE and zone != ChangeZone.RUNTIME_STATE:
        return GovernanceDecision(zone, change, GovernanceTier.HUMAN_REQUIRED,
                                  "Deleting durable files (including tests) needs the user's approval.")
    return GovernanceDecision(zone, change, GovernanceTier.AUTONOMOUS,
                              "Low-risk zone; may merge automatically once every gate passes.")
