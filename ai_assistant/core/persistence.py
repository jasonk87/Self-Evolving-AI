"""Small, dependency-free helpers for durable local JSON state.

The application stores important runtime state in JSON files.  Writing those
files directly can leave truncated JSON after a process interruption, so all
new callers should use :func:`atomic_write_json`.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Iterator


_lock_registry_guard = threading.Lock()
_path_locks: dict[str, threading.RLock] = {}


def _lock_for(path: str) -> threading.RLock:
    normalized = os.path.abspath(path)
    with _lock_registry_guard:
        return _path_locks.setdefault(normalized, threading.RLock())


@contextmanager
def json_path_lock(path: str) -> Iterator[None]:
    """Serialize read/modify/write operations for one JSON path in-process."""
    lock = _lock_for(path)
    with lock:
        yield


def atomic_write_json(path: str, value: Any, *, indent: int = 2) -> None:
    """Write JSON by replacing the destination only after a complete write."""
    directory = os.path.dirname(os.path.abspath(path)) or os.curdir
    os.makedirs(directory, exist_ok=True)

    fd, temporary_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=indent, ensure_ascii=False, default=str)
            handle.flush()
        os.replace(temporary_path, path)
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
