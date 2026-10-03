"""High-level Codex engine: one long-lived ``codex app-server`` hosting many threads.

Every Weebo conversation, background agent and self-evolution build is a Codex
thread inside this one process, so they all run concurrently. Notifications are
routed to per-thread listeners; server-initiated requests (approvals, dynamic
tool calls, user-input prompts) are routed to the owning thread's handler.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import __version__, log, paths
from ..events import EventBus
from .locator import CodexBinary, find_codex
from .rpc import EngineClosed, JsonRpcProcess, RpcError

logger = log.get("codex.engine")

ThreadListener = Callable[[str, dict[str, Any]], Awaitable[None] | None]
ThreadRequestHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]

APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "execCommandApproval",
    "applyPatchApproval",
}


@dataclass
class EngineFeatures:
    additional_context: bool = False
    dynamic_tools: bool = True
    output_schema: bool = True
    skills_extra_roots: bool = False


@dataclass
class ThreadRoute:
    listeners: list[ThreadListener] = field(default_factory=list)
    request_handler: ThreadRequestHandler | None = None


class CodexEngine:
    def __init__(self, bus: EventBus, binary_override: str = ""):
        self.bus = bus
        self.binary_override = binary_override
        self.binary: CodexBinary | None = None
        self.rpc: JsonRpcProcess | None = None
        self.features = EngineFeatures()
        self.status = "stopped"  # stopped | starting | ready | login_required | error | restarting
        self.last_error = ""
        self.account: dict[str, Any] | None = None
        self.rate_limits: dict[str, Any] | None = None
        self.models: list[dict[str, Any]] = []
        self.user_agent = ""
        self.codex_home = ""
        self.generation = 0
        self.loaded_threads: set[str] = set()
        self._routes: dict[str, ThreadRoute] = {}
        self._ready = asyncio.Event()
        self._stopping = False
        self._supervisor: asyncio.Task | None = None
        self._restart_attempts = 0
        self._login_waiters: list[asyncio.Future] = []

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._stopping = False
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(self._supervise(), name="codex-supervisor")

    async def wait_ready(self, timeout: float = 60.0) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def stop(self) -> None:
        self._stopping = True
        if self._supervisor:
            self._supervisor.cancel()
        if self.rpc:
            await self.rpc.stop()
        self._set_status("stopped")

    async def restart(self) -> None:
        """Restart the app-server (for example after changing the binary)."""
        if self.rpc:
            await self.rpc.stop()

    async def _supervise(self) -> None:
        while not self._stopping:
            try:
                await self._launch()
                self._restart_attempts = 0
                assert self.rpc is not None
                await self.rpc.closed.wait()
                await self.rpc.stop()
                if self._stopping:
                    break
                tail = "\n".join(self.rpc.stderr_tail[-5:])
                self.last_error = f"Codex engine exited (code {self.rpc.returncode}). {tail}".strip()
                logger.warning(self.last_error)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc) or type(exc).__name__
                logger.error("Codex engine failed to start: %s", self.last_error)
                if self.rpc:
                    await self.rpc.stop()
            self._ready.clear()
            self._on_engine_down()
            if self._stopping:
                break
            self._restart_attempts += 1
            delay = min(60, [1, 2, 5, 10, 20, 30][min(self._restart_attempts - 1, 5)])
            self._set_status("restarting" if self.binary else "error")
            await asyncio.sleep(delay)

    async def _launch(self) -> None:
        self._set_status("starting")
        binary = await asyncio.to_thread(find_codex, self.binary_override)
        if binary is None:
            self.binary = None
            raise RuntimeError(
                "Codex CLI not found. Install it with `npm i -g @openai/codex` or install the Codex desktop app, "
                "then sign in with `codex login`."
            )
        self.binary = binary
        logger.info("Starting Codex app-server %s (%s) from %s", binary.version_str, binary.source, binary.path)
        self.features = await asyncio.to_thread(detect_features, binary)
        rpc = JsonRpcProcess([*binary.command(), "app-server"], env=binary.env(), cwd=str(paths.workspace_dir()))
        rpc.on_notification(self._on_notification)
        rpc.on_request(self._on_request)
        await rpc.start()
        self.rpc = rpc
        init = await rpc.request("initialize", {
            "clientInfo": {"name": "weebo", "title": "Weebo", "version": __version__},
            "capabilities": {
                "experimentalApi": True,
                "requestAttestation": False,
                "optOutNotificationMethods": ["item/reasoning/textDelta", "rawResponseItem/completed"],
            },
        }, timeout=60)
        await rpc.notify("initialized")
        self.user_agent = (init or {}).get("userAgent", "")
        self.codex_home = (init or {}).get("codexHome", "")
        self.generation += 1
        self.loaded_threads.clear()
        await self.refresh_account()
        if self.account is None:
            self._set_status("login_required")
        else:
            await self._after_login()
        self._ready.set()

    async def _after_login(self) -> None:
        try:
            await self.refresh_models()
        except Exception as exc:
            logger.warning("Could not list models: %s", exc)
        try:
            await self.refresh_rate_limits()
        except Exception as exc:
            logger.debug("Could not read rate limits: %s", exc)
        self._set_status("ready")

    def _on_engine_down(self) -> None:
        self.loaded_threads.clear()
        for thread_id, route in list(self._routes.items()):
            for listener in list(route.listeners):
                try:
                    result = listener("engine/closed", {"threadId": thread_id, "message": self.last_error})
                    if asyncio.iscoroutine(result):
                        asyncio.create_task(result)
                except Exception:
                    logger.exception("Thread listener failed during engine shutdown")

    def _set_status(self, status: str) -> None:
        if self.status != status:
            self.status = status
            self.bus.publish("engine.status", self.snapshot())

    # ------------------------------------------------------------------ state
    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "error": self.last_error if self.status != "ready" else "",
            "version": self.binary.version_str if self.binary else None,
            "binary": self.binary.path if self.binary else None,
            "source": self.binary.source if self.binary else None,
            "account": self.account,
            "rateLimits": self.rate_limits,
            "models": [
                {
                    "id": m.get("id"),
                    "name": m.get("displayName") or m.get("id"),
                    "description": m.get("description", ""),
                    "efforts": [e.get("reasoningEffort") for e in m.get("supportedReasoningEfforts", [])],
                    "defaultEffort": m.get("defaultReasoningEffort"),
                    "isDefault": m.get("isDefault", False),
                    "serviceTiers": [t.get("id") for t in m.get("serviceTiers", []) or []],
                    "images": "image" in (m.get("inputModalities") or []),
                }
                for m in self.models
            ],
            "features": self.features.__dict__,
        }

    async def refresh_account(self) -> dict[str, Any] | None:
        result = await self.request("account/read", {"refreshToken": False}, timeout=30)
        self.account = (result or {}).get("account")
        return self.account

    async def refresh_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        cursor = None
        for _ in range(10):
            result = await self.request("model/list", {"cursor": cursor, "limit": 50}, timeout=30) or {}
            models.extend(result.get("data") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self.models = models
        return models

    async def refresh_rate_limits(self) -> dict[str, Any] | None:
        result = await self.request("account/rateLimits/read", None, timeout=30) or {}
        self.rate_limits = result.get("rateLimits")
        self.bus.publish("engine.usage", {"rateLimits": self.rate_limits})
        return self.rate_limits

    def usage_percent(self) -> float:
        """Highest used-percent across the plan's rate-limit windows (0 if unknown)."""
        limits = self.rate_limits or {}
        values = [
            (limits.get(k) or {}).get("usedPercent")
            for k in ("primary", "secondary")
        ]
        numbers = [float(v) for v in values if isinstance(v, (int, float))]
        return max(numbers) if numbers else 0.0

    def default_model(self, preferred: str = "") -> str | None:
        ids = [m.get("id") for m in self.models]
        if preferred and (not ids or preferred in ids):
            return preferred
        for model in self.models:
            if model.get("isDefault"):
                return model.get("id")
        return ids[0] if ids else (preferred or None)

    def model_info(self, model_id: str | None) -> dict[str, Any] | None:
        for model in self.models:
            if model.get("id") == model_id:
                return model
        return None

    def clamp_effort(self, model_id: str | None, effort: str) -> str | None:
        info = self.model_info(model_id)
        if not info:
            return effort
        supported = [e.get("reasoningEffort") for e in info.get("supportedReasoningEfforts", [])]
        if not supported or effort in supported:
            return effort
        order = ["minimal", "low", "medium", "high", "xhigh", "ultra"]
        if effort in order:
            target = order.index(effort)
            ranked = sorted(supported, key=lambda e: abs(order.index(e) - target) if e in order else 99)
            return ranked[0]
        return info.get("defaultReasoningEffort")

    # ------------------------------------------------------------------ login
    async def login_start(self) -> dict[str, Any]:
        return await self.request("account/login/start", {"type": "chatgpt"}, timeout=30)

    async def logout(self) -> None:
        await self.request("account/logout", None, timeout=30)
        self.account = None
        self._set_status("login_required")

    # ------------------------------------------------------------------ requests
    async def request(self, method: str, params: Any = None, timeout: float | None = 120.0) -> Any:
        if self.rpc is None or not self.rpc.running:
            raise EngineClosed(self.last_error or "Codex engine is not running")
        return await self.rpc.request(method, params, timeout=timeout)

    async def ensure_ready(self, timeout: float = 45.0) -> None:
        if self.status == "ready" and self.rpc and self.rpc.running:
            return
        if not await self.wait_ready(timeout):
            raise EngineClosed(self.last_error or "Codex engine is still starting")
        if self.status == "login_required":
            raise EngineClosed("Sign in to Codex with your ChatGPT account first (Settings → Codex → Sign in).")
        if self.status != "ready":
            raise EngineClosed(self.last_error or f"Codex engine is {self.status}")

    # ------------------------------------------------------------------ threads
    def route(self, thread_id: str, listener: ThreadListener | None = None,
              request_handler: ThreadRequestHandler | None = None) -> None:
        """Set the owner of a thread. Re-routing replaces the previous listener, so calling this on every
        turn can never deliver an event twice (that duplicated streamed text in the UI)."""
        route = self._routes.setdefault(thread_id, ThreadRoute())
        if listener:
            route.listeners = [listener]
        if request_handler:
            route.request_handler = request_handler

    def unroute(self, thread_id: str) -> None:
        self._routes.pop(thread_id, None)

    async def start_thread(self, **params: Any) -> dict[str, Any]:
        await self.ensure_ready()
        clean = {k: v for k, v in params.items() if v is not None}
        if not self.features.dynamic_tools:
            clean.pop("dynamicTools", None)
        result = await self.request("thread/start", clean, timeout=90)
        thread_id = result["thread"]["id"]
        self.loaded_threads.add(thread_id)
        return result

    async def resume_thread(self, thread_id: str, **params: Any) -> dict[str, Any]:
        await self.ensure_ready()
        clean = {k: v for k, v in params.items() if v is not None}
        clean["threadId"] = thread_id
        clean.setdefault("excludeTurns", True)
        result = await self.request("thread/resume", clean, timeout=90)
        self.loaded_threads.add(thread_id)
        return result

    async def start_turn(self, thread_id: str, input_items: list[dict[str, Any]],
                         context: str = "", **overrides: Any) -> dict[str, Any]:
        await self.ensure_ready()
        params: dict[str, Any] = {"threadId": thread_id, "input": list(input_items)}
        if context:
            if self.features.additional_context:
                params["additionalContext"] = {"weebo": {"kind": "application", "value": context}}
            else:
                params["input"] = [text_input(f"<weebo_context>\n{context}\n</weebo_context>"), *params["input"]]
        params.update({k: v for k, v in overrides.items() if v is not None})
        result = await self.request("turn/start", params, timeout=90)
        return result["turn"]

    async def steer(self, thread_id: str, turn_id: str, input_items: list[dict[str, Any]]) -> None:
        await self.request("turn/steer", {"threadId": thread_id, "expectedTurnId": turn_id, "input": input_items}, timeout=30)

    async def interrupt(self, thread_id: str, turn_id: str) -> None:
        await self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=30)

    async def set_thread_name(self, thread_id: str, name: str) -> None:
        try:
            await self.request("thread/name/set", {"threadId": thread_id, "name": name[:80]}, timeout=15)
        except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
            logger.debug("thread/name/set failed: %s", exc)

    async def archive_thread(self, thread_id: str) -> None:
        try:
            await self.request("thread/archive", {"threadId": thread_id}, timeout=15)
        except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
            logger.debug("thread/archive failed: %s", exc)
        self.loaded_threads.discard(thread_id)

    async def start_review(self, thread_id: str, target: dict[str, Any]) -> dict[str, Any]:
        return await self.request("review/start", {"threadId": thread_id, "target": target, "delivery": "inline"}, timeout=90)

    async def set_skill_roots(self, roots: list[str]) -> None:
        if not self.features.skills_extra_roots:
            return
        try:
            await self.request("skills/extraRoots/set", {"extraRoots": roots}, timeout=15)
        except (RpcError, EngineClosed, asyncio.TimeoutError) as exc:
            logger.debug("skills/extraRoots/set failed: %s", exc)

    # ------------------------------------------------------------------ dispatch
    async def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "account/rateLimits/updated":
            self.rate_limits = params.get("rateLimits")
            self.bus.publish("engine.usage", {"rateLimits": self.rate_limits})
            return
        if method in ("account/updated", "account/login/completed"):
            asyncio.create_task(self._account_changed())
            return
        thread_id = _thread_id_of(params)
        if thread_id is None:
            if method in ("error", "configWarning", "deprecationNotice"):
                logger.debug("Codex %s: %s", method, json.dumps(params)[:400])
            return
        route = self._routes.get(thread_id)
        if route is None:
            return
        for listener in list(route.listeners):
            try:
                result = listener(method, params)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Thread listener failed for %s", method)

    async def _account_changed(self) -> None:
        try:
            await self.refresh_account()
        except Exception as exc:
            logger.debug("account refresh failed: %s", exc)
            return
        if self.account is not None and self.status == "login_required":
            await self._after_login()
        elif self.account is None and self.status == "ready":
            self._set_status("login_required")
        self.bus.publish("engine.status", self.snapshot())

    async def _on_request(self, method: str, params: dict[str, Any]) -> Any:
        thread_id = _thread_id_of(params)
        route = self._routes.get(thread_id) if thread_id else None
        if route and route.request_handler:
            return await route.request_handler(method, params)
        # Nobody owns this thread: answer conservatively so Codex never hangs.
        if method in APPROVAL_METHODS:
            if method == "item/permissions/requestApproval":
                return {"permissions": {}, "scope": "turn"}
            if method in ("execCommandApproval", "applyPatchApproval"):
                return {"decision": "denied"}
            return {"decision": "decline"}
        if method == "item/tool/call":
            return {"contentItems": [{"type": "inputText", "text": "Tool unavailable."}], "success": False}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline", "content": None}
        raise RpcError(-32601, f"Weebo does not handle {method}")


def text_input(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "text_elements": []}


def image_input(path: str) -> dict[str, Any]:
    return {"type": "localImage", "path": path}


def _thread_id_of(params: dict[str, Any]) -> str | None:
    if not isinstance(params, dict):
        return None
    thread_id = params.get("threadId") or params.get("conversationId")
    if thread_id:
        return thread_id
    thread = params.get("thread")
    if isinstance(thread, dict):
        return thread.get("id")
    return None


def detect_features(binary: CodexBinary) -> EngineFeatures:
    """Read the engine's own protocol schema to learn which optional fields it accepts."""
    cache_file = paths.cache_dir() / f"codex-features-{binary.version_str}.json"
    if cache_file.exists():
        try:
            return EngineFeatures(**json.loads(cache_file.read_text(encoding="utf-8")))
        except (OSError, TypeError, json.JSONDecodeError):
            pass
    features = EngineFeatures()
    out_dir = paths.cache_dir() / f"codex-schema-{binary.version_str}"
    try:
        subprocess.run(
            [*binary.command(), "app-server", "generate-json-schema", "--experimental", "--out", str(out_dir)],
            capture_output=True, timeout=60, env=binary.env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        bundle = out_dir / "codex_app_server_protocol.v2.schemas.json"
        defs = json.loads(bundle.read_text(encoding="utf-8")).get("definitions", {})
        turn_props = defs.get("TurnStartParams", {}).get("properties", {})
        thread_props = defs.get("ThreadStartParams", {}).get("properties", {})
        features.additional_context = "additionalContext" in turn_props
        features.output_schema = "outputSchema" in turn_props
        features.dynamic_tools = "dynamicTools" in thread_props
        features.skills_extra_roots = "SkillsExtraRootsSetParams" in defs
        cache_file.write_text(json.dumps(features.__dict__), encoding="utf-8")
    except Exception as exc:  # feature detection is best effort
        logger.warning("Could not detect Codex features (%s); using conservative defaults", exc)
    return features


def now_ms() -> int:
    return int(time.time() * 1000)


def ensure_dir(path: str | Path) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return str(Path(path).resolve())
