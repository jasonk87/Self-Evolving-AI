import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from ai_assistant.config import get_data_dir
from ai_assistant.core.action_audit_ledger import append_action_audit_event


EXPERIMENT_SCOREBOARD_FILENAME = "experiment_scoreboard.jsonl"
DEFAULT_RECENT_LIMIT = 100
MAX_RECENT_LIMIT = 500
_LOCK = threading.Lock()


def get_experiment_scoreboard_path() -> str:
    return os.path.join(get_data_dir(), EXPERIMENT_SCOREBOARD_FILENAME)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalize_scorecard(scorecard: Any) -> Dict[str, Any]:
    data = _json_safe(scorecard)
    if not isinstance(data, dict):
        data = {}

    return {
        "task_id": str(data.get("task_id") or ""),
        "tests_run": int(data.get("tests_run") or 0),
        "tests_passed": int(data.get("tests_passed") or 0),
        "risk_level": str(data.get("risk_level") or "low"),
        "files_touched": list(data.get("files_touched") or []),
        "capabilities_used": list(data.get("capabilities_used") or []),
        "failure_reason": data.get("failure_reason"),
        "accepted": bool(data.get("accepted", False)),
        "blocked": bool(data.get("blocked", False)),
        "suggested_route": data.get("suggested_route"),
    }


def record_experiment_scorecard(
    scorecard: Any,
    *,
    actor: str,
    experiment_type: str,
    source: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = _normalize_scorecard(scorecard)
    record = {
        "record_id": f"score_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": str(actor or "unknown"),
        "experiment_type": str(experiment_type or "unknown"),
        "source": str(source) if source else None,
        "scorecard": normalized,
        "metadata": _json_safe(metadata or {}),
    }

    if "pytest" in sys.modules and os.environ.get("EXPERIMENT_SCOREBOARD_ALLOW_PYTEST") != "1":
        return record

    path = get_experiment_scoreboard_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover
        print(f"ExperimentScoreboard: failed to append scorecard: {exc}")

    append_action_audit_event(
        "EXPERIMENT_SCORECARD_RECORDED",
        actor,
        f"Experiment scorecard recorded: {experiment_type}",
        action_type=experiment_type,
        task_id=normalized.get("task_id") or None,
        source=source,
        status="accepted" if normalized.get("accepted") else "rejected",
        files_touched=normalized.get("files_touched") or [],
        capabilities_used=normalized.get("capabilities_used") or [],
        metadata={
            "record_id": record["record_id"],
            "tests_run": normalized.get("tests_run"),
            "tests_passed": normalized.get("tests_passed"),
            "risk_level": normalized.get("risk_level"),
            "blocked": normalized.get("blocked"),
            "suggested_route": normalized.get("suggested_route"),
        },
    )

    try:
        from ai_assistant.core.patch_memory import derive_lesson_from_scorecard

        derive_lesson_from_scorecard(record)
    except Exception as exc:  # pragma: no cover
        print(f"ExperimentScoreboard: failed to derive patch lesson: {exc}")

    return record


def get_recent_experiment_scorecards(limit: int = DEFAULT_RECENT_LIMIT) -> List[Dict[str, Any]]:
    try:
        bounded_limit = max(1, min(int(limit), MAX_RECENT_LIMIT))
    except (TypeError, ValueError):
        bounded_limit = DEFAULT_RECENT_LIMIT

    path = get_experiment_scoreboard_path()
    if not os.path.exists(path):
        return []

    records: List[Dict[str, Any]] = []
    try:
        with _LOCK:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
            if len(records) >= bounded_limit:
                break
    except Exception as exc:  # pragma: no cover
        print(f"ExperimentScoreboard: failed to read scorecards: {exc}")
        return []

    return records
