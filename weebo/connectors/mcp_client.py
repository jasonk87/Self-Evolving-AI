"""A small MCP client for testing connectors in isolation before anyone relies on them.

It speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC to a child process) or over streamable
HTTP (JSON-RPC POSTs; replies as JSON or server-sent events). It only does what onboarding needs: initialize, list
the tools and call one. Codex itself runs approved servers for real work.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
from typing import Any

PROTOCOL = "2025-06-18"
CLIENT = {"name": "weebo-onboarding", "version": "1"}


class MCPError(Exception):
    pass


class _Stdio:
    def __init__(self, command: list[str], env: dict[str, str] | None, cwd: str | None):
        self.command, self.env, self.cwd = command, env, cwd
        self.proc: asyncio.subprocess.Process | None = None

    async def open(self) -> None:
        env = {**os.environ, **(self.env or {})}
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env, cwd=self.cwd)
        except (OSError, ValueError) as exc:
            raise MCPError(f"Couldn't start {self.command[0]!r}: {exc}") from exc

    async def send(self, message: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    async def receive(self, request_id: int) -> dict[str, Any]:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                err = (await self.proc.stderr.read(2000)).decode(errors="replace") if self.proc.stderr else ""
                raise MCPError("The server closed the connection." + (f" It said: {err.strip()[:300]}" if err else ""))
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # stray log output
            if message.get("id") == request_id and ("result" in message or "error" in message):
                return message

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                await asyncio.wait_for(self.proc.wait(), 2)
            except (asyncio.TimeoutError, OSError):
                self.proc.kill()
                await self.proc.wait()


class _Http:
    def __init__(self, url: str, headers: dict[str, str] | None):
        self.url, self.headers, self.session_id = url, dict(headers or {}), ""
        self._replies: dict[int, dict[str, Any]] = {}
        self._http = None

    async def open(self) -> None:
        import aiohttp
        self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

    async def send(self, message: dict[str, Any]) -> None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        async with self._http.post(self.url, data=json.dumps(message), headers=headers) as response:  # type: ignore[union-attr]
            if response.status >= 400:
                raise MCPError(f"HTTP {response.status} from {self.url}")
            self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
            if "id" not in message:
                return
            body = await response.text()
            if response.headers.get("Content-Type", "").startswith("text/event-stream"):
                for line in body.splitlines():
                    if line.startswith("data:"):
                        reply = json.loads(line[5:].strip())
                        if reply.get("id") is not None:
                            self._replies[reply["id"]] = reply
            elif body.strip():
                reply = json.loads(body)
                self._replies[reply.get("id")] = reply

    async def receive(self, request_id: int) -> dict[str, Any]:
        if request_id not in self._replies:
            raise MCPError("The server sent no reply.")
        return self._replies.pop(request_id)

    async def close(self) -> None:
        if self._http:
            await self._http.close()


class MCPClient:
    def __init__(self, transport: str, command: list[str] | None = None, url: str = "",
                 env: dict[str, str] | None = None, headers: dict[str, str] | None = None, cwd: str | None = None,
                 timeout: float = 20.0):
        if transport == "stdio":
            if not command:
                raise MCPError("A stdio connector needs a command.")
            self._io: Any = _Stdio(command, env, cwd)
        elif transport == "http":
            if not url.startswith(("http://", "https://")):
                raise MCPError("An HTTP connector needs an http(s) URL.")
            self._io = _Http(url, headers)
        else:
            raise MCPError("transport must be stdio or http")
        self.timeout = timeout
        self._ids = itertools.count(1)
        self.server_info: dict[str, Any] = {}

    async def __aenter__(self) -> "MCPClient":
        await self._io.open()
        try:
            result = await self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                       "clientInfo": CLIENT})
            self.server_info = result.get("serverInfo") or {}
            await self._io.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            await self._io.close()
            raise
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._io.close()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = next(self._ids)
        await self._io.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        try:
            reply = await asyncio.wait_for(self._io.receive(request_id), self.timeout)
        except asyncio.TimeoutError as exc:
            raise MCPError(f"{method} timed out after {self.timeout:g}s") from exc
        if "error" in reply:
            raise MCPError(f"{method} failed: {(reply['error'] or {}).get('message', reply['error'])}")
        return reply.get("result") or {}

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(20):
            result = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += result.get("tools") or []
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("tools/call", {"name": name, "arguments": arguments or {}})
