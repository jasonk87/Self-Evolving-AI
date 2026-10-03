"""Run the Weebo server process (the supervisor normally launches this)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import webbrowser

from aiohttp import web

from .. import __version__, log
from ..app import RESTART_EXIT_CODE, WeeboApp
from ..remote import process_image
from .app import create_app

logger = log.get("main")


async def serve(host: str | None = None, port: int | None = None, open_browser: bool | None = None) -> int:
    weebo = WeeboApp()
    host = host or weebo.settings.get("server.host")
    port = port or int(weebo.settings.get("server.port"))
    # The security guard and the Tailscale helper must follow the real listener, not just the settings.
    weebo.bind_host, weebo.bind_port = host, port
    await weebo.start()
    app = create_app(weebo)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except OSError as exc:
        logger.error("Could not listen on %s:%s (%s). Is Weebo already running?", host, port, exc)
        await weebo.stop()
        return 1
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown_host}:{port}/"
    logger.info("Weebo %s is up at %s", __version__, url)
    if open_browser is None:
        open_browser = bool(weebo.settings.get("server.open_browser")) and not os.environ.get("WEEBO_RESTARTED")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    watchdog = asyncio.create_task(_watch_supervisor(weebo), name="supervisor-watch")
    try:
        await weebo.shutdown_event.wait()
    finally:
        watchdog.cancel()
        logger.info("Shutting down Weebo…")
        await runner.cleanup()
        await weebo.stop()
    return weebo.exit_code


async def _watch_supervisor(weebo: WeeboApp) -> None:
    """If the supervisor is killed outright, shut down too instead of lingering as an orphan on the port."""
    pid = int(os.environ.get("WEEBO_SUPERVISOR_PID") or 0)
    if not pid:
        return
    while True:
        await asyncio.sleep(3)
        if process_image(pid) is None:
            logger.warning("The supervisor (pid %s) is gone; shutting down.", pid)
            weebo.shutdown_event.set()
            return


def _respawn(host: str | None, port: int | None) -> None:
    """Restart without a supervisor (``--child`` runs): start a fresh process, then let this one exit."""
    args = [sys.executable, "-m", "weebo", "--child", "--no-browser"]
    if host:
        args += ["--host", host]
    if port:
        args += ["--port", str(port)]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(args, env={**os.environ, "WEEBO_RESTARTED": "1"}, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
                     start_new_session=os.name != "nt", close_fds=True)
    logger.info("No supervisor: started a replacement Weebo process.")


def run(host: str | None = None, port: int | None = None, open_browser: bool | None = None) -> int:
    log.setup()
    try:
        code = asyncio.run(serve(host, port, open_browser))
    except KeyboardInterrupt:
        return 0
    if code == RESTART_EXIT_CODE and not os.environ.get("WEEBO_SUPERVISOR_PID"):
        _respawn(host, port)
        return 0
    return code
