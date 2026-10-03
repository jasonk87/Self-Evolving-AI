"""Logging: rotating file + console + an in-memory ring buffer the UI can read."""

from __future__ import annotations

import collections
import logging
import logging.handlers
import sys
import threading
import time
from typing import Any

from . import paths

_RING: collections.deque[dict[str, Any]] = collections.deque(maxlen=800)
_RING_LOCK = threading.Lock()
_configured = False


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                formatter = self.formatter or logging.Formatter()
                entry["message"] += "\n" + formatter.formatException(record.exc_info)
            with _RING_LOCK:
                _RING.append(entry)
        except Exception:
            self.handleError(record)


def setup(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger("weebo")
    root.setLevel(logging.DEBUG)
    root.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        paths.logs_dir() / "weebo.log", maxBytes=4_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(file_handler)

    ring = _RingHandler()
    ring.setLevel(logging.INFO)
    ring.setFormatter(fmt)
    root.addHandler(ring)

    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def recent(limit: int = 200, min_level: str = "INFO", since: float = 0.0) -> list[dict[str, Any]]:
    threshold = logging.getLevelName(min_level.upper())
    if not isinstance(threshold, int):
        threshold = logging.INFO
    with _RING_LOCK:
        items = [e for e in _RING if e["ts"] > since and logging.getLevelName(e["level"]) >= threshold]
    return items[-limit:]


def errors_since(seconds: float) -> list[dict[str, Any]]:
    cutoff = time.time() - seconds
    return recent(limit=200, min_level="ERROR", since=cutoff)


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"weebo.{name}")
