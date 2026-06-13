import json
import os
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from ai_assistant.config import get_data_dir


TOOL_LIFECYCLE_FILENAME = "tool_lifecycle.json"
MAX_EVENTS_PER_TOOL = 50
_LOCK = threading.Lock()


class ToolLifecycleState(str, Enum):
    CANDIDATE = "candidate"
    REGISTERED = "registered"
    GRADUATED = "graduated"
    QUARANTINED = "quarantined"
    DEPRECATED = "deprecated"


def get_tool_lifecycle_path() -> str:
    return os.path.join(get_data_dir(), TOOL_LIFECYCLE_FILENAME)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _load_store() -> Dict[str, Dict[str, Any]]:
    path = get_tool_lifecycle_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            records = data.get("tools", data)
            if isinstance(records, dict):
                return {
                    str(name): record
                    for name, record in records.items()
                    if isinstance(record, dict)
                }
    except Exception as exc:  # pragma: no cover
        print(f"ToolLifecycle: failed to load lifecycle store: {exc}")
    return {}


def _save_store(records: Dict[str, Dict[str, Any]]) -> None:
    path = get_tool_lifecycle_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = path + ".tmp"
    payload = {
        "schema_version": 1,
        "updated_at": _now_iso(),
        "tools": records,
    }
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)


def _normalize_state(state: ToolLifecycleState | str) -> str:
    if isinstance(state, ToolLifecycleState):
        return state.value
    return ToolLifecycleState(str(state)).value


def _event(event_type: str, summary: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "event_id": f"tle_{uuid.uuid4().hex[:10]}",
        "event_type": str(event_type),
        "summary": str(summary),
        "timestamp": _now_iso(),
        "metadata": _json_safe(metadata or {}),
    }


def upsert_tool_lifecycle(
    *,
    tool_name: str,
    state: ToolLifecycleState | str,
    module_path: Optional[str] = None,
    function_name: Optional[str] = None,
    file_path: Optional[str] = None,
    tool_type: Optional[str] = None,
    source: Optional[str] = None,
    summary: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_name = str(tool_name or "").strip()
    if not normalized_name:
        raise ValueError("tool_name is required")

    normalized_state = _normalize_state(state)
    with _LOCK:
        records = _load_store()
        record = records.get(normalized_name) or {
            "tool_id": f"tool_{uuid.uuid4().hex[:10]}",
            "tool_name": normalized_name,
            "state": normalized_state,
            "created_at": _now_iso(),
            "events": [],
            "usage_count": 0,
            "failure_count": 0,
            "last_success_at": None,
            "last_failure_at": None,
        }

        record["state"] = normalized_state
        record["updated_at"] = _now_iso()
        for key, value in {
            "module_path": module_path,
            "function_name": function_name,
            "file_path": file_path,
            "tool_type": tool_type,
            "source": source,
        }.items():
            if value:
                record[key] = str(value)

        event_summary = summary or f"Tool lifecycle moved to {normalized_state}."
        events = list(record.get("events") or [])
        events.append(_event(f"tool_{normalized_state}", event_summary, metadata))
        record["events"] = events[-MAX_EVENTS_PER_TOOL:]
        records[normalized_name] = record
        _save_store(records)
        return dict(record)


def record_tool_candidate(
    *,
    tool_name: str,
    module_path: str,
    function_name: str,
    file_path: str,
    tool_type: str,
    source: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return upsert_tool_lifecycle(
        tool_name=tool_name,
        state=ToolLifecycleState.CANDIDATE,
        module_path=module_path,
        function_name=function_name,
        file_path=file_path,
        tool_type=tool_type,
        source=source,
        summary="Generated tool candidate saved for validation.",
        metadata=metadata,
    )


def mark_tool_registered(
    *,
    tool_name: str,
    module_path: Optional[str] = None,
    function_name: Optional[str] = None,
    file_path: Optional[str] = None,
    tool_type: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return upsert_tool_lifecycle(
        tool_name=tool_name,
        state=ToolLifecycleState.REGISTERED,
        module_path=module_path,
        function_name=function_name,
        file_path=file_path,
        tool_type=tool_type,
        summary="Tool imported and registered with the tool system.",
        metadata=metadata,
    )


def mark_tool_graduated(
    *,
    tool_name: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return upsert_tool_lifecycle(
        tool_name=tool_name,
        state=ToolLifecycleState.GRADUATED,
        summary="Tool graduated after accepted validation evidence.",
        metadata=metadata,
    )


def maybe_graduate_tool_from_scorecard(tool_name: str, scorecard: Any) -> Optional[Dict[str, Any]]:
    data = _json_safe(scorecard)
    if not isinstance(data, dict):
        return None
    tests_run = int(data.get("tests_run") or 0)
    tests_passed = int(data.get("tests_passed") or 0)
    accepted = bool(data.get("accepted"))
    blocked = bool(data.get("blocked"))
    if accepted and not blocked and tests_run > 0 and tests_passed >= tests_run:
        return mark_tool_graduated(
            tool_name=tool_name,
            metadata={"scorecard": data},
        )
    return None


def record_tool_execution(
    tool_name: str,
    *,
    success: bool,
    error_signature: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    normalized_name = str(tool_name or "").strip()
    if not normalized_name:
        return None

    with _LOCK:
        records = _load_store()
        record = records.get(normalized_name)
        if not record:
            return None

        record["updated_at"] = _now_iso()
        if success:
            record["usage_count"] = int(record.get("usage_count", 0) or 0) + 1
            record["last_success_at"] = _now_iso()
            event_type = "tool_execution_succeeded"
            summary = "Tracked generated tool executed successfully."
        else:
            record["failure_count"] = int(record.get("failure_count", 0) or 0) + 1
            record["last_failure_at"] = _now_iso()
            if error_signature:
                record["last_error_signature"] = str(error_signature)
            event_type = "tool_execution_failed"
            summary = "Tracked generated tool execution failed."

        events = list(record.get("events") or [])
        event_metadata = dict(metadata or {})
        if error_signature:
            event_metadata["error_signature"] = error_signature
        events.append(_event(event_type, summary, event_metadata))
        record["events"] = events[-MAX_EVENTS_PER_TOOL:]
        records[normalized_name] = record
        _save_store(records)
        return dict(record)


def mark_tool_quarantined(
    tool_name: str,
    *,
    reason: str,
    error_signature: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    event_metadata = dict(metadata or {})
    if error_signature:
        event_metadata["error_signature"] = error_signature
    return upsert_tool_lifecycle(
        tool_name=tool_name,
        state=ToolLifecycleState.QUARANTINED,
        summary=f"Tool quarantined: {reason}",
        metadata=event_metadata,
    )


def get_tool_lifecycle_record(tool_name: str) -> Optional[Dict[str, Any]]:
    records = _load_store()
    record = records.get(str(tool_name or "").strip())
    return dict(record) if record else None


def list_tool_lifecycle_records(
    *,
    state: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    try:
        bounded_limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        bounded_limit = 100

    records = list(_load_store().values())
    if state:
        state_text = str(state).casefold()
        records = [
            record for record in records
            if str(record.get("state", "")).casefold() == state_text
        ]
    records.sort(key=lambda record: str(record.get("updated_at") or ""), reverse=True)
    return [dict(record) for record in records[:bounded_limit]]
