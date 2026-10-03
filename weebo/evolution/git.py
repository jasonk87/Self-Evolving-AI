"""Async git helpers for self-evolution."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitResult:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def text(self) -> str:
        return (self.out + ("\n" + self.err if self.err else "")).strip()


class GitError(RuntimeError):
    pass


async def run(args: list[str], cwd: str | Path, timeout: float = 120, env: dict[str, str] | None = None) -> GitResult:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    full_env = dict(os.environ)
    full_env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true", "GIT_MERGE_AUTOEDIT": "no"})
    if env:
        full_env.update(env)
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=full_env, **kwargs
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return GitResult(124, "", f"Timed out after {timeout:.0f}s: {' '.join(args)}")
    return GitResult(proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


async def git(cwd: str | Path, *args: str, timeout: float = 120, check: bool = False) -> GitResult:
    result = await run(["git", *args], cwd, timeout)
    if check and not result.ok:
        raise GitError(f"git {' '.join(args)} failed: {result.text()[:800]}")
    return result


async def head(cwd: str | Path) -> str:
    return (await git(cwd, "rev-parse", "HEAD", check=True)).out.strip()


async def current_branch(cwd: str | Path) -> str:
    result = await git(cwd, "rev-parse", "--abbrev-ref", "HEAD", check=True)
    return result.out.strip()


async def is_repo(cwd: str | Path) -> bool:
    result = await git(cwd, "rev-parse", "--is-inside-work-tree")
    return result.ok and result.out.strip() == "true"


async def dirty_tracked(cwd: str | Path) -> list[str]:
    result = await git(cwd, "status", "--porcelain", "--untracked-files=no", check=True)
    return [line[3:] for line in result.out.splitlines() if line.strip()]


async def working_changes(cwd: str | Path) -> list[str]:
    """Every uncommitted change, including new untracked files (ignored files excluded)."""
    result = await git(cwd, "status", "--porcelain", "--untracked-files=all", check=True)
    return [line[3:].strip() for line in result.out.splitlines() if line.strip()]


async def snapshot_commit(cwd: str | Path) -> str:
    """A commit object holding the working tree exactly as it is now (uncommitted and untracked work included),
    created without touching the user's index, working tree, branch or history. Returns HEAD when clean."""
    base = await head(cwd)
    with tempfile.TemporaryDirectory(prefix="weebo-snapshot-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        for args in (["git", "read-tree", "HEAD"], ["git", "add", "-A"]):
            result = await run(args, cwd, timeout=300, env=env)
            if not result.ok:
                raise GitError(f"{' '.join(args)} failed: {result.text()[:500]}")
        tree = (await run(["git", "write-tree"], cwd, env=env)).out.strip()
    head_tree = (await git(cwd, "rev-parse", "HEAD^{tree}", check=True)).out.strip()
    if tree == head_tree:
        return base
    result = await git(cwd, "-c", "user.name=Weebo", "-c", "user.email=weebo@localhost", "commit-tree", tree,
                       "-p", base, "-m", "Weebo snapshot of uncommitted work (build base)", check=True)
    return result.out.strip()


async def changed_files(cwd: str | Path, base: str, ref: str = "HEAD") -> list[str]:
    result = await git(cwd, "diff", "--name-only", f"{base}...{ref}", check=True)
    return [line.strip() for line in result.out.splitlines() if line.strip()]
