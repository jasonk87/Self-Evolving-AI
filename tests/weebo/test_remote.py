import asyncio
import json
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer

from weebo import remote


@pytest.fixture
def helper(app, monkeypatch):
    """A surviving helper with real status files and delayed, cooperative shutdown."""
    app.settings.update({"server.tailnet": True})
    executable = remote.paths.sub("bin") / remote.HELPER_NAME
    executable.touch()
    identity = app.remote.state_dir / "node" / "tailscaled.state"
    identity.parent.mkdir()
    identity.write_text("saved test identity", encoding="utf-8")

    class Helper:
        pid = 100_001
        alive = True
        target = "http://127.0.0.1:5050"
        hostname = "weebo"
        stop_polls = 0
        stall = False

        def __init__(self):
            self.launches = []

        def report(self):
            app.remote.status_path.write_text(json.dumps({
                "state": "ready", "pid": self.pid, "target": self.target, "hostname": self.hostname,
                "origin": f"https://{self.hostname}.example.ts.net",
            }), encoding="utf-8")

        def launch(self, args, **kwargs):
            assert not self.alive  # Never share the identity with the previous process.
            assert not (app.remote.state_dir / "stop").exists()
            assert f"--state={app.remote.state_dir}" in args
            assert identity.read_text(encoding="utf-8") == "saved test identity"
            self.launches.append(args)
            self.pid += 1
            self.target = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--target="))
            self.hostname = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--hostname="))
            self.alive = True
            self.report()
            return self

    fake = Helper()
    fake.report()
    sleep = asyncio.sleep

    async def poll_stop(delay):
        if fake.alive and (app.remote.state_dir / "stop").exists():
            fake.stop_polls += 1
            if fake.stop_polls >= 2 and not fake.stall:
                fake.alive = False
        await sleep(0)

    monkeypatch.setattr(remote, "tailscale_owner", AsyncMock(return_value=None))
    monkeypatch.setattr(remote, "process_image", lambda pid: str(executable) if fake.alive and pid == fake.pid else None)
    monkeypatch.setattr(remote.subprocess, "Popen", fake.launch)
    monkeypatch.setattr(remote.asyncio, "sleep", poll_stop)
    return fake


@pytest.mark.asyncio
async def test_restart_routes_remote_requests_to_current_listener(app, helper):
    async with AsyncExitStack() as stack:
        # Isolated sockets represent the logical 5050/6060 listeners, without
        # binding ports that a real Weebo instance might already be using.
        backends = {}
        for port in (5050, 6060):
            async def respond(request, port=port):
                return web.Response(text=f"listener {port}")

            backend = web.Application()
            backend.router.add_get("/probe", respond)
            backends[port] = await stack.enter_async_context(TestServer(backend))
        session = await stack.enter_async_context(ClientSession())

        async def forward(request):
            backend = backends[urlsplit(helper.target).port]
            async with session.get(backend.make_url(request.path)) as response:
                return web.Response(status=response.status, body=await response.read())

        proxy = web.Application()
        proxy.router.add_get("/probe", forward)
        client = await stack.enter_async_context(TestClient(TestServer(proxy)))
        response = await client.get("/probe")
        assert await response.text() == "listener 5050"

        # A new endpoint represents Weebo restarting while its helper survives.
        # The saved server.port stays 5050; the actual listener override must win.
        app.bind_port = 6060
        endpoint = remote.TailnetEndpoint(app)
        status = await endpoint.start()
        assert status["state"] == "ready"
        assert len(helper.launches) == 1 and helper.stop_polls == 2
        assert helper.target == "http://127.0.0.1:6060"
        response = await client.get("/probe")
        assert response.status == 200 and await response.text() == "listener 6060"

        await remote.TailnetEndpoint(app).start()
        assert len(helper.launches) == 1  # Subsequent restarts reuse the updated helper.


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["ready", "starting", "NeedsLogin"])
async def test_restart_reuses_helper_with_matching_configuration(app, helper, state):
    status = app.remote._read_status()
    status["state"] = state
    app.remote.status_path.write_text(json.dumps(status), encoding="utf-8")
    result = await remote.TailnetEndpoint(app).start()
    assert result["running"] and result["state"] == state
    assert helper.launches == [] and helper.stop_polls == 0
    assert not (app.remote.state_dir / "stop").exists()


@pytest.mark.asyncio
async def test_restart_updates_changed_hostname(app, helper):
    app.settings.update({"server.tailnet_hostname": "weebo-lab"})
    status = await remote.TailnetEndpoint(app).start()
    assert status["state"] == "ready" and status["origin"] == "https://weebo-lab.example.ts.net"
    assert len(helper.launches) == 1 and helper.stop_polls == 2
    assert helper.hostname == "weebo-lab" and helper.target == "http://127.0.0.1:5050"


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"target": []}, {"hostname": 42}])
async def test_restart_replaces_helper_without_valid_configuration(app, helper, fields):
    status = app.remote._read_status()
    status.pop("target")
    status.pop("hostname")
    status.update(fields)
    app.remote.status_path.write_text(json.dumps(status), encoding="utf-8")
    result = await remote.TailnetEndpoint(app).start()
    assert result["state"] == "ready" and len(helper.launches) == 1
    assert helper.stop_polls == 2


@pytest.mark.asyncio
async def test_restart_does_not_launch_before_old_helper_exits(app, helper):
    app.bind_port = 6060
    helper.stall = True
    with pytest.raises(RuntimeError, match="did not stop"):
        await remote.TailnetEndpoint(app).start()
    assert helper.alive and helper.launches == []
    assert (app.remote.state_dir / "stop").exists()
