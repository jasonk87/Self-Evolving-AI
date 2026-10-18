"""Read-only Codex protocol + synthetic connector isolation probe.

Run: python -m weebo.connectors.check_isolation --probe
Uses a disposable Codex home and harmless fake MCP servers, no account credentials,
model turns, real connectors, app restart, or persistent configuration changes.
A PASS is NOT certification of isolation for the user's actual ChatGPT apps.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from ..codex.locator import find_codex
from ..codex.rpc import JsonRpcProcess, RpcError
from .launch import connector_environment
from .registry import Connectors


def supported(definitions: dict) -> bool:
    required = {"ThreadStartParams": {"config", "ephemeral"},
                "ListMcpServerStatusParams": {"threadId"},
                "McpServerToolCallParams": {"threadId", "server", "tool"}}
    return all(fields <= set(definitions.get(name, {}).get("properties", {})) for name, fields in required.items())


async def probe(binary, root: Path) -> dict:
    server = str(Path(__file__).with_name("probe_server.py"))
    env = connector_environment()
    env["CODEX_HOME"] = str(root / "codex-home")
    (root / "codex-home").mkdir()
    # These two synthetic servers are the only configuration in this disposable
    # home. No credentials or real ~/.codex config are copied into it.
    config = {"features.apps": False}
    for key in ("allowed", "blocked"):
        config[f"mcp_servers.{key}.command"] = sys.executable
        config[f"mcp_servers.{key}.args"] = ["-I", server, key]
    command = [*binary.command()]
    for key, value in config.items():
        command += ["-c", f"{key}={json.dumps(value)}"]
    rpc = JsonRpcProcess([*command, "app-server"], env=env, cwd=str(root))

    async def decline(method, params):
        from ..brain.interactions import Interactions
        return Interactions.respond(method, params, "decline")

    rpc.on_request(decline)
    thread = None
    try:
        await rpc.start()
        await rpc.request("initialize", {"clientInfo": {"name": "weebo-isolation-check", "version": "1"},
                                          "capabilities": {"experimentalApi": True}}, timeout=30)
        await rpc.notify("initialized")
        # Exercise exactly the production per-thread controls with two harmless
        # connector profiles, not a separate hand-written approximation.
        connectors = Connectors(SimpleNamespace(engine=SimpleNamespace(features=SimpleNamespace(thread_config=True))))
        rows = [{"id": key, "kind": "mcp", "status": "approved", "data": {"key": key,
            "transport": "stdio", "command": [sys.executable, "-I", server, key], "tools": [
                {"name": key + "_marker", "risk": "read"}, {"name": "outward_action", "risk": "contact_people"}]}}
            for key in ("allowed", "blocked")]
        connectors.list = lambda: rows
        connectors.get = lambda key: next((r for r in rows if r["id"] == key), None)
        extra = connectors.thread_config({"data": {"connectors": ["allowed"]}}, {"mcp": ["allowed", "blocked"]})
        result = await rpc.request("thread/start", {"cwd": str(root), "ephemeral": True,
            "approvalPolicy": "on-request", "sandbox": "read-only", "serviceName": "weebo-isolation-check",
            **extra}, timeout=45)
        thread = result["thread"]["id"]
        rows, cursor = [], None
        for _ in range(20):
            result = await rpc.request("mcpServerStatus/list", {"threadId": thread, "cursor": cursor}, timeout=30)
            rows.extend(result.get("data") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        else:
            raise ValueError("Inventory pagination did not finish")
        allowed = any(r.get("name") == "allowed" and r.get("tools") for r in rows)
        hidden = not any(r.get("name") == "blocked" and r.get("tools") for r in rows)
        # Positive control: rejecting every direct call is not isolation proof.
        positive = await rpc.request("mcpServer/tool/call", {"threadId": thread, "server": "allowed",
                                                            "tool": "allowed_marker", "arguments": {}}, timeout=15)
        allowed_call = not (positive.get("isError") or positive.get("error")) and "allowed" in json.dumps(positive)
        blocked_call = False
        try:
            result = await rpc.request("mcpServer/tool/call", {"threadId": thread, "server": "blocked",
                                                              "tool": "blocked_marker", "arguments": {}}, timeout=15)
            blocked_call = bool(result.get("isError") or result.get("error"))
        except RpcError:
            blocked_call = True
        return {"allowed_tools_visible": bool(allowed), "allowed_call_succeeded": allowed_call, "blocked_tools_hidden": hidden,
                "blocked_call_rejected": blocked_call,
                "synthetic_isolation": "PASS" if allowed and allowed_call and hidden and blocked_call else "FAIL",
                "real_apps_and_pre_execution_approval": "NOT_TESTED"}
    except Exception as exc:
        # This child has only disposable config and synthetic tools, no secrets.
        raise RuntimeError("; ".join(rpc.stderr_tail)[-1000:] or type(exc).__name__) from exc
    finally:
        # Ephemeral + stopping this dedicated server removes all probe threads.
        await rpc.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default="", help="Optional exact Codex executable")
    parser.add_argument("--probe", action="store_true", help="Also run the harmless native-server probe")
    args = parser.parse_args()
    binary = find_codex(args.binary)
    if not binary:
        print(json.dumps({"result": "NOT_TESTED", "reason": "Codex binary not found"}))
        return 2
    with tempfile.TemporaryDirectory(prefix="weebo-isolation-") as folder:
        root = Path(folder)
        generated = subprocess.run([*binary.command(), "app-server", "generate-json-schema", "--experimental",
                                    "--out", str(root / "schema")], capture_output=True, timeout=45)
        schema = root / "schema" / "codex_app_server_protocol.v2.schemas.json"
        if generated.returncode or not schema.exists():
            print(json.dumps({"version": binary.version_str, "result": "NOT_TESTED", "reason": "Schema unavailable"}))
            return 2
        definitions = json.loads(schema.read_text(encoding="utf-8")).get("definitions", {})
        ok = supported(definitions)
        report = {"version": binary.version_str, "required_schema_fields": ok, "synthetic_isolation": "NOT_TESTED",
                  "real_apps_and_pre_execution_approval": "NOT_TESTED"}
        if args.probe and ok:
            try:
                report.update(asyncio.run(probe(binary, root)))
            except Exception as exc:
                # Only the disposable synthetic subprocess's diagnostic is returned.
                report.update(synthetic_isolation="NOT_TESTED", reason=str(exc))
        print(json.dumps(report, indent=2))
        return 0 if ok and (not args.probe or report["synthetic_isolation"] == "PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
