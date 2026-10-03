import io
import json

import pytest
import pytest_asyncio
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from weebo import paths
from weebo.server.app import TOKEN_KEY, create_app, widget_signature

pytestmark = pytest.mark.asyncio

HOST = {"Host": "127.0.0.1"}


@pytest_asyncio.fixture
async def client(app):
    web_app = create_app(app)
    client = TestClient(TestServer(web_app))
    await client.start_server()
    client.token = web_app[TOKEN_KEY]
    client.h = {**HOST, "X-Weebo-Token": client.token}
    yield client
    await client.close()


async def test_index_carries_token_and_security_headers(client):
    resp = await client.get("/", headers=HOST)
    html = await resp.text()
    assert resp.status == 200 and client.token in html
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]


async def test_api_requires_header_token_not_query(client):
    assert (await client.get("/api/bootstrap", headers=HOST)).status == 401
    assert (await client.get(f"/api/bootstrap?token={client.token}", headers=HOST)).status == 401
    assert (await client.get("/api/bootstrap", headers=client.h)).status == 200


async def test_foreign_host_is_refused(client):
    resp = await client.get("/", headers={"Host": "attacker.example:5050"})
    assert resp.status == 421


async def test_cross_origin_websocket_is_refused(client):
    resp = await client.get(f"/ws?token={client.token}", headers={**HOST, "Origin": "http://evil.example"})
    assert resp.status == 403


async def test_conversation_lifecycle(client, fake_engine):
    created = await (await client.post("/api/conversations", json={}, headers=client.h)).json()
    conv_id = created["conversation"]["id"]
    sent = await client.post(f"/api/conversations/{conv_id}/messages", json={"text": "hello"}, headers=client.h)
    assert sent.status == 200 and fake_engine.turns
    data = await (await client.get(f"/api/conversations/{conv_id}", headers=client.h)).json()
    assert data["messages"][0]["content"] == "hello" and data["live"]["busy"] is True
    patched = await client.patch(f"/api/conversations/{conv_id}", json={"title": "Renamed", "cwd": "Z:/nope/nowhere"}, headers=client.h)
    assert patched.status == 400
    patched = await client.patch(f"/api/conversations/{conv_id}", json={"title": "Renamed"}, headers=client.h)
    assert (await patched.json())["conversation"]["title"] == "Renamed"
    assert (await client.delete(f"/api/conversations/{conv_id}", headers=client.h)).status == 200


async def test_empty_message_is_a_400(client):
    conv = await (await client.post("/api/conversations", json={}, headers=client.h)).json()
    resp = await client.post(f"/api/conversations/{conv['conversation']['id']}/messages", json={"text": "  "}, headers=client.h)
    assert resp.status == 400 and "empty" in (await resp.json())["error"]


async def test_settings_validation(client):
    bad = await client.patch("/api/settings", json={"changes": {"autonomy.level": "yolo"}}, headers=client.h)
    assert bad.status == 400
    ok = await client.patch("/api/settings", json={"changes": {"agents.max_parallel": 4}}, headers=client.h)
    assert (await ok.json())["changed"] == {"agents.max_parallel": 4}


async def test_memories_and_reminders_api(client):
    added = await client.post("/api/memories", json={"text": "User runs every Sunday", "kind": "fact"}, headers=client.h)
    memory = (await added.json())["memory"]
    found = await (await client.get("/api/memories?q=sunday", headers=client.h)).json()
    assert found["memories"][0]["id"] == memory["id"]
    secret = await client.post("/api/memories", json={"text": "my password is abc123"}, headers=client.h)
    assert secret.status == 400
    rem = await client.post("/api/reminders", json={"text": "Call mom", "when": "in 2 hours"}, headers=client.h)
    rid = (await rem.json())["reminder"]["id"]
    assert (await (await client.delete(f"/api/reminders/{rid}", headers=client.h)).json())["ok"] is True
    bad = await client.post("/api/reminders", json={"text": "x", "when": "someday"}, headers=client.h)
    assert bad.status == 400


async def test_upload_accepts_only_images(client):
    form = FormData()
    form.add_field("file", io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), filename="a.png", content_type="image/png")
    resp = await client.post("/api/uploads", data=form, headers=client.h)
    body = await resp.json()
    assert resp.status == 200 and body["url"].startswith("/uploads/")
    assert (await client.get(body["url"], headers=HOST)).status == 200
    form = FormData()
    form.add_field("file", io.BytesIO(b"MZ"), filename="evil.exe", content_type="application/octet-stream")
    assert (await client.post("/api/uploads", data=form, headers=client.h)).status == 400


async def test_workspace_files_are_sandboxed_and_contained(client):
    target = paths.workspace_dir() / "game" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<h1>hi</h1>")
    resp = await client.get("/files/game/", headers=HOST)
    assert resp.status == 200 and "sandbox allow-scripts" in resp.headers["Content-Security-Policy"]
    assert "allow-same-origin" not in resp.headers["Content-Security-Policy"]
    assert (await client.get("/files/../weebo.db", headers=HOST)).status == 404
    assert (await client.get("/files/%2e%2e/weebo.db", headers=HOST)).status == 404


async def test_widgets_require_signature(client, app):
    conv = app.conversations.create()
    msg = app.store.add_message(conv["id"], "assistant", "Look:\n```html-dynamic\n<b>w</b>\n```\n")
    assert (await client.get(f"/wv/{msg["id"]}/0?sig=nope", headers=HOST)).status == 403
    url = (await (await client.get(f"/api/widget-url?id={msg['id']}&index=0", headers=client.h)).json())["url"]
    resp = await client.get(url, headers=HOST)
    assert resp.status == 200 and "<b>w</b>" in await resp.text()
    assert "sandbox allow-scripts" in resp.headers["Content-Security-Policy"]
    assert widget_signature(client.token, msg["id"], 0) in url


async def test_interaction_resolution_endpoint(client, app):
    assert (await client.post("/api/interactions/ask_missing", json={"decision": "accept"}, headers=client.h)).status == 400


async def test_proposal_api(client, app):
    app.settings.update({"evolution.mode": "propose"})
    resp = await client.post("/api/proposals", json={"title": "Add a weather tool", "description": "Use web search."}, headers=client.h)
    proposal = (await resp.json())["proposal"]
    assert proposal["status"] == "proposed"
    listed = await (await client.get("/api/proposals", headers=client.h)).json()
    assert listed["proposals"][0]["id"] == proposal["id"]
    rejected = await client.post(f"/api/proposals/{proposal['id']}/reject", headers=client.h)
    assert (await rejected.json())["proposal"]["status"] == "rejected"
    assert (await client.post(f"/api/proposals/{proposal['id']}/merge", headers=client.h)).status == 400


# ---------------------------------------------------------------- LAN (phone) mode
class _Transport:
    def __init__(self, peer):
        self.peer = peer

    def get_extra_info(self, name, default=None):
        return (self.peer, 54321) if name == "peername" else default

    def is_closing(self):
        return False


async def _guarded(web_app, path, host="192.168.1.50:5050", peer="192.168.1.77", cookies=None, extra=None):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request
    headers = {"Host": host, **(extra or {})}
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    request = make_mocked_request("GET", path, headers=headers, transport=_Transport(peer), app=web_app)
    guard = web_app.middlewares[0]

    async def handler(_):
        return web.Response(text="ok")

    try:
        return await guard(request, handler)
    except web.HTTPException as exc:
        return exc


async def test_lan_mode_requires_key_from_other_devices(app):
    app.bind_host = "0.0.0.0"  # what server.main sets from settings or --host
    web_app = create_app(app)
    key = app.store.kv_get("lan_access_key")
    assert len(key) >= 20
    assert (await _guarded(web_app, "/")).status == 401
    redirect = await _guarded(web_app, f"/?key={key}")
    assert redirect.status == 302 and "weebo_key" in redirect.cookies
    assert redirect.cookies["weebo_key"]["samesite"] == "Lax"  # Strict breaks links opened from a QR scanner
    assert (await _guarded(web_app, "/", cookies={"weebo_key": key})).text == "ok"
    assert (await _guarded(web_app, "/", peer="127.0.0.1")).text == "ok"  # this PC itself needs no key
    # Settings still say loopback (as with `--host 0.0.0.0`); a spoofed Host header must not skip the key.
    assert app.settings.get("server.host") == "127.0.0.1"
    assert (await _guarded(web_app, "/", host="localhost:5050")).status == 401


async def test_lan_mode_blocks_rebinding_hosts_and_brute_force(app):
    app.bind_host = "0.0.0.0"  # what server.main sets from settings or --host
    web_app = create_app(app)
    key = app.store.kv_get("lan_access_key")
    assert (await _guarded(web_app, "/", host="attacker.example:5050", peer="127.0.0.1")).status == 421
    for _ in range(10):
        assert (await _guarded(web_app, "/?key=wrong")).status == 401
    assert (await _guarded(web_app, f"/?key={key}")).status == 429  # locked out even with the right key
    assert (await _guarded(web_app, f"/?key={key}", peer="192.168.1.78")).status == 302  # other devices unaffected


async def test_remote_info_lists_links(client, app):
    info = await (await client.get("/api/remote", headers=client.h)).json()
    assert info["lan"]["enabled"] is False and info["tailnet"]["enabled"] is False
    app.bind_host = "0.0.0.0"  # what server.main sets from settings or --host
    from weebo.server.app import create_app as make
    web_app = make(app)
    lan_client = TestClient(TestServer(web_app))
    await lan_client.start_server()
    try:
        headers = {**HOST, "X-Weebo-Token": web_app[TOKEN_KEY]}
        info = await (await lan_client.get("/api/remote", headers=headers)).json()
        key = app.store.kv_get("lan_access_key")
        assert info["lan"]["enabled"] is True
        assert all(url.endswith(f"/?key={key}") and "//100." not in url for url in info["lan"].get("urls", []))
    finally:
        await lan_client.close()


# ---------------------------------------------------------------- Tailscale address (phone app)
TS_HOST = "weebo.tailnet-example.ts.net"


async def test_tailscale_owner_gets_in_without_a_key(app):
    app.remote.pc_owner = "owner@example.com"
    web_app = create_app(app)  # loopback bind: Weebo is only reachable locally and via Tailscale
    ok = await _guarded(web_app, "/", host=TS_HOST, peer="127.0.0.1", extra={"Tailscale-User-Login": "owner@example.com"})
    assert ok.text == "ok"


async def test_other_tailscale_users_need_the_key(app):
    app.remote.pc_owner = "owner@example.com"
    web_app = create_app(app)
    key = app.store.kv_get("lan_access_key")
    locked = await _guarded(web_app, "/", host=TS_HOST, peer="127.0.0.1", extra={"Tailscale-User-Login": "guest@example.com"})
    assert locked.status == 401 and "Weebo is private" in locked.text
    no_identity = await _guarded(web_app, "/", host=TS_HOST, peer="127.0.0.1")
    assert no_identity.status == 401
    redirect = await _guarded(web_app, f"/?key={key}", host=TS_HOST, peer="127.0.0.1", extra={"Tailscale-User-Login": "guest@example.com"})
    assert redirect.status == 302
    cookie = redirect.cookies["weebo_key"]
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Lax"


async def test_tailscale_host_only_trusted_through_this_machine(app):
    app.remote.pc_owner = "owner@example.com"
    web_app = create_app(app)
    spoofed = await _guarded(web_app, "/", host=TS_HOST, peer="192.168.1.66", extra={"Tailscale-User-Login": "owner@example.com"})
    assert spoofed.status == 421  # a forged identity header from another machine is never trusted
    assert (await _guarded(web_app, "/", host="evil.example", peer="127.0.0.1")).status == 421


async def test_install_files_are_public_but_app_is_not(app):
    web_app = create_app(app)
    for path in ("/manifest.webmanifest", "/static/icon.svg", "/static/icons/icon-192.png"):
        assert (await _guarded(web_app, path, host=TS_HOST, peer="127.0.0.1")).text == "ok"
    assert (await _guarded(web_app, "/api/bootstrap", host=TS_HOST, peer="127.0.0.1")).status == 401


async def test_manifest_is_installable(client):
    manifest = await (await client.get("/manifest.webmanifest", headers=HOST)).json()
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    assert {"192x192", "512x512"} <= sizes and any(i.get("purpose") == "maskable" for i in manifest["icons"])
    for icon in manifest["icons"]:
        assert (await client.get(icon["src"], headers=HOST)).status == 200


# ---------------------------------------------------------------- restarts with and without the supervisor
async def test_restart_without_supervisor_starts_a_replacement(monkeypatch):
    from weebo.server import main as server_main
    launched = []
    monkeypatch.setattr(server_main.subprocess, "Popen", lambda args, **kw: launched.append((args, kw["env"])))
    server_main._respawn("0.0.0.0", 6060)
    args, env = launched[0]
    assert args[1:] == ["-m", "weebo", "--child", "--no-browser", "--host", "0.0.0.0", "--port", "6060"]
    assert env["WEEBO_RESTARTED"] == "1"


async def test_orphaned_server_shuts_down_when_supervisor_dies(app, monkeypatch):
    import asyncio
    from weebo.server import main as server_main
    monkeypatch.setenv("WEEBO_SUPERVISOR_PID", "999999999")  # not a live process
    real_sleep = asyncio.sleep
    monkeypatch.setattr(server_main.asyncio, "sleep", lambda _s: real_sleep(0))
    await asyncio.wait_for(server_main._watch_supervisor(app), 2)
    assert app.shutdown_event.is_set()
