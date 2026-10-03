"""A tiny async pub/sub bus. The websocket hub forwards every event to the UI."""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import itertools
import time
from typing import Any, Awaitable, Callable

from . import log

logger = log.get("events")

Callback = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, tuple[str, Callback]] = {}
        self._ids = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, pattern: str, callback: Callback) -> Callable[[], None]:
        sub_id = next(self._ids)
        self._subs[sub_id] = (pattern, callback)

        def unsubscribe() -> None:
            self._subs.pop(sub_id, None)

        return unsubscribe

    def publish(self, topic: str, data: dict[str, Any] | None = None) -> None:
        payload = dict(data or {})
        payload.setdefault("ts", time.time())
        for pattern, callback in list(self._subs.values()):
            if pattern != "*" and pattern != topic and not fnmatch.fnmatchcase(topic, pattern):
                continue
            try:
                result = callback(topic, payload)
            except Exception:
                logger.exception("Event subscriber failed for %s", topic)
                continue
            if inspect.isawaitable(result):
                task = asyncio.ensure_future(result)
                self._tasks.add(task)
                task.add_done_callback(self._task_done)

    def publish_threadsafe(self, topic: str, data: dict[str, Any] | None = None) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self.publish, topic, data)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Async event subscriber failed: %r", task.exception())
