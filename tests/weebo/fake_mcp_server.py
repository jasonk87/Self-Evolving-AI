"""A tiny stdio MCP server for connector onboarding tests (newline-delimited JSON-RPC)."""

import json
import sys

TOOLS = [{"name": "lookup", "description": "Look something up", "inputSchema": {"type": "object"}},
         {"name": "place_call", "description": "Call a business", "inputSchema": {"type": "object"}}]
if "--extra" in sys.argv:
    TOOLS.append({"name": "wire_money", "description": "Undeclared", "inputSchema": {"type": "object"}})


def reply(message_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": message_id}
    out["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method, message_id = message.get("method"), message.get("id")
    if message_id is None:
        continue
    if method == "initialize":
        reply(message_id, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "fake", "version": "1"}})
    elif method == "tools/list":
        reply(message_id, {"tools": TOOLS})
    elif method == "tools/call":
        if "--fail" in sys.argv:
            reply(message_id, {"content": [{"type": "text", "text": "backend down"}], "isError": True})
        else:
            query = (message.get("params") or {}).get("arguments", {}).get("q", "")
            reply(message_id, {"content": [{"type": "text", "text": f"found {query}"}]})
    else:
        reply(message_id, error={"code": -32601, "message": "unknown method"})
