"""Find the newest Codex CLI binary on this machine.

The Codex desktop app bundles a newer engine than the npm CLI is often updated
to, and older engines reject newer models, so we always pick the highest
version we can find.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(order=True)
class CodexBinary:
    version: tuple[int, int, int]
    path: str = field(compare=False)
    source: str = field(compare=False, default="")
    extra_path_dirs: list[str] = field(compare=False, default_factory=list)

    @property
    def version_str(self) -> str:
        return ".".join(str(p) for p in self.version)

    def command(self) -> list[str]:
        if self.path.lower().endswith(".js"):
            return ["node", self.path]
        if os.name == "nt" and self.path.lower().endswith((".cmd", ".bat")):
            return ["cmd.exe", "/d", "/c", self.path]
        return [self.path]

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.extra_path_dirs:
            env["PATH"] = os.pathsep.join([*self.extra_path_dirs, env.get("PATH", "")])
        env.setdefault("NO_COLOR", "1")
        env["RUST_LOG"] = env.get("WEEBO_CODEX_RUST_LOG", "error")
        return env


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _probe(path: str, extra_dirs: list[str], source: str) -> CodexBinary | None:
    candidate = CodexBinary((0, 0, 0), path, source, extra_dirs)
    try:
        out = subprocess.run(
            [*candidate.command(), "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            env=candidate.env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    version = parse_version(out.stdout + out.stderr)
    if version is None:
        return None
    candidate.version = version
    return candidate


def _candidates() -> list[tuple[str, list[str], str]]:
    found: list[tuple[str, list[str], str]] = []
    exe = "codex.exe" if os.name == "nt" else "codex"

    local = os.environ.get("LOCALAPPDATA")
    if local:
        bin_root = Path(local) / "OpenAI" / "Codex" / "bin"
        helper_dirs = [str(p) for p in bin_root.glob("*") if p.is_dir()]
        for path in glob.glob(str(bin_root / "*" / exe)):
            found.append((path, helper_dirs, "codex-desktop"))

    if sys.platform == "darwin":
        for path in ("/Applications/Codex.app/Contents/Resources/codex",
                     str(Path.home() / "Applications/Codex.app/Contents/Resources/codex")):
            if os.path.exists(path):
                found.append((path, [], "codex-desktop"))

    npm_roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        npm_roots.append(Path(appdata) / "npm" / "node_modules")
    npm_roots += [Path("/usr/local/lib/node_modules"), Path("/usr/lib/node_modules"),
                  Path.home() / ".npm-global" / "lib" / "node_modules"]
    for root in npm_roots:
        pattern = str(root / "@openai" / "codex" / "node_modules" / "@openai" / "codex-*" / "vendor" / "*" / "bin" / exe)
        for path in glob.glob(pattern):
            helper = Path(path).parent.parent / "codex-path"
            found.append((path, [str(helper)] if helper.exists() else [], "npm"))

    on_path = shutil.which("codex")
    if on_path:
        found.append((on_path, [], "PATH"))
    return found


def find_codex(explicit: str = "") -> CodexBinary | None:
    """Return the best Codex binary. An explicit path always wins if it works."""
    explicit = explicit or os.environ.get("WEEBO_CODEX_BIN", "")
    if explicit:
        probed = _probe(explicit, [], "configured")
        if probed:
            return probed
    best: CodexBinary | None = None
    seen: set[str] = set()
    for path, extra, source in _candidates():
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        probed = _probe(path, extra, source)
        if probed and (best is None or probed.version > best.version):
            best = probed
    return best


def list_codex() -> list[CodexBinary]:
    results: list[CodexBinary] = []
    seen: set[str] = set()
    for path, extra, source in _candidates():
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        probed = _probe(path, extra, source)
        if probed:
            results.append(probed)
    return sorted(results, reverse=True)
