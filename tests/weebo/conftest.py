"""Shared fixtures for Weebo 2.0 tests: isolated data dir + a scriptable fake Codex engine."""

from __future__ import annotations

import asyncio
import itertools
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("WEEBO_DISABLE_LEGACY", "1")
os.environ.setdefault("WEEBO_SKIP_LEGACY_IMPORT", "1")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "weebo_data"
    monkeypatch.setenv("WEEBO_DATA_DIR", str(path))
    return path


class FakeEngine:
    """Stands in for CodexEngine. Tests drive it by emitting notifications/requests."""

    def __init__(self) -> None:
        from weebo.codex.engine import EngineFeatures

        self.status = "ready"
        self.features = EngineFeatures(additional_context=True, dynamic_tools=True, output_schema=True)
        self.models = [{"id": "gpt-test", "displayName": "GPT Test", "isDefault": True,
                        "supportedReasoningEfforts": [{"reasoningEffort": e} for e in ("low", "medium", "high")],
                        "defaultReasoningEffort": "medium", "serviceTiers": []}]
        self.rate_limits = {"primary": {"usedPercent": 10}}
        self.loaded_threads: set[str] = set()
        self.routes: dict[str, tuple[Any, Any]] = {}
        self.threads: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self.steers: list[dict[str, Any]] = []
        self.interrupts: list[tuple[str, str]] = []
        self.reviews: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        self.on_turn = None  # optional async callback(engine, thread_id, turn_id, params)
        self.binary_override = ""

    # --- surface used by Weebo -------------------------------------------------
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def restart(self) -> None: ...
    async def wait_ready(self, timeout: float = 0) -> bool: return True
    async def ensure_ready(self, timeout: float = 0) -> None: ...
    async def set_skill_roots(self, roots) -> None: ...
    async def set_thread_name(self, thread_id, name) -> None: ...
    async def archive_thread(self, thread_id) -> None: self.loaded_threads.discard(thread_id)
    async def refresh_rate_limits(self): return self.rate_limits
    async def refresh_models(self): return self.models

    def snapshot(self) -> dict[str, Any]:
        return {"status": self.status, "models": [], "rateLimits": self.rate_limits, "account": None,
                "version": "0.0.0", "binary": None, "source": "fake", "error": "", "features": {}}

    def usage_percent(self) -> float:
        return float((self.rate_limits or {}).get("primary", {}).get("usedPercent", 0))

    def default_model(self, preferred: str = "") -> str:
        return preferred or "gpt-test"

    def model_info(self, model_id):
        return self.models[0] if model_id == "gpt-test" else None

    def clamp_effort(self, model_id, effort):
        return effort

    def route(self, thread_id, listener=None, request_handler=None) -> None:
        self.routes[thread_id] = (listener, request_handler)

    def unroute(self, thread_id) -> None:
        self.routes.pop(thread_id, None)

    async def start_thread(self, **params):
        thread_id = f"thread-{next(self._ids)}"
        self.loaded_threads.add(thread_id)
        self.threads.append({"id": thread_id, **params})
        return {"thread": {"id": thread_id}}

    async def resume_thread(self, thread_id, **params):
        self.loaded_threads.add(thread_id)
        self.threads.append({"id": thread_id, "resumed": True, **params})
        return {"thread": {"id": thread_id}}

    async def start_turn(self, thread_id, input_items, context="", **overrides):
        turn_id = f"turn-{next(self._ids)}"
        record = {"thread_id": thread_id, "turn_id": turn_id, "input": input_items, "context": context, **overrides}
        self.turns.append(record)
        if self.on_turn:
            asyncio.get_running_loop().create_task(self.on_turn(self, thread_id, turn_id, record))
        return {"id": turn_id}

    async def steer(self, thread_id, turn_id, input_items):
        self.steers.append({"thread_id": thread_id, "turn_id": turn_id, "input": input_items})

    async def interrupt(self, thread_id, turn_id):
        self.interrupts.append((thread_id, turn_id))
        await self.emit(thread_id, "turn/completed", {"turn": {"id": turn_id, "status": "interrupted"}})

    async def start_review(self, thread_id, target):
        self.reviews.append({"thread_id": thread_id, "target": target})
        return {"turn": {"id": "review-turn"}}

    # --- test helpers ----------------------------------------------------------
    async def emit(self, thread_id: str, method: str, params: dict[str, Any]) -> None:
        listener = self.routes.get(thread_id, (None, None))[0]
        if listener is None:
            return
        params = {"threadId": thread_id, **params}
        result = listener(method, params)
        if asyncio.iscoroutine(result):
            await result

    async def request(self, thread_id: str, method: str, params: dict[str, Any]) -> Any:
        handler = self.routes[thread_id][1]
        return await handler(method, {"threadId": thread_id, **params})

    async def finish_turn(self, thread_id: str, turn_id: str, text: str = "Done.", status: str = "completed") -> None:
        await self.emit(thread_id, "turn/started", {"turn": {"id": turn_id}})
        item = {"type": "agentMessage", "id": f"msg-{turn_id}", "text": text, "phase": "final_answer"}
        await self.emit(thread_id, "item/started", {"turnId": turn_id, "item": {**item, "text": ""}})
        await self.emit(thread_id, "item/agentMessage/delta", {"turnId": turn_id, "itemId": item["id"], "delta": text})
        await self.emit(thread_id, "item/completed", {"turnId": turn_id, "item": item})
        error = {"message": "boom"} if status == "failed" else None
        await self.emit(thread_id, "turn/completed", {"turn": {"id": turn_id, "status": status, "error": error}})


@pytest.fixture
def fake_engine():
    return FakeEngine()


@pytest_asyncio.fixture
async def app(fake_engine):
    from weebo.app import WeeboApp

    weebo = WeeboApp()
    weebo.engine = fake_engine
    await weebo.start(with_engine=False, with_background=False)
    yield weebo
    await weebo.evolution.stop()
    weebo.store.close()


async def drain(cycles: int = 5) -> None:
    for _ in range(cycles):
        await asyncio.sleep(0)
