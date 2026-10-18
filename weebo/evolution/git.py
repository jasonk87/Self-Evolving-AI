"""Async git helpers for self-evolution."""

from __future__ import annotations

import asyncio
import io
import os
import re
import subprocess
import tarfile
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
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.communicate()  # drain the pipes as well as reaping the child
        if isinstance(exc, asyncio.CancelledError):
            raise
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
    result = await git(cwd, "status", "--porcelain", "-z", "--untracked-files=no", check=True)
    return _status_paths(result.out)


async def working_changes(cwd: str | Path) -> list[str]:
    """Every uncommitted change, including new untracked files (ignored files excluded)."""
    result = await git(cwd, "status", "--porcelain", "-z", "--untracked-files=all", check=True)
    return _status_paths(result.out)


def _status_paths(output: str) -> list[str]:
    fields, paths = iter(output.split("\0")), []
    for record in fields:
        if not record:
            continue
        paths.append(record[3:])
        if "R" in record[:2] or "C" in record[:2]:
            original = next(fields, "")
            if original:
                paths.append(original)
    return paths


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


# The gates parse these diffs, so they must read the same on every machine whatever the user's git config or
# attribute files say (diff.noprefix, color.ui=always, external diff drivers, textconv, quoted non-ASCII paths,
# diff.interHunkContext, a "*.py -diff" or "binary" attribute that would turn a file's lines into "Binary files
# differ"). Renames are off: a rename has to show up as the old path deleted plus the new one added, or moving a
# file would hide the old path from the change policy (a moved protected file, a test "rewritten" by renaming it).
DISPLAY_DIFF = ("-c", "core.quotepath=false", "diff", "--no-color", "--no-ext-diff", "--no-textconv",
                "--src-prefix=a/", "--dst-prefix=b/")  # for people (the review panel): renames stay readable
PLAIN_DIFF = (*DISPLAY_DIFF, "--no-renames", "--text", "--inter-hunk-context=0")


async def changed_files(cwd: str | Path, base: str, ref: str = "HEAD") -> list[str]:
    """Every path the change touches; a rename lists both the old and the new path."""
    result = await git(cwd, *PLAIN_DIFF, "--name-only", "-z", f"{base}...{ref}", check=True)
    return [path for path in result.out.split("\0") if path]


async def deleted_files(cwd: str | Path, base: str, ref: str = "HEAD") -> list[str]:
    result = await git(cwd, *PLAIN_DIFF, "--diff-filter=D", "--name-only", "-z", f"{base}...{ref}", check=True)
    return [path for path in result.out.split("\0") if path]


async def clean_untracked(cwd: str | Path, keep: tuple[str, ...] = ()) -> GitResult:
    """Delete every file git doesn't track, ignored ones included, except the ``keep`` folders at the root (only
    there: a nested ".weebo-tmp" is cleaned like anything else). Best effort: a file the OS won't let go of is
    left behind (git says which) rather than failing the build."""
    return await git(cwd, "clean", "-ffdxq", *[arg for name in keep for arg in ("-e", "/" + name.strip("/"))],
                     timeout=300)


async def rewritten_files(cwd: str | Path, base: str, ref: str = "HEAD", pathspec: str = ".") -> list[str]:
    """Changed files that existed at ``base``, including append-only edits and deletions.

    Appended code can skip or replace original tests and fixtures without deleting a line. Only entirely new
    files are excluded; the caller must review any change to an existing test's expectations.
    """
    result = await git(cwd, *PLAIN_DIFF, "--name-only", "-z", "--diff-filter=a", f"{base}...{ref}", "--", pathspec,
                       check=True)
    return [path for path in result.out.split("\0") if path]


async def added_lines(cwd: str | Path, base: str, ref: str = "HEAD") -> list[tuple[str, int, str]]:
    """(path, new line number, text) for every line the change adds."""
    result = await git(cwd, *PLAIN_DIFF, "--unified=0", f"{base}...{ref}", check=True)
    lines, path, number, old_left, new_left = [], "", 0, 0, 0
    # Normalize actual CRLF records only: a bare CR, form feed or Unicode separator inside content is data.
    for raw in result.out.replace("\r\n", "\n").split("\n"):
        if old_left > 0 or new_left > 0:
            # Inside a hunk its header's counts say which lines are content, so an added line that happens to
            # read "+++ b/x" or "@@" (a diff quoted in a README) can't pass for a header and hide what follows.
            tag = raw[:1]
            if tag == "+":
                lines.append((path, number, raw[1:]))
                number, new_left = number + 1, new_left - 1
            elif tag == "-":
                old_left -= 1
            elif tag in (" ", ""):  # context (none at -U0, but count it if it's there)
                number, old_left, new_left = number + 1, old_left - 1, new_left - 1
            continue  # "\ No newline at end of file" counts for neither side
        if raw.startswith("+++ "):
            path = "" if raw == "+++ /dev/null" else _diff_path(raw[4:]).removeprefix("b/")
        elif raw.startswith("@@"):
            hunk = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
            if hunk:
                old_left, number = int(hunk.group(1) or 1), int(hunk.group(2))
                new_left = int(hunk.group(3) or 1)
    return lines


def _diff_path(value: str) -> str:
    """Decode git's C-quoted header paths; unquoted paths with spaces end in a separator tab."""
    if not value.startswith('"'):
        return value.removesuffix("\t")
    import ast
    # Git quotes non-ASCII bytes as octal escapes, not Unicode code points.
    literal = value.encode("utf-8").decode("latin1")
    return ast.literal_eval(literal).encode("latin1").decode("utf-8", "replace")


async def extract_tree(cwd: str | Path, ref: str, prefix: str, dest: Path) -> list[str]:
    """Write every file under ``prefix`` as it was at ``ref`` into ``dest`` (one ``git archive``).
    Returns the repository-relative paths written. Entries that would land outside ``dest`` are skipped."""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = await asyncio.create_subprocess_exec(
        "git", "archive", "--format=tar", ref, "--", prefix, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 300)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.communicate()
        raise
    if proc.returncode != 0:
        if b"did not match any files" in err:
            return []  # nothing under prefix at that commit
        raise GitError(f"git archive {ref} {prefix} failed: {err.decode('utf-8', 'replace')[:500]}")
    written = []
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(out)) as archive:
        for member in archive.getmembers():
            target = (dest / member.name).resolve()
            if not member.isfile() or root not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is not None:
                target.write_bytes(source.read())
                written.append(member.name)
    return written
