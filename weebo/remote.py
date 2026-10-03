"""Remote access: Weebo's own private HTTPS address on the user's Tailscale network.

A small helper (``weebo/tailnet_helper``, a Tailscale ``tsnet`` node) gets its own
machine name such as ``weebo.<tailnet>.ts.net`` with a real HTTPS certificate,
reachable only from the user's Tailscale devices, at home or away. It forwards to
Weebo on 127.0.0.1 and attaches the *verified* Tailscale login of whoever is
connecting (after stripping any client-supplied copy), so the owner's phone opens
Weebo with no password. HTTPS is also what lets phones install Weebo as an app.

The helper runs as its own process and survives Weebo restarts (self-upgrades),
so the address never blips. Weebo reuses it when it's already running.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import log, paths

if TYPE_CHECKING:
    from .app import WeeboApp

logger = log.get("remote")

HELPER_NAME = "weebo-tailnet.exe" if os.name == "nt" else "weebo-tailnet"
TAILSCALE_CLI = (
    [r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe"]
    if os.name == "nt" else ["/usr/bin/tailscale", "/usr/local/bin/tailscale",
                             "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]
)


def process_image(pid: int) -> str | None:
    """Executable path of a live process, or None. (Never os.kill on Windows: it terminates.)"""
    if not pid or pid <= 0:
        return None
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
                return None
            size = ctypes.c_ulong(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return ""
        finally:
            kernel32.CloseHandle(handle)
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def tailscale_cli() -> str | None:
    for candidate in TAILSCALE_CLI:
        if Path(candidate).exists():
            return candidate
    return shutil.which("tailscale")


async def tailscale_owner() -> str | None:
    """Login of the Tailscale account this PC is signed in to (used to trust `tailscale serve` too)."""
    cli = tailscale_cli()
    if not cli:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            cli, "status", "--json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out, _ = await asyncio.wait_for(proc.communicate(), 15)
        data = json.loads(out or b"{}")
        self_node = data.get("Self") or {}
        user = (data.get("User") or {}).get(str(self_node.get("UserID"))) or {}
        return user.get("LoginName")
    except (OSError, asyncio.TimeoutError, json.JSONDecodeError):
        return None


class TailnetEndpoint:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.state_dir = paths.sub("tailnet")
        self.pc_owner: str | None = None

    # ------------------------------------------------------------------ state
    @property
    def status_path(self) -> Path:
        return self.state_dir / "status.json"

    def helper_path(self) -> Path | None:
        candidate = paths.sub("bin") / HELPER_NAME
        return candidate if candidate.exists() else None

    def _read_status(self) -> dict[str, Any]:
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def running_pid(self) -> int | None:
        status = self._read_status()
        pid = int(status.get("pid") or 0)
        image = process_image(pid)
        helper = self.helper_path()
        if image and helper and Path(image).resolve() == helper.resolve():
            return pid
        return None

    def status(self) -> dict[str, Any]:
        enabled = bool(self.app.settings.get("server.tailnet"))
        raw = self._read_status()
        running = self.running_pid() is not None
        state = raw.get("state", "off") if running else ("stopped" if enabled else "off")
        if raw.get("state") == "error" and raw.get("error"):
            state = "error"
        return {
            "enabled": enabled,
            "helper": self.helper_path() is not None,
            "running": running,
            "state": state,
            "origin": raw.get("origin") if running else None,
            "owner": raw.get("owner") if running else None,
            "auth_url": raw.get("authUrl") if running and state != "ready" else None,
            "error": raw.get("error") if state == "error" else None,
            "hostname": self.app.settings.get("server.tailnet_hostname"),
        }

    def owners(self) -> set[str]:
        """Tailscale logins trusted as the owner (no access key needed)."""
        found = {self._read_status().get("owner") if self.running_pid() else None, self.pc_owner}
        return {o for o in found if o}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> dict[str, Any]:
        self.pc_owner = await tailscale_owner()
        if not self.app.settings.get("server.tailnet"):
            return self.status()
        helper = self.helper_path()
        if helper is None:
            logger.warning("Tailscale address is on, but %s is missing (build weebo/tailnet_helper).", HELPER_NAME)
            return self.status()
        if self.running_pid():
            return self.status()  # survives Weebo restarts; reuse it
        (self.state_dir / "stop").unlink(missing_ok=True)
        port = self.app.bind_port
        args = [str(helper), f"--state={self.state_dir}", f"--hostname={self.app.settings.get('server.tailnet_hostname')}",
                f"--target=http://127.0.0.1:{port}"]
        flags = 0
        if os.name == "nt":
            flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                     | getattr(subprocess, "DETACHED_PROCESS", 0))
        with open(self.state_dir / "helper.log", "ab") as out, open(self.state_dir / "helper-error.log", "ab") as err:
            subprocess.Popen(args, cwd=str(self.state_dir), stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                             creationflags=flags, close_fds=True, start_new_session=os.name != "nt")
        for _ in range(40):  # up to ~16s for the node to come online
            await asyncio.sleep(0.4)
            status = self.status()
            if status["state"] in ("ready", "error") or status["auth_url"]:
                break
        status = self.status()
        logger.info("Tailscale address: %s %s", status["state"], status.get("origin") or status.get("auth_url") or "")
        self.app.bus.publish("remote.updated", {"tailnet": status})
        return status

    async def stop(self) -> dict[str, Any]:
        (self.state_dir / "stop").write_text(str(time.time()), encoding="utf-8")
        for _ in range(25):
            if not self.running_pid():
                break
            await asyncio.sleep(0.2)
        status = self.status()
        self.app.bus.publish("remote.updated", {"tailnet": status})
        return status

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        self.app.settings.update({"server.tailnet": enabled})
        return await (self.start() if enabled else self.stop())


def adopt_identity(source_state: Path, target_state: Path) -> bool:
    """Move an existing helper identity (e.g. Blackbird's) to Weebo so no new device login is needed.
    The source is renamed, not deleted, so it can be restored by hand."""
    source_node = source_state / "node"
    target_node = target_state / "node"
    if not (source_node / "tailscaled.state").exists() or target_node.exists():
        return False
    shutil.copytree(source_node, target_node)
    source_state.rename(source_state.with_name(source_state.name + ".moved-to-weebo"))
    return True


if __name__ == "__main__":  # python -m weebo.remote adopt <state dir>
    if len(sys.argv) == 3 and sys.argv[1] == "adopt":
        print(adopt_identity(Path(sys.argv[2]), paths.sub("tailnet")))
