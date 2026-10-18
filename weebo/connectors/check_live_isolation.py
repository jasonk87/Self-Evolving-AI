"""Check native scoping using this PC's actual Codex configuration and sign-in.

python -m weebo.connectors.check_live_isolation [--resume-turns]

No real outward call or user configuration edit. By default no model turns;
--resume-turns uses two harmless turns to verify persisted resume and archives
the diagnostic threads afterwards. Only synthetic marker tools are called. App checks inspect effective runtime
policy, not the unfiltered discovery catalog. Credentials and account data are
never printed. A pass does NOT certify outward approvals or an OS sandbox.
"""
from __future__ import annotations

import asyncio
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from .. import paths
from ..brain.interactions import Interactions
from ..codex.engine import CodexEngine, detect_features
from ..codex.locator import find_codex
from ..codex.rpc import JsonRpcProcess, RpcError
from ..events import EventBus
from ..config import Settings
from .registry import Connectors


def callable_apps(snapshot: dict) -> set[str]:
    if not isinstance(snapshot.get("apps"), list):
        raise ValueError("App runtime snapshot unavailable")
    return {a["id"] for a in snapshot["apps"] if a.get("callable")}


async def inventory(rpc, thread_id):
    rows, cursor = [], None
    for _ in range(30):
        page = await rpc.request("mcpServerStatus/list", {"threadId": thread_id, "cursor": cursor,
                                                         "limit": 100, "detail": "toolsAndAuthOnly"}, timeout=60)
        rows.extend(page.get("data") or [])
        cursor = page.get("nextCursor")
        if not cursor:
            return rows
    raise ValueError("Native inventory pagination incomplete")


async def rejected_marker(rpc, thread, server):
    try:
        result = await rpc.request("mcpServer/tool/call", {"threadId": thread, "server": server,
            "tool": server + "_marker", "arguments": {}}, timeout=20)
        return bool(result.get("isError") or result.get("error"))
    except RpcError:
        return True


async def check(binary, resume_turns: bool = False) -> dict:
    features = await asyncio.to_thread(detect_features, binary)
    if not features.connector_controls:
        return {"result": "NOT_TESTED", "reason": "Installed Codex lacks verified connector controls"}
    cwd = str(paths.workspace_dir())
    rpc = JsonRpcProcess([*binary.command(), "app-server"], env=binary.env(), cwd=cwd)
    last_method = "startup"
    request = rpc.request

    async def tracked_request(method, *args, **kwargs):
        nonlocal last_method
        last_method = method
        return await request(method, *args, **kwargs)

    rpc.request = tracked_request

    async def decline(method, params):
        return Interactions.respond(method, params, "decline")

    rpc.on_request(decline)
    created_threads = []
    completed = {}

    async def notification(method, params):
        if method == "turn/completed" and params.get("threadId") in completed:
            event, state = completed[params["threadId"]]
            state.update(params.get("turn") or {})
            event.set()

    rpc.on_notification(notification)
    try:
        await rpc.start()
        await rpc.request("initialize", {"clientInfo": {"name": "weebo-live-isolation", "version": "1"},
                                          "capabilities": {"experimentalApi": True}}, timeout=30)
        await rpc.notify("initialized")
        engine = CodexEngine(EventBus())
        engine.rpc, engine.status, engine.features = rpc, "ready", features
        if resume_turns:
            await engine.refresh_models()
        model = engine.default_model(Settings().get("codex.model")) if resume_turns else None
        inherited = await engine.connector_inventory(cwd)
        connectors = Connectors(SimpleNamespace(engine=engine))
        server = str(Path(__file__).with_name("probe_server.py"))
        rows = [{"id": key, "kind": "mcp", "status": "approved", "data": {
            "key": key, "transport": "stdio", "command": [sys.executable, "-I", server, key],
            "tools": [{"name": key + "_marker", "risk": "read"},
                      {"name": "outward_action", "risk": "contact_people"}]}}
                for key in ("weebo_probe_a", "weebo_probe_b")]
        connectors.get = lambda key: next((r for r in rows if r["id"] == key), None)

        async def start(assigned, persistent=False):
            result = await rpc.request("thread/start", {"cwd": cwd, "ephemeral": not persistent,
                "approvalPolicy": "on-request", "sandbox": "read-only",
                **connectors.thread_config({"data": {"connectors": assigned}}, inherited)}, timeout=60)
            thread_id = result["thread"]["id"]
            if persistent:
                created_threads.append(thread_id)
            return thread_id

        checks, details = {}, {}
        for label in ("a", "b"):
            key, other = "weebo_probe_" + label, "weebo_probe_" + ("b" if label == "a" else "a")
            thread = await start([key], persistent=resume_turns)
            native = await inventory(rpc, thread)
            visible = {row["name"] for row in native if row.get("tools")}
            details[f"bot_{label}_visible_servers"] = sorted(visible)
            checks[f"bot_{label}_only_assigned_server"] = visible == {key}
            checks[f"bot_{label}_no_outward_tools"] = all("outward_action" not in (r.get("tools") or {}) for r in native)
            result = await rpc.request("mcpServer/tool/call", {"threadId": thread, "server": key,
                "tool": key + "_marker", "arguments": {}}, timeout=20)
            checks[f"bot_{label}_positive_read"] = not result.get("isError") and key in json.dumps(result)
            checks[f"bot_{label}_other_read_rejected"] = await rejected_marker(rpc, thread, other)
            if resume_turns:
                event, state = asyncio.Event(), {}
                completed[thread] = (event, state)
                turn = await rpc.request("turn/start", {"threadId": thread, "model": model,
                    "effort": engine.clamp_effort(model, "low"), "input": [{"type": "text",
                    "text": "Reply with READY only. Do not use tools or take any actions."}]}, timeout=60)
                try:
                    await asyncio.wait_for(event.wait(), 60)
                    checks[f"bot_{label}_seed_completed"] = state.get("status") == "completed"
                finally:
                    if not event.is_set():
                        await rpc.request("turn/interrupt", {"threadId": thread, "turnId": turn["turn"]["id"]}, timeout=15)
                await rpc.request("thread/unsubscribe", {"threadId": thread}, timeout=15)
                if label == "a":
                    # A real app-server restart, not just an in-memory resume.
                    # Only our diagnostic process is stopped, never Weebo/Codex UI.
                    await rpc.stop()
                    rpc = JsonRpcProcess([*binary.command(), "app-server"], env=binary.env(), cwd=cwd)
                    request = rpc.request
                    rpc.request = tracked_request
                    rpc.on_request(decline)
                    rpc.on_notification(notification)
                    await rpc.start()
                    await rpc.request("initialize", {"clientInfo": {"name": "weebo-live-isolation", "version": "1"},
                        "capabilities": {"experimentalApi": True}}, timeout=30)
                    await rpc.notify("initialized")
                    engine.rpc = rpc
                await rpc.request("thread/resume", {"threadId": thread, "cwd": cwd, "excludeTurns": True,
                    **connectors.thread_config({"data": {"connectors": [key]}}, inherited)}, timeout=60)
                checks[f"bot_{label}_resume_scoped"] = {r["name"] for r in await inventory(rpc, thread) if r.get("tools")} == {key}
                if label == "a":
                    checks["app_server_restart_scoped"] = checks[f"bot_{label}_resume_scoped"]
        # Production revocation replaces a loaded thread (via tools_hash), not
        # a resume of that same loaded thread, which Codex may merely rejoin.
        revoked = await start([])
        checks["revoked_thread_empty"] = not any(r.get("tools") for r in await inventory(rpc, revoked))
        checks["revoked_marker_rejected"] = await rejected_marker(rpc, revoked, "weebo_probe_a")

        # app/installed reports effective permissions. mcpServerStatus/list is
        # deliberately NOT used to infer app callability: it returns raw tools.
        snapshot = await rpc.request("app/installed", {"forceRefresh": True}, timeout=60)
        candidates = [a for a in snapshot.get("apps", []) if a.get("callable")]
        selected = [(a["id"], inherited["read_tools"][a["id"]][0]) for a in candidates
                    if inherited.get("read_tools", {}).get(a["id"])][:2]
        app_checks = {}
        for index, (app_id, tool) in enumerate(selected):
            key = "app_probe_" + str(index)
            rows.append({"id": key, "kind": "app", "status": "approved",
                         "data": {"app_id": app_id, "tools": [{"name": tool, "risk": "read"}]}})
            thread = await start([key])
            runtime = await rpc.request("app/installed", {"threadId": thread, "forceRefresh": False}, timeout=60)
            details[f"app_{index}_tool"] = tool
            details[f"app_{index}_callable_count"] = len(callable_apps(runtime))
            app_checks[f"app_{index}_only_assigned_callable"] = callable_apps(runtime) == {app_id}
            native = await inventory(rpc, thread)
            app_checks[f"app_{index}_no_plugin_mcp_leak"] = all(
                r["name"] == "codex_apps" or not r.get("tools") for r in native)
        disabled = await rpc.request("app/installed", {"threadId": revoked, "forceRefresh": False}, timeout=60)
        checks["revoked_apps_not_callable"] = not callable_apps(disabled)
        ok = all(checks.values()) and len(selected) == 2 and all(app_checks.values())
        return {"version": binary.version_str, "result": "PASS" if ok else "INCOMPLETE_OR_FAILED",
                "inherited_mcp_count": len(inherited["mcp"]), "inherited_plugin_count": len(inherited["plugins"]),
                "checks": checks, "real_app_policy_checks": app_checks,
                "details": details,
                "real_app_count_tested": len(selected), "outward_approvals_and_os_sandbox": "NOT_TESTED",
                "persisted_resume": "TESTED" if resume_turns else "NOT_TESTED",
                "real_messages_sent": 0}
    except Exception as exc:
        # Real subprocess errors can contain credentials/URLs/account details.
        return {"result": "NOT_TESTED", "error_type": type(exc).__name__, "method": last_method,
                "error_code": exc.code if isinstance(exc, RpcError) else None,
                "error_categories": [hint for hint in ("missing field", "command", "transport", "duplicate", "invalid", "config")
                                     if hint in str(exc).lower()]}
    finally:
        for thread_id in created_threads:
            try:
                await rpc.request("thread/archive", {"threadId": thread_id}, timeout=15)
            except (RpcError, RuntimeError, asyncio.TimeoutError):
                pass
        await rpc.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-turns", action="store_true", help="Use two real Codex turns to check persisted resume")
    args = parser.parse_args()
    binary = find_codex()
    if not binary:
        print(json.dumps({"result": "NOT_TESTED", "reason": "Codex not found"}))
        return 2
    report = asyncio.run(check(binary, args.resume_turns))
    print(json.dumps(report, indent=2))
    return 0 if report["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
