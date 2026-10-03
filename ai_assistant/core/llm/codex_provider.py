"""Codex provider for the Weebo 1.x LLM layer.

Routes every legacy LLM call (router, ollama/gemini/deepseek clients) through the
Codex app-server, which runs on the user's ChatGPT plan instead of pay-per-token
API billing.

Inside Weebo 2.0 the running app installs its own engine via ``install_backend``
so legacy tools share one Codex process. Run standalone (``python web_app.py``),
the first call lazily starts a private Codex engine in the caller's event loop.
Enable with ``LLM_PROVIDER=codex`` or ``WEEBO_LLM_BACKEND=codex`` (Weebo 2.0 sets it).
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import os
import tempfile
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ai_assistant.core.llm.provider import LLMProvider

Backend = Callable[..., Awaitable[str]]

_backend: Optional[Backend] = None
_backend_loop: Optional[asyncio.AbstractEventLoop] = None
_starting: Optional[asyncio.Future] = None


def codex_enabled() -> bool:
    if _backend is not None:
        return True
    for var in ("WEEBO_LLM_BACKEND", "LLM_PROVIDER"):
        value = os.environ.get(var, "").strip().lower()
        if value:
            return value == "codex"
    try:  # the legacy config's default provider (codex unless overridden)
        from ai_assistant import config
        return str(getattr(config, "LLM_PROVIDER", "")).lower() == "codex"
    except Exception:
        return False


def install_backend(backend: Backend, loop: asyncio.AbstractEventLoop) -> None:
    """Called by Weebo 2.0: ``backend(prompt, images=[paths], json_mode=bool, task_name=str) -> str``."""
    global _backend, _backend_loop
    _backend, _backend_loop = backend, loop
    os.environ["WEEBO_LLM_BACKEND"] = "codex"


def _compose(prompt: str, system_instruction: Optional[str], history: Optional[List[Dict[str, str]]],
             json_mode: bool) -> str:
    parts: list[str] = []
    if system_instruction:
        parts.append(f"Instructions:\n{system_instruction}")
    if history:
        lines = [f"{str(m.get('role', 'user')).upper()}: {m.get('content', '')}" for m in history]
        parts.append("Conversation so far:\n" + "\n".join(lines))
    parts.append(prompt if not parts else f"Request:\n{prompt}")
    if json_mode:
        parts.append("Respond with valid JSON only, no prose and no code fences.")
    return "\n\n".join(parts)


def _image_paths(images: Optional[List[str]], temporary: Optional[list[str]] = None) -> list[str]:
    """File paths for the images; base64 images are written to temp files (listed in ``temporary``)."""
    paths: list[str] = []
    for image in images or []:
        if not image:
            continue
        if os.path.isfile(image):
            paths.append(image)
            continue
        data = image.split(",", 1)[1] if image.startswith("data:") else image
        try:
            raw = base64.b64decode(data, validate=False)
        except (ValueError, TypeError):
            continue
        handle = tempfile.NamedTemporaryFile(prefix="weebo-legacy-img-", suffix=".png", delete=False)
        with handle:
            handle.write(raw)
        paths.append(handle.name)
        if temporary is not None:
            temporary.append(handle.name)
    return paths


def _remove(files: list[str]) -> None:
    for name in files:
        try:
            os.unlink(name)
        except OSError:
            pass


async def _start_standalone() -> None:
    """Legacy app running without Weebo 2.0: start a private Codex engine on first use."""
    from weebo.codex.engine import CodexEngine
    from weebo.codex.oneshot import ask
    from weebo.events import EventBus

    loop = asyncio.get_running_loop()
    bus = EventBus()
    bus.bind_loop(loop)
    engine = CodexEngine(bus)
    await engine.start()
    await engine.ensure_ready(120)

    async def call(prompt: str, images: Optional[list[str]] = None, json_mode: bool = False,
                   task_name: str = "unknown") -> str:
        return await ask(engine, prompt, images=images, effort="low", service_name="weebo-legacy")

    install_backend(call, loop)


async def codex_generate(prompt: str, system_instruction: Optional[str] = None,
                         history: Optional[List[Dict[str, str]]] = None, images: Optional[List[str]] = None,
                         json_mode: bool = False, task_name: str = "unknown") -> str:
    full_prompt = _compose(prompt, system_instruction, history, json_mode)
    temporary: list[str] = []
    image_paths = _image_paths(images, temporary)
    try:
        global _starting
        if _backend is None:
            if _starting is None:
                _starting = asyncio.ensure_future(_start_standalone())
            try:
                await _starting
            except Exception:
                _starting = None
                raise
        backend, backend_loop = _backend, _backend_loop
        assert backend is not None
        current = asyncio.get_running_loop()
        coro = backend(full_prompt, images=image_paths, json_mode=json_mode, task_name=task_name)
        if backend_loop is None or backend_loop is current:
            return await coro
        future = asyncio.run_coroutine_threadsafe(coro, backend_loop)
        return await asyncio.wrap_future(future)
    finally:
        _remove(temporary)  # decoded base64 images only; caller-owned files are never touched


def codex_generate_sync(prompt: str, system_instruction: Optional[str] = None,
                        history: Optional[List[Dict[str, str]]] = None, images: Optional[List[str]] = None,
                        json_mode: bool = False, task_name: str = "unknown", timeout: float = 600.0) -> str:
    """Blocking variant for legacy sync code (always called from worker threads)."""
    backend_loop = _backend_loop
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if backend_loop is not None and backend_loop.is_running():
        if running is backend_loop:
            raise RuntimeError("codex_generate_sync was called on the event loop thread; use codex_generate.")
        future = asyncio.run_coroutine_threadsafe(
            codex_generate(prompt, system_instruction, history, images, json_mode, task_name), backend_loop)
        return future.result(timeout)
    if running is not None:
        # A loop is running in this thread but it isn't the backend loop: run in a helper thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, codex_generate(prompt, system_instruction, history, images,
                                                           json_mode, task_name)).result(timeout)
    return asyncio.run(codex_generate(prompt, system_instruction, history, images, json_mode, task_name))


class CodexProvider(LLMProvider):
    async def generate_response(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        images: Optional[List[str]] = None,
        endpoint_url: Optional[str] = None,
    ) -> str:
        return await codex_generate(prompt, system_instruction, history, images)

    @property
    def provider_name(self) -> str:
        return "codex"


def describe() -> Dict[str, Any]:
    return {"enabled": codex_enabled(), "shared_engine": _backend is not None}
