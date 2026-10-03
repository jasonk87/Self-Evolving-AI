"""Boot self-test: proves this copy of Weebo can start and serve its UI.

Runs without the Codex engine or background loops, against a throwaway data
directory. Self-evolution runs it on every candidate upgrade before merging.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import pkgutil
import re
import sys
import tempfile
from pathlib import Path


def _check(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


async def _run() -> list[str]:
    failures: list[str] = []
    import weebo
    from weebo import paths

    for module in pkgutil.walk_packages(weebo.__path__, "weebo."):
        if module.name in ("weebo.__main__",):
            continue
        try:
            importlib.import_module(module.name)
        except Exception as exc:
            failures.append(f"import {module.name}: {type(exc).__name__}: {exc}")
    if failures:
        return failures

    from aiohttp.test_utils import TestClient, TestServer

    from weebo.app import WeeboApp
    from weebo.brain.tools import registry
    from weebo.server.app import TOKEN_KEY, create_app

    app = WeeboApp()
    await app.start(with_engine=False, with_background=False)
    for scope in ("chat", "agent"):
        specs = registry.specs(scope)
        _check(bool(specs), f"no tools registered for scope {scope}", failures)
        for spec in specs:
            _check(spec["inputSchema"].get("type") == "object", f"tool {spec['name']} schema is not an object", failures)
            json.dumps(spec)

    web_app = create_app(app)
    client = TestClient(TestServer(web_app))
    await client.start_server()
    try:
        token = web_app[TOKEN_KEY]
        resp = await client.get("/", headers={"Host": "127.0.0.1"})
        html = await resp.text()
        _check(resp.status == 200 and token in html, "index page did not render with the session token", failures)
        for ref in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
            asset = await client.get(ref, headers={"Host": "127.0.0.1"})
            _check(asset.status == 200, f"missing asset {ref}", failures)
        resp = await client.get("/api/bootstrap", headers={"Host": "127.0.0.1", "X-Weebo-Token": token})
        _check(resp.status == 200, f"/api/bootstrap returned {resp.status}", failures)
        if resp.status == 200:
            data = await resp.json()
            _check("desk_id" in data and "settings" in data, "bootstrap payload is incomplete", failures)
        resp = await client.get("/api/bootstrap", headers={"Host": "127.0.0.1"})
        _check(resp.status == 401, "API answered without a token", failures)
        resp = await client.get("/", headers={"Host": "evil.example"})
        _check(resp.status == 421, "server answered a foreign Host header", failures)
    finally:
        await client.close()
        app.store.close()

    for js in sorted((paths.WEB_DIR / "js").rglob("*.js")):
        text = js.read_text(encoding="utf-8")
        for target in re.findall(r"""from\s+['"](\.{1,2}/[^'"]+)['"]""", text):
            _check((js.parent / target).resolve().exists(), f"{js.name} imports missing module {target}", failures)
    return failures


def main() -> int:
    if not os.environ.get("WEEBO_DATA_DIR"):
        os.environ["WEEBO_DATA_DIR"] = tempfile.mkdtemp(prefix="weebo-selftest-")
    os.environ["WEEBO_DISABLE_LEGACY"] = "1"
    os.environ["WEEBO_SKIP_LEGACY_IMPORT"] = "1"
    failures = asyncio.run(_run())
    if failures:
        print("SELFTEST FAILED")
        for failure in failures:
            print(" -", failure)
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
