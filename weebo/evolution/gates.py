"""Quality gates every self-evolution build must pass before it can merge.

Weebo runs these itself instead of trusting the building agent's claims.

The build agent may edit the test suite it is judged by, so tests alone can't prove a change is safe. Two
checks cover that: the original version of every existing test file the change modified is run against the new code
(advisory: a failure there means old expectations changed, which a person then has to confirm), and the
change policy refuses to auto-merge any change that modifies or deletes existing tests, including append-only edits.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import git as gitlib
from .git import run


@dataclass
class GateResult:
    name: str
    ok: bool
    output: str = ""
    seconds: float = 0.0
    skipped: bool = False
    advisory: bool = False  # shown to the reviewer, never fails the build on its own


@dataclass
class GateReport:
    results: list[GateResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok or r.skipped or r.advisory for r in self.results)

    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.ok and not r.skipped and not r.advisory]

    def result(self, name: str) -> GateResult | None:
        return next((r for r in self.results if r.name == name), None)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "results": [asdict(r) for r in self.results]}

    def summary(self) -> str:
        lines = []
        for r in self.results:
            mark = "skipped" if r.skipped else ("passed" if r.ok else "FAILED")
            lines.append(f"- {r.name}: {mark}{' (advisory)' if r.advisory else ''} ({r.seconds:.1f}s)")
        return "\n".join(lines)

    def failure_text(self, limit: int = 6000) -> str:
        chunks = [f"## {r.name}\n{r.output[-2500:]}" for r in self.failures()]
        return "\n\n".join(chunks)[-limit:]


def _tail(text: str, limit: int = 8000) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


async def _step(name: str, args: list[str], cwd: Path, timeout: float, env: dict[str, str] | None = None) -> GateResult:
    started = time.perf_counter()
    result = await run(args, cwd, timeout=timeout, env=env)
    return GateResult(name, result.ok, _tail(result.text()), time.perf_counter() - started)


EXISTING_TESTS = "Existing tests"
PERSONAL_DETAILS = "Personal details"


async def check_js(node: str, path: Path) -> str:
    """Parse one JavaScript file; returns the error ('' when it parses).

    ``node --check file.js`` can't be trusted for the UI's ES modules: Node guesses the module type from the
    .js extension, and when the guess fails it can exit 0 on a real syntax error. So the file is parsed under
    explicit extensions instead: it passes if it is a valid ES module (.mjs) or a valid classic script (.cjs).
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    errors = []
    with tempfile.TemporaryDirectory(prefix="weebo-jscheck-") as tmp:
        for suffix in (".mjs", ".cjs"):
            probe = Path(tmp) / (path.stem + suffix)
            probe.write_text(source, encoding="utf-8")
            result = await run([node, "--check", str(probe)], tmp, timeout=60)
            if result.ok:
                return ""
            errors.append(result.text().replace(str(probe), str(path)))
    return errors[0]  # as an ES module: what the UI's files are


def _term_pattern(term: str) -> re.Pattern[str]:
    flags = re.IGNORECASE if "@" in term else 0  # emails in any case; names as written, to spare words like "will"
    return re.compile(rf"(?<![\w@.]){re.escape(term)}(?![\w@])", flags)


async def personal_details(worktree: Path, base_commit: str, terms: tuple[str, ...]) -> GateResult:
    """Fail when the change hardcodes the user's own details (their name, their email) into the code.
    Weebo's code is shared and reused; who the user is belongs in settings, read at runtime."""
    started = time.perf_counter()
    terms = tuple(t.strip() for t in terms if t and len(t.strip()) >= 3)
    if not terms or not base_commit:
        return GateResult(PERSONAL_DETAILS, True, "No personal details to check for.", skipped=True)
    patterns = [_term_pattern(t) for t in terms]
    hits = []
    for path, number, text in await gitlib.added_lines(worktree, base_commit):
        if path.startswith("weebo_data/"):
            continue
        if any(p.search(text) for p in patterns):
            hits.append(f"{path}:{number}: {text.strip()[:160]}")
    if not hits:
        return GateResult(PERSONAL_DETAILS, True, "The change doesn't hardcode the user's name or email.",
                          seconds=time.perf_counter() - started)
    return GateResult(PERSONAL_DETAILS, False,
                      "These added lines hardcode the user's personal details. Read the name from the user.name setting "
                      "at runtime, or write for 'you' / 'the user' instead (tests: use a made-up name).\n"
                      + "\n".join(hits[:40]), seconds=time.perf_counter() - started)


def _is_test_module(path: str) -> bool:
    return Path(path).name.startswith("test_") and path.endswith(".py")


def _affected_tests(rewritten: list[str], originals: list[str], root: Path) -> set[str]:
    """Original test files whose expectations a rewrite can change: the rewritten test files themselves, every
    test in a folder whose conftest.py was rewritten, and every test that imports a rewritten test module."""
    targets = {p for p in rewritten if _is_test_module(p)}
    for path in rewritten:
        if Path(path).name == "conftest.py":
            folder = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
            targets.update(p for p in originals if _is_test_module(p) and p.startswith(folder))
    modules = {p: p[:-3].replace("/", ".") for p in rewritten if _is_test_module(p)}
    for path in originals:
        if not _is_test_module(path) or path in targets:
            continue
        try:
            source = (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for module_path, dotted in modules.items():
            same_folder = module_path.rsplit("/", 1)[0] == path.rsplit("/", 1)[0]
            package, _, stem = dotted.rpartition(".")
            if (dotted in source or (package and _imports_from(source, re.escape(package), stem))
                    or (same_folder and (re.search(rf"from\s+\.\s*{stem}\b", source)
                                         or _imports_from(source, r"\.", stem)))):
                targets.add(path)
                break
    return targets


def _imports_from(source: str, package: str, name: str) -> bool:
    """Whether ``source`` has ``from <package> import ... name ...`` (one line or a parenthesized list)."""
    for match in re.finditer(rf"from\s+{package}\s+import\s+(\([^)]*\)|[^\n]*)", source):
        if re.search(rf"\b{re.escape(name)}\b", match.group(1)):
            return True
    return False


async def existing_tests(worktree: Path, base_commit: str, env: dict[str, str]) -> GateResult | None:
    """Run the original version of every existing test file the change modified against the new code."""
    started = time.perf_counter()
    rewritten = [p for p in await gitlib.rewritten_files(worktree, base_commit, pathspec="tests") if p.endswith(".py")]
    if not rewritten:
        return None
    header = "The change modified or deleted existing tests:\n" + "\n".join(f"- {p}" for p in rewritten)
    with tempfile.TemporaryDirectory(prefix="weebo-baseline-tests-") as tmp:
        root = Path(tmp)
        # The whole original tests/ tree: test modules can import each other's fixtures and helpers.
        originals = await gitlib.extract_tree(worktree, base_commit, "tests", root)
        targets = _affected_tests(rewritten, originals, root)
        runnable = sorted(p for p in targets if (root / p).exists())
        if not runnable:
            return GateResult(EXISTING_TESTS, True, header + "\n\nNo original test files to re-run.",
                              seconds=time.perf_counter() - started, advisory=True)
        result = await run([sys.executable, "-m", "pytest", *runnable, "-q", "-p", "no:cacheprovider"], root,
                           timeout=1500, env=env)
    verdict = ("The original tests still pass against the new code." if result.ok else
               "Some original expectations no longer hold. A person should confirm each was meant to change.")
    return GateResult(EXISTING_TESTS, result.ok, f"{header}\n\n{verdict}\n\n{_tail(result.text(), 6000)}",
                      seconds=time.perf_counter() - started, advisory=True)


async def run_gates(worktree: Path, changed: list[str], test_command: str = "", *, base_commit: str = "",
                    personal_terms: tuple[str, ...] = ()) -> GateReport:
    report = GateReport()
    python = sys.executable
    data_dir = Path(tempfile.mkdtemp(prefix="weebo-gate-"))
    env = {"PYTHONPATH": str(worktree), "WEEBO_DATA_DIR": str(data_dir), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONIOENCODING": "utf-8"}
    try:
        report.results.append(await personal_details(worktree, base_commit, personal_terms))

        py_files = [f for f in changed if f.endswith(".py") and (worktree / f).exists()]
        if py_files:
            report.results.append(await _step("Python syntax", [python, "-m", "py_compile", *py_files], worktree, 120, env))
        else:
            report.results.append(GateResult("Python syntax", True, "No Python files changed.", skipped=True))

        js_files = [f for f in changed if f.endswith((".js", ".mjs")) and (worktree / f).exists()]
        node = shutil.which("node")
        if js_files and node:
            outputs, ok, started = [], True, time.perf_counter()
            for js in js_files:
                error = await check_js(node, worktree / js)
                if error:
                    ok = False
                    outputs.append(f"{js}:\n{error}")
            report.results.append(GateResult("JavaScript syntax", ok, _tail("\n".join(outputs) or "ok"),
                                             time.perf_counter() - started))
        else:
            reason = "No JavaScript files changed." if not js_files else "node not found."
            report.results.append(GateResult("JavaScript syntax", True, reason, skipped=True))

        report.results.append(await _step("Boot self-test", [python, "-m", "weebo", "--selftest"], worktree, 180, env))

        if test_command.strip():
            args = shlex.split(test_command, posix=os.name != "nt")
            report.results.append(await _step("Tests", args, worktree, 1500, env))
        elif (worktree / "tests" / "weebo").is_dir():
            report.results.append(await _step(
                "Tests", [python, "-m", "pytest", "tests/weebo", "-q", "-p", "no:cacheprovider", "-x"],
                worktree, 1500, env))
        else:
            report.results.append(GateResult("Tests", True, "No tests/weebo directory.", skipped=True))

        if base_commit:
            original = await existing_tests(worktree, base_commit, env)
            if original:
                report.results.append(original)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    return report
