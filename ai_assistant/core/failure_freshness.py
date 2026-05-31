import datetime
import os
from typing import Any, Dict, Optional

from ai_assistant.config import project_root


FAILURE_RUNTIME_REVISION_KEY = "runtime_revision_at_failure"
FAILURE_OBSERVED_AT_KEY = "failure_observed_at"
SUPERSEDED_FAILURE_STATUS = "SUPERSEDED_EXTERNAL_UPDATE"

_TRACKED_ROOTS = (
    os.path.join(project_root, "ai_assistant"),
    os.path.join(project_root, "routes"),
)
_TRACKED_FILES = (
    os.path.join(project_root, "web_app.py"),
    os.path.join(project_root, ".env"),
)
_TRACKED_EXTENSIONS = {".py", ".json", ".html", ".js", ".css"}
_SKIPPED_DIRECTORIES = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    "chroma_db",
    "data",
    "logs",
}


def get_runtime_revision_timestamp() -> float:
    """Return the newest source or configuration modification timestamp."""
    newest = 0.0

    for filepath in _TRACKED_FILES:
        try:
            newest = max(newest, os.path.getmtime(filepath))
        except OSError:
            pass

    for root in _TRACKED_ROOTS:
        if not os.path.isdir(root):
            continue
        for current_dir, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                dirname for dirname in dirnames
                if dirname not in _SKIPPED_DIRECTORIES
            ]
            for filename in filenames:
                if os.path.splitext(filename)[1].lower() not in _TRACKED_EXTENSIONS:
                    continue
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(current_dir, filename)))
                except OSError:
                    pass

    return newest


def _as_timestamp(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


def annotate_failure_metadata(
    metadata: Optional[Dict[str, Any]] = None,
    failure_observed_at: Optional[Any] = None,
) -> Dict[str, Any]:
    """Attach freshness data without overwriting the original failure snapshot."""
    annotated = metadata if metadata is not None else {}
    if FAILURE_OBSERVED_AT_KEY not in annotated:
        observed_timestamp = _as_timestamp(failure_observed_at)
        if observed_timestamp is None:
            observed_timestamp = datetime.datetime.now(datetime.timezone.utc).timestamp()
        annotated[FAILURE_OBSERVED_AT_KEY] = datetime.datetime.fromtimestamp(
            observed_timestamp, tz=datetime.timezone.utc
        ).isoformat()
    if _as_timestamp(annotated.get(FAILURE_RUNTIME_REVISION_KEY)) is None:
        observed_timestamp = _as_timestamp(annotated[FAILURE_OBSERVED_AT_KEY])
        current_revision = get_runtime_revision_timestamp()
        annotated[FAILURE_RUNTIME_REVISION_KEY] = min(
            current_revision,
            observed_timestamp if observed_timestamp is not None else current_revision,
        )
    return annotated


def is_failure_stale(
    metadata: Optional[Dict[str, Any]] = None,
    failure_observed_at: Optional[Any] = None,
    current_runtime_revision: Optional[float] = None,
) -> bool:
    """Return True when source or configuration changed after a failure."""
    metadata = metadata or {}
    failure_revision = _as_timestamp(metadata.get(FAILURE_RUNTIME_REVISION_KEY))
    if failure_revision is None:
        failure_revision = _as_timestamp(metadata.get(FAILURE_OBSERVED_AT_KEY))
    if failure_revision is None:
        failure_revision = _as_timestamp(failure_observed_at)
    if failure_revision is None:
        return False

    current_revision = (
        get_runtime_revision_timestamp()
        if current_runtime_revision is None
        else current_runtime_revision
    )
    return current_revision > failure_revision
