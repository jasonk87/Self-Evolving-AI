import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from ai_assistant.config import get_data_dir


ACTION_AUDIT_LEDGER_FILENAME = "action_audit_ledger.jsonl"
DEFAULT_RECENT_LIMIT = 100
MAX_RECENT_LIMIT = 500
_LOCK = threading.Lock()
_REDACTED_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "suggested_code_change",
    "token",
}


def get_action_audit_ledger_path() -> str:
    return os.path.join(get_data_dir(), ACTION_AUDIT_LEDGER_FILENAME)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        safe: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _REDACTED_KEYS or any(part in key_text.lower() for part in ("secret", "token", "password", "api_key")):
                safe[key_text] = "[redacted]"
            else:
                safe[key_text] = _json_safe(item)
        return safe
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def append_action_audit_event(
    event_type: str,
    actor: str,
    summary: str,
    *,
    action_type: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    source: Optional[str] = None,
    status: Optional[str] = None,
    files_touched: Optional[List[str]] = None,
    capabilities_used: Optional[List[Dict[str, Any]]] = None,
    policy_decision: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append one structured action-audit event.

    This ledger is intentionally append-only JSONL. If a write fails, callers
    should not fail the autonomous action; audit must observe behavior, not
    become a new runtime dependency.
    """
    event = {
        "event_id": f"act_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": str(event_type),
        "actor": str(actor or "unknown"),
        "summary": str(summary or ""),
        "action_type": str(action_type) if action_type else None,
        "task_id": str(task_id) if task_id else None,
        "session_id": str(session_id) if session_id else None,
        "source": str(source) if source else None,
        "status": str(status) if status else None,
        "files_touched": list(files_touched or []),
        "capabilities_used": _json_safe(capabilities_used or []),
        "policy_decision": _json_safe(policy_decision or {}),
        "metadata": _json_safe(metadata or {}),
    }

    path = get_action_audit_ledger_path()
    if "pytest" in sys.modules and os.environ.get("ACTION_AUDIT_LEDGER_ALLOW_PYTEST") != "1":
        return event

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(event, ensure_ascii=False)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception as exc:  # pragma: no cover
        print(f"ActionAuditLedger: failed to append event: {exc}")

    return event


def get_recent_action_audit_events(limit: int = DEFAULT_RECENT_LIMIT) -> List[Dict[str, Any]]:
    try:
        bounded_limit = max(1, min(int(limit), MAX_RECENT_LIMIT))
    except (TypeError, ValueError):
        bounded_limit = DEFAULT_RECENT_LIMIT

    path = get_action_audit_ledger_path()
    if not os.path.exists(path):
        return []

    events: List[Dict[str, Any]] = []
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
                events.append(parsed)
            if len(events) >= bounded_limit:
                break
    except Exception as exc:  # pragma: no cover
        print(f"ActionAuditLedger: failed to read events: {exc}")
        return []

    return events
