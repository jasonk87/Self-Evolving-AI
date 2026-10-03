"""Bridge to Weebo 1.x: its tool registry and subsystems, now thinking with Codex.

The legacy ``ToolSystem`` discovers ~120 tools. Most are superseded by Codex's own
abilities (shell, files, patches) or by Weebo 2.0 subsystems (memory, reminders,
agents). The ones that add real abilities (weather, location, Google search, deep
research, image search, Google Calendar, SMS, chart/table widgets) are exposed to
the Codex brain through one dynamic tool. Outward-facing ones need user approval.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import log, paths

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("legacy")

# name -> (risk, one-line description for the model). risk: read | confirm
CATALOG: dict[str, tuple[str, str]] = {
    "get_weather": ("read", "Current weather. Args: location (e.g. 'Austin, TX')."),
    "get_user_location": ("read", "The user's approximate location (GPS if available, else IP). No args."),
    "google_search": ("read", "Google Custom Search with the user's own Google keys. Args: query, num_results."),
    "news_search": ("read", "Fresh, dated news headlines. Args: query, num_results."),
    "execute_deep_research": ("read", "Multi-source research: searches, reads the top pages and synthesizes an answer. Args: query."),
    "web_search_images": ("read", "Finds and downloads images, shown in chat. Args: query, num_images."),
    "check_calendar": ("read", "Google Calendar schedule for a date. Args: date_str ('2026-10-04' or 'today')."),
    "list_upcoming_events": ("read", "Next Google Calendar events. Args: max_results."),
    "schedule_event": ("confirm", "Create a Google Calendar event. Args: summary, datetime_str, duration_mins, description."),
    "send_text_message": ("confirm", "Send an SMS through the user's Twilio account. Args: recipient, message."),
    "generate_bar_chart_html": ("read", "Render a bar chart widget. Args: labels[], values[], title, orientation."),
    "generate_div_table_html": ("read", "Render a table widget. Args: data (list of rows)."),
    "chat_dynamic_html": ("read", "Generate a small interactive HTML widget from an idea. Args: idea."),
}

HTML_TOOLS = {"generate_bar_chart_html", "generate_div_table_html", "chat_dynamic_html"}

_GOOGLE = ("GOOGLE_API_KEY", "GOOGLE_CSE_ID")
_REQUIRED_ENV: dict[str, tuple[str, ...]] = {
    "get_weather": ("OPENWEATHER_API_KEY",),
    "google_search": _GOOGLE,
    "execute_deep_research": _GOOGLE,
    "web_search_images": _GOOGLE,
    "send_text_message": ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"),
}
_CALENDAR_TOOLS = {"check_calendar", "list_upcoming_events", "schedule_event"}


def missing_requirements(name: str) -> list[str]:
    """What a legacy tool still needs before it can work on this machine (empty = ready)."""
    missing = [var for var in _REQUIRED_ENV.get(name, ()) if not os.environ.get(var)]
    if name in _CALENDAR_TOOLS:
        candidates = [paths.PROJECT_ROOT / "ai_assistant" / "core" / "data" / f for f in ("credentials.json", "token.json")]
        candidates += [paths.PROJECT_ROOT / "credentials.json"]
        if not any(c.exists() for c in candidates):
            missing.append("Google Calendar credentials.json")
    return missing


class _ExecutorShim:
    """Stands in for Weebo 1.x's ActionExecutor where legacy tools expect one."""

    def __init__(self, app: "WeeboApp"):
        self.app = app

    async def run_code(self, lang: str = "llm", code: str = "", **_: Any) -> str:
        if lang != "llm":
            raise RuntimeError("Weebo 2.0 runs code through Codex, not the legacy executor.")
        return await self.app.mind.think(code + "\n\nRespond with JSON only.", label="legacy-llm", count=False)


class LegacyBridge:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.tool_system: Any = None
        self.status = "idle"  # idle | loading | ready | error | disabled
        self.error = ""
        self.loaded_at: float | None = None
        self._ready = asyncio.Event()
        self._shim = _ExecutorShim(app)

    # ------------------------------------------------------------------ startup
    def start(self) -> None:
        if os.environ.get("WEEBO_DISABLE_LEGACY") == "1":
            self.status = "disabled"
            self._ready.set()
            return
        os.environ["WEEBO_LLM_BACKEND"] = "codex"
        self.status = "loading"
        asyncio.create_task(self._load(), name="legacy-load")

    async def _codex_backend(self, prompt: str, images: list[str] | None = None, json_mode: bool = False,
                             task_name: str = "unknown") -> str:
        return await self.app.mind.think(prompt, images=images, label=f"legacy:{task_name}", count=False)

    async def _load(self) -> None:
        started = time.perf_counter()
        try:
            from ai_assistant.core.llm import codex_provider
            codex_provider.install_backend(self._codex_backend, asyncio.get_running_loop())
            self.tool_system = await asyncio.to_thread(self._import_tool_system)
            self._forward_legacy_events()
            self.status = "ready"
            self.loaded_at = time.time()
            available = [n for n in CATALOG if n in self.tool_system._tool_registry]
            logger.info("Weebo 1.x bridge ready in %.1fs (%d tools available)", time.perf_counter() - started, len(available))
        except Exception as exc:
            self.status, self.error = "error", f"{type(exc).__name__}: {exc}"
            logger.warning("Weebo 1.x tools unavailable: %s", self.error)
        finally:
            self._ready.set()
            self.app.bus.publish("legacy.status", self.snapshot())

    @staticmethod
    def _import_tool_system() -> Any:
        import logging
        # The legacy stack is chatty on import; keep Weebo's console clean.
        legacy_root = logging.getLogger("ai_assistant")
        previous = legacy_root.level
        legacy_root.setLevel(logging.WARNING)
        sink = io.StringIO()
        try:
            with contextlib.redirect_stdout(sink):
                from ai_assistant.tools import tool_system
            return tool_system.tool_system_instance
        finally:
            legacy_root.setLevel(previous)

    def _forward_legacy_events(self) -> None:
        try:
            from ai_assistant.core.events import EventEmitter
        except Exception:
            return

        def forward(name: str, data: dict[str, Any]) -> None:
            self.app.bus.publish_threadsafe("legacy.event", {"name": name, "data": data})

        EventEmitter.register_listener(forward)

    # ------------------------------------------------------------------ catalog
    def available(self) -> dict[str, tuple[str, str]]:
        if self.tool_system is None:
            return dict(CATALOG)
        registry = self.tool_system._tool_registry
        return {name: meta for name, meta in CATALOG.items() if name in registry}

    @staticmethod
    def describe_catalog() -> str:
        lines = []
        for name, (risk, about) in CATALOG.items():
            missing = missing_requirements(name)
            note = f" UNAVAILABLE (needs {', '.join(missing)}); use another way." if missing else ""
            lines.append(f"- {name}: {about}{' Asks the user first.' if risk == 'confirm' else ''}{note}")
        return "\n".join(lines)

    def snapshot(self) -> dict[str, Any]:
        return {"status": self.status, "error": self.error, "tools": [
            {"name": name, "risk": risk, "about": about, "missing": missing_requirements(name)}
            for name, (risk, about) in self.available().items()
        ]}

    # ------------------------------------------------------------------ execution
    async def run(self, name: str, arguments: dict[str, Any], conversation_id: str | None = None,
                  task_id: str | None = None) -> tuple[str, bool, dict[str, Any]]:
        """Returns (text_for_model, success, extras) where extras may carry html/images for the UI."""
        if name not in CATALOG:
            return f"'{name}' is not an available Weebo 1.x tool. Available: {', '.join(CATALOG)}", False, {}
        if self.status == "disabled":
            return "Weebo 1.x tools are disabled.", False, {}
        try:
            await asyncio.wait_for(self._ready.wait(), 90)
        except asyncio.TimeoutError:
            return "Weebo 1.x tools are still loading; try again in a moment.", False, {}
        if self.tool_system is None:
            return f"Weebo 1.x tools failed to load: {self.error}", False, {}
        if name not in self.tool_system._tool_registry:
            return f"The Weebo 1.x tool '{name}' is not registered on this machine.", False, {}

        missing = missing_requirements(name)
        if missing:
            return (f"{name} isn't set up on this machine (needs {', '.join(missing)}). "
                    "Use another approach, such as your own web search, and tell the user how to enable it."), False, {}
        risk, _about = CATALOG[name]
        if risk == "confirm":
            pretty = json.dumps(arguments, ensure_ascii=False, indent=1)[:1500]
            approved = await self.app.interactions.confirm(
                f"Run Weebo 1.x tool: {name}", f"{name}({pretty})", conversation_id=conversation_id, task_id=task_id)
            if not approved:
                return "The user declined this action.", False, {}

        kwargs = dict(arguments)
        info = self.tool_system._tool_registry[name]
        func = info.get("callable_cache")
        if func is None:
            import importlib
            module = await asyncio.to_thread(importlib.import_module, info["module_path"])
            func = getattr(module, info["function_name"])
        if "action_executor" in inspect.signature(func).parameters:
            kwargs["action_executor"] = self._shim
        try:
            raw = await self.tool_system.execute_tool(name, kwargs=kwargs)
        except Exception as exc:
            self.app.diagnostics.record("legacy_tool_failed", f"Weebo 1.x tool {name} failed: {exc}", {"tool": name})
            return f"{name} failed: {exc}", False, {}
        return self._present(name, raw)

    def _present(self, name: str, raw: Any) -> tuple[str, bool, dict[str, Any]]:
        success, result, error = True, raw, ""
        if isinstance(raw, dict) and "success" in raw:
            success = bool(raw.get("success"))
            result = raw.get("result") if "result" in raw else raw.get("data")
            error = str(raw.get("error") or raw.get("message") or "")
        extras: dict[str, Any] = {}
        if name in HTML_TOOLS and isinstance(result, str) and result.strip():
            html = result.strip()
            if html.startswith("```"):
                html = html.split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in html else ""
            extras["html"] = html
            return "Rendered the widget in the chat for the user.", success, extras
        if name == "web_search_images":
            images = self._publish_images(result)
            if images:
                extras["images"] = images
        if isinstance(result, (dict, list)):
            text = json.dumps(result, ensure_ascii=False, default=str)
        else:
            text = str(result if result is not None else "")
        if not success:
            text = f"{name} reported a failure: {error or text}"
        return text[:20000], success, extras

    def _publish_images(self, result: Any) -> list[str]:
        candidates: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, str) and os.path.isfile(value):
                candidates.append(value)
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(result)
        urls = []
        for path in candidates[:6]:
            src = Path(path)
            if src.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                continue
            dest = paths.uploads_dir() / f"legacy_{int(time.time())}_{src.name}"
            try:
                dest.write_bytes(src.read_bytes())
                urls.append(f"/uploads/{dest.name}")
            except OSError:
                continue
        return urls
