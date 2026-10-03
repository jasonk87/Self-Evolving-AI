"""Process supervisor: keeps Weebo running across self-upgrades and crashes.

Deliberately tiny and stdlib-only, because it is the one piece that must keep
working when an upgrade breaks everything else (and the change policy treats it
as governance code that only a human may approve changes to).

* Exit code 75 from the server means "restart me" (after a self-upgrade).
* If the server dies within the first minute after an upgrade was merged, the
  supervisor reverts the merge commit and starts the previous version again.
* Repeated crash loops back off and eventually stop instead of spinning forever.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

RESTART_CODE = 75
EARLY_CRASH_SECONDS = 60
MAX_FAST_CRASHES = 5

ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    override = os.environ.get("WEEBO_DATA_DIR")
    return Path(override).expanduser().resolve() if override else ROOT / "weebo_data"


def _say(message: str) -> None:
    print(f"[weebo-supervisor] {message}", flush=True)


def _rollback(pending_path: Path) -> bool:
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if pending.get("rolled_back"):
        return False
    merged = pending.get("merged_commit")
    if not merged:
        return False
    _say(f"The upgrade {merged[:8]} failed to boot. Reverting it…")
    result = subprocess.run(
        ["git", "-c", "user.name=Weebo", "-c", "user.email=weebo@localhost", "revert", "--no-edit", "-m", "1", merged],
        cwd=ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        subprocess.run(["git", "revert", "--abort"], cwd=ROOT, capture_output=True)
        _say("Automatic revert failed:\n" + (result.stdout + result.stderr)[-1500:])
        return False
    pending["rolled_back"] = True
    pending["rolled_back_at"] = time.time()
    pending_path.write_text(json.dumps(pending), encoding="utf-8")
    _say("Reverted. Starting the previous version.")
    return True


def _stop_helpers() -> None:
    """Weebo is quitting for good: ask its Tailscale helper to shut down (it outlives mere restarts)."""
    tailnet = _data_dir() / "tailnet"
    if tailnet.is_dir():
        try:
            (tailnet / "stop").write_text(str(time.time()), encoding="utf-8")
        except OSError:
            pass


def supervise(child_args: list[str]) -> int:
    fast_crashes = 0
    restarted = False
    while True:
        env = dict(os.environ)
        env["WEEBO_SUPERVISOR_PID"] = str(os.getpid())  # lets the server know someone will restart it
        if restarted:
            env["WEEBO_RESTARTED"] = "1"
        started = time.time()
        proc = subprocess.Popen([sys.executable, "-m", "weebo", "--child", *child_args], cwd=ROOT, env=env)
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            _say("Stopping…")
            try:
                code = proc.wait(timeout=10)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                proc.kill()
            _stop_helpers()
            return 0
        uptime = time.time() - started
        restarted = True
        if code == 0:
            _stop_helpers()
            return 0
        if code == RESTART_CODE:
            _say("Restarting into the new version…")
            fast_crashes = 0
            continue
        pending = _data_dir() / "evolution_pending.json"
        if uptime < EARLY_CRASH_SECONDS and pending.exists() and _rollback(pending):
            continue
        fast_crashes = fast_crashes + 1 if uptime < EARLY_CRASH_SECONDS else 1
        if fast_crashes >= MAX_FAST_CRASHES:
            _say(f"Weebo crashed {fast_crashes} times in a row; giving up. Check weebo_data/logs/weebo.log.")
            return code
        delay = min(30, 2 ** fast_crashes)
        _say(f"Weebo exited with code {code}; restarting in {delay}s…")
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            return 0
