"""Where should the next self-audit look? A heatmap of Weebo's own source.

A file is hot when it changed since it was last audited, was never audited, hasn't been looked at in a while,
or sits in an area with open recorded failures. Each audit takes the hottest area and starts with its hottest
files, then marks those files audited, so attention follows change and evidence instead of a fixed rotation.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import paths

if TYPE_CHECKING:
    from ..app import WeeboApp

HEATMAP_KEY = "audit_heatmap"
FILES_PER_AUDIT = 8
SOURCE_SUFFIXES = (".py", ".js", ".mjs", ".css", ".html")

AREAS: dict[str, tuple[str, str]] = {
    "codex": ("weebo/codex/", "reliability of the Codex engine integration (weebo/codex)"),
    "brain": ("weebo/brain/", "conversation streaming, the persona and Weebo's tools (weebo/brain)"),
    "web": ("weebo/web/", "the web UI (weebo/web): broken interactions, missing error states, accessibility, mobile layout"),
    "proactive": ("weebo/proactive/", "proactive features: heartbeat, dreams, scheduler and routines (weebo/proactive)"),
    "memory": ("weebo/memory/", "memory quality and recall (weebo/memory)"),
    "agents": ("weebo/agents/", "background agents (weebo/agents)"),
    "server": ("weebo/server/", "the HTTP/WebSocket server (weebo/server)"),
    "integrations": ("weebo/integrations/", "integrations: search, research, calendar, SMS, widgets (weebo/integrations)"),
    "evolution": ("weebo/evolution/", "self-evolution: gates, evals, Council and merge safety (weebo/evolution)"),
    "core": ("weebo/", "app wiring, settings, storage and the supervisor (weebo/*.py)"),
}

# Which area a recorded failure points at.
FAILURE_AREAS = {
    "turn_failed": "brain", "turn_start_failed": "codex", "tool_crash": "brain", "agent_crash": "agents",
    "agent_failed": "agents", "api_error": "server", "integration_failed": "integrations",
    "evolution_failed": "evolution", "dream_crash": "proactive", "audit_crash": "proactive",
    "brief_crash": "proactive", "eval_crash": "evolution",
}


def area_of(path: str) -> str:
    for name, (prefix, _) in AREAS.items():
        if name != "core" and path.startswith(prefix):
            return name
    return "core"


def source_files(root: Path | None = None) -> dict[str, float]:
    """{relative path: mtime} for Weebo's own source (vendored libraries and assets excluded)."""
    root = root or paths.PROJECT_ROOT
    files = {}
    for path in (root / "weebo").rglob("*"):
        rel = path.relative_to(root).as_posix()
        if (path.is_file() and path.suffix in SOURCE_SUFFIXES and "/vendor/" not in rel
                and "__pycache__" not in rel and "/tailnet_helper/" not in rel):
            try:
                files[rel] = path.stat().st_mtime
            except OSError:
                continue
    return files


def heat(mtime: float, audited_at: float | None, now: float) -> float:
    if not audited_at:
        return 3.0
    score = min((now - audited_at) / (7 * 86400), 2.0)  # staleness: up to 2 after two weeks
    if mtime > audited_at:
        score += 3.0  # changed since anyone last looked
    return score


def pick(app: "WeeboApp", issues: list[dict[str, Any]], root: Path | None = None) -> dict[str, Any]:
    """Choose the area and files for the next audit. Returns {"area", "focus", "files"}."""
    now = time.time()
    audited: dict[str, float] = app.store.kv_get(HEATMAP_KEY, {}) or {}
    by_area: dict[str, list[tuple[float, str]]] = {}
    for rel, mtime in source_files(root).items():
        by_area.setdefault(area_of(rel), []).append((heat(mtime, audited.get(rel), now), rel))
    failures: dict[str, int] = {}
    for issue in issues:
        area = FAILURE_AREAS.get(issue.get("kind", ""))
        if area:
            failures[area] = failures.get(area, 0) + min(int(issue.get("count", 1)), 5)
    best, best_score = "core", -1.0
    for area, scored in by_area.items():
        scored.sort(reverse=True)
        score = sum(h for h, _ in scored[:5]) + 2.0 * failures.get(area, 0)
        if score > best_score:
            best, best_score = area, score
    files = [rel for _, rel in by_area.get(best, [])[:FILES_PER_AUDIT]]
    return {"area": best, "focus": AREAS[best][1], "files": files, "failures": failures.get(best, 0)}


def mark_audited(app: "WeeboApp", files: list[str]) -> None:
    audited: dict[str, float] = app.store.kv_get(HEATMAP_KEY, {}) or {}
    now = time.time()
    audited.update({rel: now for rel in files})
    app.store.kv_set(HEATMAP_KEY, audited)
