"""Harmless synthetic MCP server for the local isolation diagnostic, never real accounts."""
import json
import sys


def main():
    label = sys.argv[1]
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method = message.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": label, "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": label + "_marker", "description": "Return a harmless diagnostic marker.",
                                 "inputSchema": {"type": "object", "properties": {}},
                                 "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": label}]}
        elif method in ("resources/list", "resources/templates/list"):
            result = {"resources": [], "resourceTemplates": []}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                              "error": {"code": -32601, "message": "unsupported"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
