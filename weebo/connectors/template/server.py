"""Template: a thin MCP server that wraps one of your own programs so Weebo (and ChatGPT) can use it.

Copy this folder, rename it, and fill in the tool functions. It wraps a program you already have, either its
command line (``PROGRAM_CMD``) or its HTTP API (``PROGRAM_URL``), so nothing about the program is hardcoded here.

Run it over stdio (what Weebo's onboarding test uses):

    python server.py

or over HTTP (to add it to ChatGPT as a custom connector, or to share it between machines):

    python server.py --http --port 8765

Needs the MCP Python SDK: ``pip install mcp``. Then register it with Weebo using ``manifest.json`` (Capabilities →
Add a connector, or ask Weebo). Every tool needs a risk label there: read, act, spend or contact_people. Tools that
spend money or contact people need a rule in your own words before Weebo will approve the connector.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import urllib.request

from mcp.server.fastmcp import FastMCP

NAME = os.environ.get("CONNECTOR_NAME", "my-program")
PROGRAM_CMD = os.environ.get("PROGRAM_CMD", "")  # e.g. the CLI you already use; arguments are appended per call
PROGRAM_URL = os.environ.get("PROGRAM_URL", "")  # e.g. http://localhost:9000, if the program has an HTTP API
TIMEOUT = float(os.environ.get("PROGRAM_TIMEOUT", "60"))

mcp = FastMCP(NAME)


def _run_cli(*args: str) -> str:
    if not PROGRAM_CMD:
        raise RuntimeError("Set PROGRAM_CMD to the program's command line.")
    done = subprocess.run([*shlex.split(PROGRAM_CMD), *args], capture_output=True, text=True, timeout=TIMEOUT)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip()[:500] or f"exit code {done.returncode}")
    return done.stdout


def _call_http(path: str, payload: dict) -> dict:
    if not PROGRAM_URL:
        raise RuntimeError("Set PROGRAM_URL to the program's HTTP address.")
    request = urllib.request.Request(PROGRAM_URL.rstrip("/") + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 (your own program's address)
        return json.loads(response.read() or b"{}")


@mcp.tool()
def lookup(query: str) -> str:
    """Read-only: look something up in the program (risk: read). Replace with your program's real query."""
    return _run_cli("search", query) if PROGRAM_CMD else json.dumps(_call_http("/search", {"query": query}))


@mcp.tool()
def start_job(description: str) -> str:
    """Start work in the program (risk: act). Return what the program says, so Weebo can verify it afterwards."""
    return _run_cli("start", description) if PROGRAM_CMD else json.dumps(_call_http("/jobs", {"description": description}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args()
    if options.http:
        mcp.settings.port = options.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
