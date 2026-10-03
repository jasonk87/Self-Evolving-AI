"""Registry of Weebo's own tools, exposed to Codex as ``dynamicTools``.

Codex calls back into Weebo (``item/tool/call``) whenever the model uses one of
these, so they can touch Weebo's memory, scheduler, agents and evolution engine.
"""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .. import log

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("brain.tools")


@dataclass
class ToolContext:
    app: "WeeboApp"
    thread_id: str
    turn_id: str = ""
    conversation_id: str | None = None
    task_id: str | None = None
    trigger: str = "user"  # what started the turn: user | routine | brief | agent_report | event | agent

    @property
    def is_agent(self) -> bool:
        return self.task_id is not None


@dataclass
class ToolResult:
    text: str
    success: bool = True
    images: list[str] = field(default_factory=list)  # data: URLs or http(s) URLs

    def to_codex(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = [{"type": "inputText", "text": self.text}]
        items += [{"type": "inputImage", "imageUrl": url} for url in self.images]
        return {"contentItems": items, "success": self.success}


ToolFn = Callable[..., Awaitable[ToolResult | str | dict] | ToolResult | str | dict]


@dataclass
class Tool:
    name: str
    description: str | Callable[[], str]
    parameters: dict[str, Any]
    fn: ToolFn
    scopes: tuple[str, ...] = ("chat",)  # chat | agent | background

    def spec(self) -> dict[str, Any]:
        description = self.description() if callable(self.description) else self.description
        return {"type": "function", "name": self.name, "description": description.strip(), "inputSchema": self.parameters}


class ToolError(Exception):
    """Raised by a tool to report a user-facing failure without a stack trace."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, description: str | Callable[[], str], parameters: dict[str, Any] | None = None,
                 scopes: tuple[str, ...] = ("chat",)) -> Callable[[ToolFn], ToolFn]:
        schema = parameters or {"type": "object", "properties": {}, "additionalProperties": False}
        schema.setdefault("type", "object")
        schema.setdefault("additionalProperties", False)

        def decorator(fn: ToolFn) -> ToolFn:
            self._tools[name] = Tool(name, description, schema, fn, scopes)
            return fn

        return decorator

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self, scope: str) -> list[str]:
        return [t.name for t in self._tools.values() if scope in t.scopes]

    def specs(self, scope: str) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values() if scope in t.scopes]

    async def call(self, ctx: ToolContext, name: str, arguments: Any) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"Unknown Weebo tool: {name}", success=False)
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                return ToolResult("Arguments must be a JSON object.", success=False)
        if not isinstance(arguments, dict):
            return ToolResult("Arguments must be a JSON object.", success=False)
        allowed = set(tool.parameters.get("properties", {}).keys())
        unknown = [k for k in arguments if k not in allowed]
        if unknown:
            return ToolResult(f"Unknown argument(s) for {name}: {', '.join(unknown)}", success=False)
        missing = [k for k in tool.parameters.get("required", []) if k not in arguments]
        if missing:
            return ToolResult(f"Missing required argument(s) for {name}: {', '.join(missing)}", success=False)
        started = time.perf_counter()
        try:
            result = tool.fn(ctx, **arguments)
            if inspect.isawaitable(result):
                result = await result
        except ToolError as exc:
            return ToolResult(str(exc), success=False)
        except Exception as exc:
            logger.exception("Tool %s crashed", name)
            ctx.app.diagnostics.record("tool_crash", f"Tool {name} crashed: {exc!r}", {"tool": name})
            return ToolResult(f"{name} failed: {exc}", success=False)
        finally:
            logger.debug("tool %s took %.0f ms", name, (time.perf_counter() - started) * 1000)
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, dict):
            return ToolResult(json.dumps(result, ensure_ascii=False, default=str))
        return ToolResult(str(result))


registry = ToolRegistry()
