import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ai_assistant.config import get_data_dir


PATCH_MEMORY_FILENAME = "patch_memory.json"
MAX_LESSONS = 500


def get_patch_memory_path() -> str:
    return os.path.join(get_data_dir(), PATCH_MEMORY_FILENAME)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_lessons() -> List[Dict[str, Any]]:
    path = get_patch_memory_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except Exception as exc:  # pragma: no cover
        print(f"PatchMemory: failed to load lessons: {exc}")
    return []


def _save_lessons(lessons: List[Dict[str, Any]]) -> None:
    if "pytest" in sys.modules and os.environ.get("PATCH_MEMORY_ALLOW_PYTEST") != "1":
        return

    path = get_patch_memory_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bounded = lessons[-MAX_LESSONS:]
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(bounded, handle, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)


def _normalize_lesson_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold())
    return " ".join(normalized.split())


def _normalize_applies_to(applies_to: List[str]) -> List[str]:
    normalized = sorted({str(item).strip().casefold() for item in applies_to if str(item).strip()})
    return normalized or ["general"]


def _lesson_key(failure_class: str, applies_to: List[str], problem: str, rule: str) -> str:
    applies_key = ",".join(_normalize_applies_to(applies_to))
    return "|".join([
        str(failure_class or "unknown").casefold(),
        applies_key,
        _normalize_lesson_text(problem),
        _normalize_lesson_text(rule),
    ])


def _merge_unique_list(existing: List[Any], incoming: List[Any]) -> List[Any]:
    merged: List[Any] = []
    seen = set()
    for item in list(existing or []) + list(incoming or []):
        key = str(item).casefold()
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def _merge_metadata(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if key == "files_touched":
            merged[key] = _merge_unique_list(list(merged.get(key) or []), list(value or []))
        elif key == "sources":
            merged[key] = _merge_unique_list(list(merged.get(key) or []), list(value or []))
        elif key == "source":
            sources = list(merged.get("sources") or [])
            if merged.get("source"):
                sources.append(merged["source"])
                merged.pop("source", None)
            if value:
                sources.append(value)
            merged["sources"] = _merge_unique_list([], sources)
        elif key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = value
        elif merged.get(key) != value:
            merged[f"latest_{key}"] = value
    return merged


def add_patch_lesson(
    *,
    problem: str,
    failure_class: str,
    fix: str,
    rule: str,
    applies_to: Optional[List[str]] = None,
    source_scorecard_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    lessons = _load_lessons()
    applies = _normalize_applies_to([str(item) for item in (applies_to or []) if item])
    key = _lesson_key(failure_class, applies, problem, rule)

    for lesson in lessons:
        if lesson.get("dedupe_key") == key:
            lesson["updated_at"] = _now_iso()
            lesson["source_scorecard_id"] = source_scorecard_id or lesson.get("source_scorecard_id")
            lesson["occurrence_count"] = int(lesson.get("occurrence_count", 1) or 1) + 1
            lesson["applies_to"] = _merge_unique_list(list(lesson.get("applies_to") or []), applies)
            lesson["metadata"] = _merge_metadata(lesson.get("metadata", {}), metadata or {})
            _save_lessons(lessons)
            return lesson

    lesson = {
        "lesson_id": f"lesson_{uuid.uuid4().hex[:10]}",
        "dedupe_key": key,
        "problem": str(problem),
        "failure_class": str(failure_class),
        "fix": str(fix),
        "rule": str(rule),
        "applies_to": applies,
        "source_scorecard_id": source_scorecard_id,
        "metadata": metadata or {},
        "occurrence_count": 1,
        "times_reused": 0,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    lessons.append(lesson)
    _save_lessons(lessons)
    return lesson


def search_patch_lessons(
    *,
    failure_class: Optional[str] = None,
    action_type: Optional[str] = None,
    file_path: Optional[str] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    lessons = _load_lessons()
    scored: List[tuple[int, Dict[str, Any]]] = []
    failure_text = str(failure_class or "").casefold()
    action_text = str(action_type or "").casefold()
    file_text = str(file_path or "").casefold()

    for lesson in lessons:
        score = 0
        if failure_text and str(lesson.get("failure_class", "")).casefold() == failure_text:
            score += 4
        applies = [str(item).casefold() for item in lesson.get("applies_to", [])]
        if action_text and (action_text in applies or "general" in applies):
            score += 3
        haystack = " ".join([
            str(lesson.get("problem", "")),
            str(lesson.get("fix", "")),
            str(lesson.get("rule", "")),
            " ".join(str(item) for item in lesson.get("metadata", {}).get("files_touched", [])),
        ]).casefold()
        if file_text and file_text in haystack:
            score += 2
        if score:
            scored.append((score, lesson))

    scored.sort(key=lambda item: (item[0], item[1].get("updated_at", "")), reverse=True)
    try:
        bounded_limit = max(1, int(limit))
    except (TypeError, ValueError):
        bounded_limit = 3
    return [lesson for _, lesson in scored[:bounded_limit]]


def get_recent_patch_lessons(limit: int = 20) -> List[Dict[str, Any]]:
    """Return recently updated lessons without requiring a search match."""
    try:
        bounded_limit = max(1, min(int(limit), MAX_LESSONS))
    except (TypeError, ValueError):
        bounded_limit = 20
    lessons = sorted(
        _load_lessons(),
        key=lambda lesson: str(lesson.get("updated_at") or lesson.get("created_at") or ""),
        reverse=True,
    )
    return lessons[:bounded_limit]


def mark_lessons_reused(lesson_ids: List[str]) -> None:
    if not lesson_ids:
        return
    lessons = _load_lessons()
    wanted = set(lesson_ids)
    for lesson in lessons:
        if lesson.get("lesson_id") in wanted:
            lesson["times_reused"] = int(lesson.get("times_reused", 0) or 0) + 1
            lesson["updated_at"] = _now_iso()
    _save_lessons(lessons)


def format_patch_lessons_for_prompt(lessons: List[Dict[str, Any]]) -> str:
    if not lessons:
        return ""
    lines = ["Prior repair lessons:"]
    for index, lesson in enumerate(lessons, start=1):
        lines.append(
            f"{index}. Problem: {lesson.get('problem', '')} "
            f"Fix: {lesson.get('fix', '')} "
            f"Rule: {lesson.get('rule', '')}"
        )
    return "\n".join(lines)


def derive_lesson_from_scorecard(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    scorecard = record.get("scorecard", {}) if isinstance(record, dict) else {}
    if not isinstance(scorecard, dict):
        return None
    if scorecard.get("accepted") and not scorecard.get("blocked"):
        return None

    failure = scorecard.get("failure_reason") or {}
    failure_class = str(failure.get("failure_class") or "unknown")
    suggested_route = str(scorecard.get("suggested_route") or "")
    experiment_type = str(record.get("experiment_type") or "general")
    files_touched = list(scorecard.get("files_touched") or [])

    templates = {
        "dependency_missing": (
            "A dependency was missing during validation.",
            "Update the appropriate dependency manifest before retrying implementation.",
            "Every new external import must be represented in a dependency file.",
        ),
        "import_path_issue": (
            "A project import path failed during validation.",
            "Fix package boundaries, module names, or relative imports before rewriting behavior.",
            "Import/path failures should route to path repair, not blind logic rewrites.",
        ),
        "test_assertion_mismatch": (
            "A test assertion disagreed with produced behavior.",
            "Compare the test expectation with the task contract before changing code.",
            "Assertion mismatches require test-or-behavior review before normal repair.",
        ),
        "capability_violation": (
            "An agent attempted an action outside its capabilities.",
            "Route to capability policy review instead of retrying code generation.",
            "Capability violations are governance failures, not implementation bugs.",
        ),
        "state_machine_violation": (
            "An agent attempted an invalid lifecycle transition.",
            "Repair the lifecycle/state transition before retrying task execution.",
            "State-machine violations must block normal retries.",
        ),
        "environment_ci_issue": (
            "The environment or CI layer blocked validation.",
            "Fix the environment before generating more code.",
            "Environment failures should not trigger implementation rewrites.",
        ),
        "flaky_llm_issue": (
            "The model output was malformed, empty, or inconsistent.",
            "Allow one clearer retry, then block if repeated.",
            "Flaky model output needs prompt/output discipline, not endless retries.",
        ),
        "unknown": (
            "A failure could not be classified deterministically.",
            "Route to human review before attempting another repair.",
            "Unknown failures should block autonomous rewrites.",
        ),
    }
    problem, fix, rule = templates.get(failure_class, templates["unknown"])
    lesson = add_patch_lesson(
        problem=problem,
        failure_class=failure_class,
        fix=fix,
        rule=rule,
        applies_to=[experiment_type, "general"],
        source_scorecard_id=record.get("record_id"),
        metadata={
            "suggested_route": suggested_route,
            "files_touched": files_touched,
            "source": record.get("source"),
        },
    )
    return lesson
