"""JSON-RPC (JSON lines over stdio) transport for ``codex app-server``."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import subprocess
from typing import Any, Awaitable, Callable

from .. import log

logger = log.get("codex.rpc")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

NotificationHandler = Callable[[str, dict[str, Any]], Awaitable[None] | None]
RequestHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def __str__(self) -> str:
        return f"{self.message} (code {self.code})"


class EngineClosed(RuntimeError):
    pass


class JsonRpcProcess:
    """Owns one app-server child process and multiplexes requests over it."""

    def __init__(self, command: list[str], env: dict[str, str] | None = None, cwd: str | None = None):
        self.command = command
        self.env = env
        self.cwd = cwd
        self.proc: asyncio.subprocess.Process | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._write_lock = asyncio.Lock()
        self._notification_handler: NotificationHandler | None = None
        self._request_handler: RequestHandler | None = None
        self._tasks: list[asyncio.Task] = []
        self._handler_tasks: set[asyncio.Task] = set()
        self._teardown_task: asyncio.Task | None = None
        self._notifications: asyncio.Queue = asyncio.Queue()
        self.closed = asyncio.Event()
        self.stderr_tail: list[str] = []
        self.returncode: int | None = None

    def on_notification(self, handler: NotificationHandler) -> None:
        self._notification_handler = handler

    def on_request(self, handler: RequestHandler) -> None:
        self._request_handler = handler

    async def start(self) -> None:
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            cwd=self.cwd,
            limit=64 * 1024 * 1024,
            **kwargs,
        )
        self._tasks = [
            asyncio.create_task(self._read_stdout(), name="codex-stdout"),
            asyncio.create_task(self._read_stderr(), name="codex-stderr"),
            asyncio.create_task(self._wait_exit(), name="codex-exit"),
            asyncio.create_task(self._notification_worker(), name="codex-notifications"),
        ]

    @property
    def running(self) -> bool:
        return (self.proc is not None and self.proc.returncode is None
                and self._teardown_task is None and not self.closed.is_set())

    async def request(self, method: str, params: Any = None, timeout: float | None = 120.0) -> Any:
        if not self.running:
            raise EngineClosed("Codex engine is not running")
        request_id = next(self._ids)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            await self._send(message)
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Any = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)

    async def _send(self, message: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise EngineClosed("Codex engine is not running")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                self.proc.stdin.write(data)
                await self.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
                raise EngineClosed(f"Codex engine pipe closed: {exc}") from exc

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        reader = self.proc.stdout
        while True:
            try:
                line = await reader.readline()
            except (asyncio.LimitOverrunError, ValueError) as exc:
                logger.error("Dropping oversized message from Codex: %s", exc)
                continue
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                logger.debug("Non-JSON output from Codex: %s", text[:300])
                continue
            self._dispatch(message)

    def _dispatch(self, message: dict[str, Any]) -> None:
        has_id = "id" in message
        method = message.get("method")
        if has_id and method is None:
            future = self._pending.get(message["id"])
            if future is None or future.done():
                return
            if "error" in message:
                err = message["error"] or {}
                future.set_exception(RpcError(err.get("code", -1), err.get("message", "unknown error"), err.get("data")))
            else:
                future.set_result(message.get("result"))
            return
        if has_id and method:
            # Server requests may wait on the user (approvals), so they run concurrently.
            task = asyncio.create_task(self._serve_request(message["id"], method, message.get("params") or {}))
            self._handler_tasks.add(task)
            task.add_done_callback(self._handler_tasks.discard)
        elif method:
            # Notifications (streamed deltas) must be handled strictly in order.
            self._notifications.put_nowait((method, message.get("params") or {}))

    async def _notification_worker(self) -> None:
        while True:
            method, params = await self._notifications.get()
            try:
                if self._notification_handler is None:
                    continue
                result = self._notification_handler(method, params)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Notification handler failed for %s", method)
            finally:
                self._notifications.task_done()

    async def _serve_request(self, request_id: Any, method: str, params: dict[str, Any]) -> None:
        try:
            if self._request_handler is None:
                raise RpcError(-32601, f"No handler for {method}")
            result = await self._request_handler(method, params)
            await self._send({"id": request_id, "result": result})
        except RpcError as exc:
            await self._safe_send({"id": request_id, "error": {"code": exc.code, "message": exc.message}})
        except Exception as exc:  # never leave Codex waiting on a reply
            logger.exception("Server request %s failed", method)
            await self._safe_send({"id": request_id, "error": {"code": -32603, "message": str(exc) or type(exc).__name__}})

    async def _safe_send(self, message: dict[str, Any]) -> None:
        try:
            await self._send(message)
        except EngineClosed:
            pass

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            text = _ANSI.sub("", line.decode("utf-8", "replace")).rstrip()
            if not text:
                continue
            self.stderr_tail.append(text)
            del self.stderr_tail[:-40]
            logger.debug("codex: %s", text[:500])

    async def _wait_exit(self) -> None:
        assert self.proc
        self.returncode = await self.proc.wait()
        self._begin_teardown()

    def _begin_teardown(self) -> asyncio.Task:
        if self._teardown_task is None:
            self._teardown_task = asyncio.create_task(self._teardown(), name="codex-teardown")
        return self._teardown_task

    async def _teardown(self) -> None:
        # Let the readers consume final replies and notifications before cancelling
        # workers. Bound the drain in case a descendant still holds a pipe open.
        try:
            await asyncio.wait_for(asyncio.gather(*self._tasks[:2], return_exceptions=True), 1)
        except asyncio.TimeoutError:
            pass
        error = EngineClosed(f"Codex engine exited with code {self.returncode}")
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

        tasks = [*self._tasks, *self._handler_tasks]
        for task in self._handler_tasks:
            task.cancel()
        # Preserve queued terminal notifications where possible, but a blocked
        # listener must not keep this transport alive across engine generations.
        try:
            await asyncio.wait_for(self._notifications.join(), 1)
        except asyncio.TimeoutError:
            pass
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.closed.set()

    async def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.returncode is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
                await self.proc.wait()
        self.returncode = self.proc.returncode
        # Both exit and stop share one cleanup task. Shield it so cancelling a
        # caller cannot strand the transport's workers or pending requests.
        await asyncio.shield(self._begin_teardown())
