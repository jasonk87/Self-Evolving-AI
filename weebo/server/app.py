"""HTTP + WebSocket server for the Weebo UI.

Security model: Weebo can run commands on this machine, so the API must never be
reachable by other web pages. Every API call and the WebSocket need a random
per-launch token that is only embedded in the page Weebo itself serves, and the
Host header must match (blocks DNS-rebinding). In LAN mode, devices other than
this computer must also present the access key once.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import re
import secrets
import socket
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from aiohttp import WSMsgType, web

from .. import __version__, log, paths
from ..app import WeeboApp
from ..config import SettingsError
from ..evolution.engine import EvolutionError
from ..memory.memory import MemoryError_
from ..timeparse import TimeParseError

logger = log.get("server")

USER_ERRORS = (ValueError, SettingsError, EvolutionError, MemoryError_, TimeParseError, KeyError)
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
MAX_UPLOAD = 20 * 1024 * 1024

APP_KEY = web.AppKey("weebo", WeeboApp)
TOKEN_KEY = web.AppKey("token", str)
HUB_KEY = web.AppKey("hub", object)


# Browsers fetch the app manifest and its icons without cookies, so these stay public (no secrets in them).
PUBLIC_PATHS = {"/manifest.webmanifest", "/static/icon.svg"}

LOCKED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#101117">
<title>Weebo is private</title><link rel="icon" href="/static/icon.svg">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#101117;color:#ecebf4;
font:15px/1.5 system-ui,sans-serif}form{max-width:340px;padding:28px;text-align:center}img{width:84px}
h1{font-size:22px;margin:12px 0 6px}p{color:#a3a5b6;margin:0 0 18px}input{width:100%;box-sizing:border-box;
padding:11px 13px;border-radius:12px;border:1px solid #363a48;background:#191b23;color:inherit;font:inherit}
button{margin-top:10px;width:100%;padding:11px;border:0;border-radius:12px;background:#5ad7ff;color:#04161d;
font:600 15px system-ui}</style></head><body><form method="get" action="/">
<img src="/static/icon.svg" alt=""><h1>Weebo is private</h1>
<p>Open Weebo with the phone link from Settings on your PC, or paste the access key.</p>
<input name="key" type="password" autocomplete="current-password" placeholder="Access key" required>
<button type="submit">Open Weebo</button></form></body></html>"""


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


def _host_only(raw: str) -> str:
    """Strip the port from a Host header ("[::1]:5050" -> "::1", "pc:5050" -> "pc")."""
    if raw.startswith("["):
        return raw[1:raw.index("]")] if "]" in raw else raw.strip("[]")
    return raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw


def _lan_host_allowed(host: str) -> bool:
    """In LAN mode only IP addresses and this machine's own names are accepted as Host. A web page can point a
    domain it controls at this machine (DNS rebinding), but it can't make the browser send an IP or our name."""
    host = host.lower()
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    name = socket.gethostname().lower()
    return host in ("localhost", name, f"{name}.local") or (host.startswith(f"{name}.") and host.endswith(".ts.net"))


def lan_addresses() -> list[str]:
    """This machine's reachable IPv4 addresses (Wi-Fi/Ethernet, VPNs like Tailscale), best first."""
    found: list[str] = []
    try:
        found = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    try:  # the address the OS would use to reach the internet (usually the Wi-Fi/Ethernet one)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET address: no packet is sent for UDP connect
            found.insert(0, probe.getsockname()[0])
    except OSError:
        pass
    result: list[str] = []
    for addr in found:
        ip = ipaddress.ip_address(addr)
        if ip.is_loopback or ip.is_link_local or addr in result:
            continue
        result.append(addr)
    return result


def qr_data_url(text: str) -> str | None:
    try:
        import base64
        import io

        import qrcode
    except ImportError:
        return None
    image = qrcode.make(text, box_size=6, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, default=str, ensure_ascii=False))


class Hub:
    """Fans bus events out to every connected browser tab."""

    def __init__(self, app: WeeboApp):
        self.app = app
        self.clients: dict[web.WebSocketResponse, asyncio.Queue] = {}
        app.bus.subscribe("*", self._on_event)

    def _on_event(self, topic: str, data: dict[str, Any]) -> None:
        if not self.clients:
            return
        payload = json.dumps({"type": topic, "data": data}, default=str, ensure_ascii=False)
        for queue in self.clients.values():
            if queue.qsize() < 5000:
                queue.put_nowait(payload)

    async def serve(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        queue: asyncio.Queue = asyncio.Queue()
        self.clients[ws] = queue
        self.app.clients = len(self.clients)
        writer = asyncio.create_task(self._writer(ws, queue))
        await ws.send_str(json.dumps({"type": "hello", "data": {"version": __version__}}))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._on_client_message(msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            writer.cancel()
            self.clients.pop(ws, None)
            self.app.clients = len(self.clients)
        return ws

    async def _writer(self, ws: web.WebSocketResponse, queue: asyncio.Queue) -> None:
        try:
            while not ws.closed:
                payload = await queue.get()
                await ws.send_str(payload)
        except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
            pass

    async def _on_client_message(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        if message.get("type") == "activity":
            self.app.heartbeat.touch()

    async def close_all(self) -> None:
        for ws in list(self.clients):
            await ws.close()


def create_app(weebo: WeeboApp) -> web.Application:
    token = secrets.token_urlsafe(24)
    settings = weebo.settings
    bind_host = weebo.bind_host  # the actual listener (CLI overrides included), not only the setting
    lan_mode = not _is_loopback(bind_host)
    access_key = weebo.store.kv_get("lan_access_key") or ""
    if len(access_key) < 20:
        access_key = secrets.token_urlsafe(18)
        weebo.store.kv_set("lan_access_key", access_key)
    failures: dict[str, list[float]] = {}

    def key_ok(value: str | None) -> bool:
        return bool(value) and secrets.compare_digest(value, access_key)

    def owner_logins() -> set[str]:
        remote = getattr(weebo, "remote", None)
        return remote.owners() if remote is not None else set()

    @web.middleware
    async def guard(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
        peer = (request.remote or "").split("%")[0]
        host = _host_only(request.host or "")
        local_peer = not peer or _is_loopback(peer)
        # Weebo's Tailscale address (and `tailscale serve`) reach it from this machine with the *.ts.net name.
        via_tailscale = local_peer and host.lower().endswith(".ts.net")
        if not (host in ("127.0.0.1", "localhost", "::1") or via_tailscale or (lan_mode and _lan_host_allowed(host))):
            return web.Response(status=421, text="Weebo answers on this computer and its private Tailscale address.")
        public = request.path in PUBLIC_PATHS or request.path.startswith("/static/icons/")
        if (not local_peer or via_tailscale) and not public:
            # Tailscale proves who is connecting (the helper strips any client-supplied copy of this header).
            login = request.headers.get("Tailscale-User-Login", "") if via_tailscale else ""
            if not (login and login in owner_logins()) and not key_ok(request.cookies.get("weebo_key")):
                recent = [t for t in failures.get(peer + host, []) if time.time() - t < 900]
                if len(recent) >= 10:
                    return web.Response(status=429, text="Too many wrong keys. Try again in 15 minutes.")
                if key_ok(request.query.get("key")):
                    failures.pop(peer + host, None)
                    response = web.HTTPFound(request.path)
                    response.set_cookie("weebo_key", access_key, httponly=True, samesite="Lax",
                                        secure=via_tailscale, max_age=86400 * 365)
                    raise response
                if "key" in request.query:
                    failures[peer + host] = [*recent, time.time()]
                return web.Response(status=401, text=LOCKED_PAGE, content_type="text/html")
        path = request.path
        if path.startswith("/api/") or path == "/ws":
            # The API token travels only in a header: cross-site pages can't set custom headers without a CORS
            # preflight, which Weebo never grants. WebSockets can't set headers, so /ws also checks the Origin.
            supplied = request.headers.get("X-Weebo-Token")
            if path == "/ws":
                supplied = supplied or request.query.get("token")
                origin = request.headers.get("Origin", "")
                if origin and origin.split("://", 1)[-1] != request.host:
                    return web.Response(status=403, text="Cross-origin WebSocket refused.")
            if not supplied or not secrets.compare_digest(supplied, token):
                return _json({"error": "Missing or invalid Weebo token. Reload the page."}, 401)
        try:
            response = await handler(request)
        except web.HTTPException:
            raise
        except USER_ERRORS as exc:
            message = exc.args[0] if exc.args else str(exc)
            return _json({"error": str(message)}, 400)
        except Exception as exc:
            logger.exception("API error on %s %s", request.method, path)
            weebo.diagnostics.record("api_error", f"{request.method} {request.match_info.route.resource and request.match_info.route.resource.canonical or path}: {exc!r}")
            return _json({"error": f"Internal error: {exc}"}, 500)
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    app = web.Application(middlewares=[guard], client_max_size=MAX_UPLOAD + 1024 * 1024)
    app[APP_KEY] = weebo
    app[TOKEN_KEY] = token
    hub = Hub(weebo)
    app[HUB_KEY] = hub
    r = app.router

    # ------------------------------------------------------------------ pages & assets
    async def index(_: web.Request) -> web.Response:
        html = (paths.WEB_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace("{{WEEBO_TOKEN}}", token).replace("{{WEEBO_VERSION}}", __version__)
        return web.Response(text=html, content_type="text/html", headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' blob:; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; "
                "script-src 'self'; connect-src 'self' ws: wss:; frame-src 'self' blob: data:; object-src 'none'; base-uri 'none'"
            ),
        })

    async def static(request: web.Request) -> web.StreamResponse:
        rel = request.match_info["path"]
        target = (paths.WEB_DIR / rel).resolve()
        if paths.WEB_DIR.resolve() not in target.parents or not target.is_file():
            raise web.HTTPNotFound()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "text/javascript"
        return web.FileResponse(target, headers={"Content-Type": content_type, "Cache-Control": "no-cache"})

    async def uploads(request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        target = (paths.uploads_dir() / name).resolve()
        if paths.uploads_dir().resolve() != target.parent or not target.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(target, headers={"Cache-Control": "private, max-age=86400"})

    async def widget(request: web.Request) -> web.Response:
        """Serve an interactive chat widget for a sandboxed iframe. The signature is per message and is not an
        API token, so widget scripts (opaque origin, no same-origin access) can never call Weebo's API."""
        message_id = request.match_info["id"]
        index_ = int(request.match_info.get("index", "0"))
        if not secrets.compare_digest(request.query.get("sig", ""), widget_signature(token, message_id, index_)):
            raise web.HTTPForbidden()
        message = weebo.store.get_message(message_id)
        if not message:
            raise web.HTTPNotFound()
        html = widget_html(message, index_)
        if html is None:
            raise web.HTTPNotFound()
        widget_key = json.dumps(f"{message_id}:{index_}")
        resize_script = (
            "<script>(function(){function h(){parent.postMessage({weeboWidget:" + widget_key + ",height:"
            "document.documentElement.scrollHeight},'*')}new ResizeObserver(h).observe(document.body);h()})()</script>"
        )
        page = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
                "<style>html,body{margin:0;padding:8px;font-family:system-ui,sans-serif;background:transparent;"
                "color:#e8e6f0}</style></head><body>" + html + resize_script + "</body></html>")
        return web.Response(text=page, content_type="text/html", headers={
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                       "img-src data: https:; font-src data:; sandbox allow-scripts",
            "Cache-Control": "private, max-age=3600",
        })

    async def widget_url(request: web.Request) -> web.Response:
        message_id = request.query.get("id", "")
        index_ = int(request.query.get("index", "0"))
        return _json({"url": f"/wv/{quote(message_id, safe='')}/{index_}?sig={widget_signature(token, message_id, index_)}"})

    async def workspace_file(request: web.Request) -> web.StreamResponse:
        """Open things Weebo built (games, pages, images) straight from its workspace.

        Served with a CSP sandbox (opaque origin), so a generated page can run its own scripts but can never read
        Weebo's page, its session token, or call the API."""
        rel = request.match_info["path"]
        root = paths.workspace_dir().resolve()
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise web.HTTPNotFound()
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            raise web.HTTPNotFound()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        return web.FileResponse(target, headers={
            "Content-Type": content_type,
            "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-modals allow-popups allow-pointer-lock",
            "Cache-Control": "no-cache",
        })

    r.add_get("/", index)
    r.add_get("/files/{path:.*}", workspace_file)
    # Not "/widget/": ad-block filter lists block that path, which left widgets blank.
    r.add_get("/wv/{id}/{index}", widget)
    r.add_get("/api/widget-url", widget_url)
    r.add_get("/static/{path:.+}", static)
    r.add_get("/uploads/{name}", uploads)
    async def manifest(_: web.Request) -> web.StreamResponse:
        return web.FileResponse(paths.WEB_DIR / "manifest.webmanifest", headers={"Content-Type": "application/manifest+json"})

    async def service_worker(_: web.Request) -> web.StreamResponse:
        return web.FileResponse(paths.WEB_DIR / "sw.js", headers={"Content-Type": "text/javascript", "Cache-Control": "no-cache"})

    r.add_get("/manifest.webmanifest", manifest)
    r.add_get("/sw.js", service_worker)
    r.add_get("/ws", hub.serve)

    # ------------------------------------------------------------------ bootstrap & system
    async def bootstrap(_: web.Request) -> web.Response:
        return _json({
            "snapshot": weebo.snapshot(),
            "settings": weebo.settings.all(),
            "conversations": weebo.store.list_conversations(),
            "desk_id": weebo.conversations.desk()["id"],
            "tasks": weebo.store.list_tasks(limit=40),
            "proposals": _slim_proposals(weebo.store.list_proposals(limit=60)),
            "notifications": weebo.store.list_notifications(limit=None, unread_only=True),
            "read_notification_ids": weebo.store.read_notification_ids(),
            "reminders": weebo.store.list_reminders(),
            "lan": lan_info(),
        })

    def lan_info() -> dict[str, Any]:
        port = weebo.bind_port
        info: dict[str, Any] = {"enabled": lan_mode, "configured_host": settings.get("server.host")}
        if lan_mode:
            urls = [f"http://{addr}:{port}/?key={access_key}" for addr in lan_addresses()
                    if not addr.startswith("100.")]  # Tailscale gets its own HTTPS address
            info.update(urls=urls, qr=qr_data_url(urls[0]) if urls else None)
        return info

    def remote_info() -> dict[str, Any]:
        tailnet = weebo.remote.status()
        if tailnet.get("origin"):
            tailnet["qr"] = qr_data_url(tailnet["origin"])
            tailnet["key_link"] = f"{tailnet['origin']}/?key={access_key}"
        return {"tailnet": tailnet, "lan": lan_info()}

    async def remote(_: web.Request) -> web.Response:
        return _json(remote_info())

    async def remote_tailnet(request: web.Request) -> web.Response:
        body = await _body(request)
        await weebo.remote.set_enabled(bool(body.get("enabled")))
        return _json(remote_info())

    async def status(_: web.Request) -> web.Response:
        return _json(weebo.snapshot())

    async def logs(request: web.Request) -> web.Response:
        return _json({"logs": log.recent(int(request.query.get("limit", 300)), request.query.get("level", "INFO"))})

    async def journal(request: web.Request) -> web.Response:
        return _json({"journal": weebo.store.list_journal(int(request.query.get("limit", 120)))})

    async def diagnostics(_: web.Request) -> web.Response:
        return _json({"diagnostics": weebo.diagnostics.all()})

    async def restart(_: web.Request) -> web.Response:
        weebo.request_restart("Restart requested from the UI")
        return _json({"ok": True})

    async def trigger(request: web.Request) -> web.Response:
        return _json(await weebo.heartbeat.trigger(request.match_info["name"]))

    r.add_get("/api/bootstrap", bootstrap)
    r.add_get("/api/remote", remote)
    r.add_post("/api/remote/tailnet", remote_tailnet)
    r.add_get("/api/status", status)
    r.add_get("/api/logs", logs)
    r.add_get("/api/journal", journal)
    r.add_get("/api/diagnostics", diagnostics)
    r.add_post("/api/system/restart", restart)
    r.add_post("/api/autonomy/{name}", trigger)

    # ------------------------------------------------------------------ settings & engine
    async def get_settings(_: web.Request) -> web.Response:
        return _json({"settings": weebo.settings.all()})

    async def patch_settings(request: web.Request) -> web.Response:
        body = await request.json()
        changed = weebo.settings.update(body.get("changes") or body)
        return _json({"settings": weebo.settings.all(), "changed": changed})

    async def engine_status(_: web.Request) -> web.Response:
        return _json(weebo.engine.snapshot())

    async def engine_login(_: web.Request) -> web.Response:
        return _json(await weebo.engine.login_start())

    async def engine_logout(_: web.Request) -> web.Response:
        await weebo.engine.logout()
        return _json({"ok": True})

    async def engine_restart(_: web.Request) -> web.Response:
        await weebo.engine.restart()
        return _json({"ok": True})

    async def engine_refresh(_: web.Request) -> web.Response:
        await weebo.engine.refresh_rate_limits()
        await weebo.engine.refresh_models()
        return _json(weebo.engine.snapshot())

    r.add_get("/api/settings", get_settings)
    r.add_patch("/api/settings", patch_settings)
    r.add_get("/api/engine", engine_status)
    r.add_post("/api/engine/login", engine_login)
    r.add_post("/api/engine/logout", engine_logout)
    r.add_post("/api/engine/restart", engine_restart)
    r.add_post("/api/engine/refresh", engine_refresh)

    # ------------------------------------------------------------------ conversations
    async def list_conversations(request: web.Request) -> web.Response:
        archived = request.query.get("archived") == "1"
        return _json({"conversations": weebo.store.list_conversations(include_archived=archived)})

    async def create_conversation(request: web.Request) -> web.Response:
        body = await _body(request)
        conv = weebo.conversations.create(title=body.get("title") or "New chat", cwd=body.get("cwd") or None)
        return _json({"conversation": conv})

    async def get_conversation(request: web.Request) -> web.Response:
        conv_id = request.match_info["id"]
        conv = weebo.store.get_conversation(conv_id)
        if not conv:
            raise web.HTTPNotFound()
        before = request.query.get("before")
        messages = weebo.store.list_messages(conv_id, limit=int(request.query.get("limit", 200)),
                                             before_seq=int(before) if before else None)
        if conv.get("unread"):
            conv = weebo.store.update_conversation(conv_id, unread=0, updated_at=conv["updated_at"])
        return _json({"conversation": conv, "messages": messages, "live": weebo.conversations.live_state(conv_id),
                      "evolution_messages": weebo.store.list_proposal_messages(conv_id) if not before else []})

    async def patch_conversation(request: web.Request) -> web.Response:
        conv_id = request.match_info["id"]
        body = await _body(request)
        allowed = {k: body[k] for k in ("title", "pinned", "archived", "cwd") if k in body}
        if "cwd" in allowed and allowed["cwd"]:
            if not Path(allowed["cwd"]).expanduser().is_dir():
                raise ValueError(f"Folder not found: {allowed['cwd']}")
        conv = weebo.store.get_conversation(conv_id)
        if not conv:
            raise web.HTTPNotFound()
        conv = weebo.store.update_conversation(conv_id, updated_at=conv["updated_at"], **allowed)
        weebo.bus.publish("conv.updated", {"conversation": conv})
        return _json({"conversation": conv})

    async def delete_conversation(request: web.Request) -> web.Response:
        await weebo.conversations.delete(request.match_info["id"])
        return _json({"ok": True})

    async def send_message(request: web.Request) -> web.Response:
        body = await _body(request)
        images = []
        for name in body.get("images") or []:
            candidate = (paths.uploads_dir() / Path(str(name)).name).resolve()
            if candidate.parent == paths.uploads_dir().resolve() and candidate.is_file():
                images.append(str(candidate))
        message = await weebo.conversations.send(request.match_info["id"], body.get("text", ""), images)
        return _json({"message": message})

    async def interrupt(request: web.Request) -> web.Response:
        return _json({"ok": await weebo.conversations.interrupt(request.match_info["id"])})

    r.add_get("/api/conversations", list_conversations)
    r.add_post("/api/conversations", create_conversation)
    r.add_get("/api/conversations/{id}", get_conversation)
    r.add_patch("/api/conversations/{id}", patch_conversation)
    r.add_delete("/api/conversations/{id}", delete_conversation)
    r.add_post("/api/conversations/{id}/messages", send_message)
    r.add_post("/api/conversations/{id}/interrupt", interrupt)

    # ------------------------------------------------------------------ uploads & interactions
    async def upload(request: web.Request) -> web.Response:
        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "file":
            raise ValueError("Send the image as a 'file' form field.")
        content_type = field.headers.get("Content-Type", "")
        ext = IMAGE_TYPES.get(content_type)
        if not ext:
            raise ValueError("Only PNG, JPEG, GIF and WebP images can be attached.")
        name = f"u_{int(time.time())}_{secrets.token_hex(4)}{ext}"
        dest = paths.uploads_dir() / name
        size = 0
        with dest.open("wb") as handle:
            while chunk := await field.read_chunk(256 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    handle.close()
                    dest.unlink(missing_ok=True)
                    raise ValueError("Images must be under 20 MB.")
                handle.write(chunk)
        return _json({"name": name, "url": f"/uploads/{name}"})

    async def resolve_interaction(request: web.Request) -> web.Response:
        body = await _body(request)
        ok = weebo.interactions.resolve(request.match_info["id"], body.get("decision", "accept"), body.get("answers"))
        if not ok:
            raise ValueError("That request already expired.")
        return _json({"ok": True})

    r.add_post("/api/uploads", upload)
    r.add_post("/api/interactions/{id}", resolve_interaction)
    async def list_interactions(_: web.Request) -> web.Response:
        return _json({"pending": weebo.interactions.list()})

    r.add_get("/api/interactions", list_interactions)

    # ------------------------------------------------------------------ agents
    async def list_tasks(_: web.Request) -> web.Response:
        return _json({"tasks": weebo.store.list_tasks(limit=80)})

    async def get_task(request: web.Request) -> web.Response:
        task_id = request.match_info["id"]
        task = weebo.store.get_task(task_id)
        if not task:
            raise web.HTTPNotFound()
        return _json({"task": task, "events": weebo.store.list_task_events(task_id), "live": weebo.agents.live(task_id)})

    async def create_task(request: web.Request) -> web.Response:
        body = await _body(request)
        task = await weebo.agents.start(body.get("title", ""), body.get("instructions", ""), cwd=body.get("cwd") or None,
                                        effort=body.get("effort") or None, conversation_id=body.get("conversation_id"))
        return _json({"task": task})

    async def stop_task(request: web.Request) -> web.Response:
        await weebo.agents.stop(request.match_info["id"])
        return _json({"ok": True})

    async def message_task(request: web.Request) -> web.Response:
        body = await _body(request)
        await weebo.agents.message(request.match_info["id"], body.get("text", ""))
        return _json({"ok": True})

    r.add_get("/api/tasks", list_tasks)
    r.add_post("/api/tasks", create_task)
    r.add_get("/api/tasks/{id}", get_task)
    r.add_post("/api/tasks/{id}/stop", stop_task)
    r.add_post("/api/tasks/{id}/message", message_task)

    # ------------------------------------------------------------------ memory
    async def list_memories(request: web.Request) -> web.Response:
        query = request.query.get("q", "").strip()
        kind = request.query.get("kind") or None
        if query:
            rows = weebo.store.search_memories(query, limit=100, kinds=[kind] if kind else None)
        else:
            rows = weebo.store.list_memories(kind=kind, limit=500)
        return _json({"memories": rows, "stats": weebo.memory.stats()})

    async def add_memory(request: web.Request) -> web.Response:
        body = await _body(request)
        memory, action = weebo.memory.remember(body.get("text", ""), body.get("kind", "fact"),
                                               int(body.get("importance", 3)), source="user",
                                               pinned=bool(body.get("pinned")))
        return _json({"memory": memory, "action": action})

    async def patch_memory(request: web.Request) -> web.Response:
        body = await _body(request)
        memory = weebo.memory.update(request.match_info["id"], text=body.get("text"), kind=body.get("kind"),
                                     importance=body.get("importance"),
                                     pinned=None if body.get("pinned") is None else int(bool(body["pinned"])))
        if not memory:
            raise web.HTTPNotFound()
        return _json({"memory": memory})

    async def delete_memory(request: web.Request) -> web.Response:
        return _json({"ok": weebo.memory.forget(request.match_info["id"])})

    async def import_legacy(_: web.Request) -> web.Response:
        return _json({"imported": weebo.memory.import_legacy(force=True)})

    r.add_get("/api/memories", list_memories)
    r.add_post("/api/memories", add_memory)
    r.add_patch("/api/memories/{id}", patch_memory)
    r.add_delete("/api/memories/{id}", delete_memory)
    r.add_post("/api/memories/import-legacy", import_legacy)

    # ------------------------------------------------------------------ schedule
    async def list_reminders(request: web.Request) -> web.Response:
        return _json({"reminders": weebo.store.list_reminders(include_done=request.query.get("all") == "1")})

    async def add_reminder(request: web.Request) -> web.Response:
        body = await _body(request)
        reminder = weebo.scheduler.add(body.get("text", ""), body.get("when", ""), body.get("recurrence", ""),
                                       body.get("action", "notify"), body.get("conversation_id"))
        return _json({"reminder": reminder})

    async def cancel_reminder(request: web.Request) -> web.Response:
        return _json({"ok": weebo.scheduler.cancel(request.match_info["id"])})

    async def snooze_reminder(request: web.Request) -> web.Response:
        body = await _body(request)
        return _json({"reminder": weebo.scheduler.snooze(request.match_info["id"], int(body.get("minutes", 10)))})

    r.add_get("/api/reminders", list_reminders)
    r.add_post("/api/reminders", add_reminder)
    r.add_delete("/api/reminders/{id}", cancel_reminder)
    r.add_post("/api/reminders/{id}/snooze", snooze_reminder)

    # ------------------------------------------------------------------ evolution & skills
    async def list_proposals(_: web.Request) -> web.Response:
        return _json({"proposals": _slim_proposals(weebo.store.list_proposals(limit=100)),
                      "current": weebo.evolution.current, "mode": weebo.settings.get("evolution.mode")})

    async def get_proposal(request: web.Request) -> web.Response:
        proposal = weebo.store.get_proposal(request.match_info["id"])
        if not proposal:
            raise web.HTTPNotFound()
        return _json({"proposal": proposal})

    async def create_proposal(request: web.Request) -> web.Response:
        body = await _body(request)
        proposal = await weebo.evolution.propose(body.get("title", ""), body.get("description", ""),
                                                 body.get("rationale", "Requested by the user."), source="user")
        return _json({"proposal": proposal})

    async def proposal_action(request: web.Request) -> web.Response:
        action = request.match_info["action"]
        proposal_id = request.match_info["id"]
        handlers = {"approve": weebo.evolution.approve, "rebuild": weebo.evolution.approve,
                    "reject": weebo.evolution.reject, "discard": weebo.evolution.discard,
                    "merge": weebo.evolution.merge, "rollback": weebo.evolution.rollback}
        if action not in handlers:
            raise web.HTTPNotFound()
        proposal = await handlers[action](proposal_id)
        return _json({"proposal": _slim_proposals([proposal])[0]})

    async def list_skills(_: web.Request) -> web.Response:
        return _json({"skills": weebo.skills.list()})

    async def delete_skill(request: web.Request) -> web.Response:
        return _json({"ok": await weebo.skills.delete(request.match_info["name"])})

    r.add_get("/api/proposals", list_proposals)
    r.add_post("/api/proposals", create_proposal)
    r.add_get("/api/proposals/{id}", get_proposal)
    r.add_post("/api/proposals/{id}/{action}", proposal_action)
    r.add_get("/api/skills", list_skills)
    r.add_delete("/api/skills/{name}", delete_skill)

    # ------------------------------------------------------------------ notifications
    async def list_notifications(request: web.Request) -> web.Response:
        unread_only = request.query.get("unread_only") == "1"
        limit = int(request.query["limit"]) if "limit" in request.query else (None if unread_only else 50)
        return _json({"notifications": weebo.store.list_notifications(limit=limit, unread_only=unread_only),
                      "read_notification_ids": weebo.store.read_notification_ids()})

    async def read_notifications(request: web.Request) -> web.Response:
        body = await _body(request)
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(id_, str) for id_ in ids):
            raise web.HTTPBadRequest(text="Explicit notification ids are required")
        weebo.store.mark_notifications_read(ids)
        weebo.bus.publish("notifications.read", {"ids": ids})
        return _json({"ok": True})

    r.add_get("/api/notifications", list_notifications)
    r.add_post("/api/notifications/read", read_notifications)

    async def on_shutdown(_: web.Application) -> None:
        await hub.close_all()

    app.on_shutdown.append(on_shutdown)
    return app


def widget_signature(token: str, message_id: str, index: int) -> str:
    return hmac.new(token.encode(), f"widget:{message_id}:{index}".encode(), hashlib.sha256).hexdigest()[:32]


_FENCE = re.compile(r"```html-dynamic[ \t]*\r?\n(.*?)```", re.DOTALL)


def widget_html(message: dict[str, Any], index: int) -> str | None:
    if message.get("kind") == "widget":
        return (message.get("data") or {}).get("html") if index == 0 else None
    blocks = _FENCE.findall(message.get("content") or "")
    return blocks[index] if 0 <= index < len(blocks) else None


async def _body(request: web.Request) -> dict[str, Any]:
    if not request.body_exists:
        return {}
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be JSON.") from exc
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object.")
    return body


def _slim_proposals(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slim = []
    for proposal in proposals:
        item = dict(proposal)
        meta = dict(item.get("meta") or {})
        meta.pop("diff", None)
        item["meta"] = meta
        slim.append(item)
    return slim
