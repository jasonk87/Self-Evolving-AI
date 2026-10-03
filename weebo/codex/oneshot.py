"""One-shot Codex calls: ephemeral read-only thread, one turn, return the final text.

Shared by Weebo's background mind and by the legacy (Weebo 1.x) LLM layer, which
is routed here so every old subsystem also runs on the user's ChatGPT plan.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .engine import CodexEngine, image_input, text_input
from .rpc import EngineClosed, RpcError


class OneShotError(RuntimeError):
    pass


async def ask(engine: CodexEngine, prompt: str, *, developer_instructions: str = "", model: str | None = None,
              effort: str | None = None, schema: dict[str, Any] | None = None, images: list[str] | None = None,
              cwd: str | None = None, timeout: float = 300.0, service_name: str = "weebo-mind") -> str:
    await engine.ensure_ready()
    model = model or engine.default_model()
    if effort:
        effort = engine.clamp_effort(model, effort)
    result = await engine.start_thread(
        cwd=cwd, approvalPolicy="never", sandbox="read-only", ephemeral=True,
        developerInstructions=developer_instructions or None, serviceName=service_name, model=model,
    )
    thread_id = result["thread"]["id"]
    done = asyncio.Event()
    state: dict[str, Any] = {"final": "", "last": "", "status": None, "error": None}

    def listener(method: str, params: dict[str, Any]) -> None:
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                state["last"] = item.get("text", "")
                if item.get("phase") != "commentary":
                    state["final"] = item.get("text", "")
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            state["status"] = turn.get("status")
            state["error"] = (turn.get("error") or {}).get("message")
            done.set()
        elif method == "engine/closed":
            state["status"], state["error"] = "failed", "engine stopped"
            done.set()

    async def deny(method: str, params: dict[str, Any]) -> Any:
        if method == "item/tool/call":
            return {"contentItems": [{"type": "inputText", "text": "No tools in this mode."}], "success": False}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        if method == "item/permissions/requestApproval":
            return {"permissions": {}, "scope": "turn"}
        return {"decision": "decline"}

    engine.route(thread_id, listener=listener, request_handler=deny)
    try:
        overrides: dict[str, Any] = {"model": model}
        if effort:
            overrides["effort"] = effort
        if schema and engine.features.output_schema:
            overrides["outputSchema"] = schema
        inputs = [text_input(prompt)] + [image_input(p) for p in images or []]
        await engine.start_turn(thread_id, inputs, **overrides)
        await asyncio.wait_for(done.wait(), timeout)
    except asyncio.TimeoutError as exc:
        raise OneShotError(f"Codex did not answer within {timeout:.0f}s") from exc
    except (RpcError, EngineClosed) as exc:
        raise OneShotError(str(exc)) from exc
    finally:
        engine.unroute(thread_id)
        engine.loaded_threads.discard(thread_id)
    if state["status"] != "completed":
        raise OneShotError(state["error"] or f"turn {state['status']}")
    return state["final"] or state["last"]
