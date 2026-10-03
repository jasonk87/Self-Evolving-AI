"""Background thinking: one-shot structured Codex calls for reflection and planning."""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

from .. import log, paths
from ..codex.oneshot import OneShotError, ask
from . import persona

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("mind")


class ThinkError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, flags=re.DOTALL)
    if match:
        return json.loads(match.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ThinkError("The model did not return JSON.")


class Mind:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self._slots = asyncio.Semaphore(2)

    async def think(self, prompt: str, schema: dict[str, Any] | None = None, effort: str | None = None,
                    timeout: float = 300.0, label: str = "think", count: bool = True,
                    images: list[str] | None = None, instructions: str | None = None) -> Any:
        engine = self.app.engine
        async with self._slots:
            try:
                text = await ask(
                    engine, prompt,
                    developer_instructions=instructions or persona.developer_instructions(self.app, "background"),
                    model=engine.default_model(self.app.settings.get("codex.model")),
                    effort=effort or self.app.settings.get("codex.background_effort"),
                    schema=schema, images=images, cwd=str(paths.workspace_dir()), timeout=timeout,
                )
            except OneShotError as exc:
                raise ThinkError(f"{label} failed: {exc}") from exc
            finally:
                if count:
                    self.app.count_background_turn(label)
        if schema is None:
            return text
        try:
            return extract_json(text)
        except (ThinkError, json.JSONDecodeError) as exc:
            raise ThinkError(f"{label}: could not parse JSON ({exc})") from exc
