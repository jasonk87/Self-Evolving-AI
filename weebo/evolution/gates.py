"""Quality gates every self-evolution build must pass before it can merge.

Weebo runs these itself instead of trusting the building agent's claims.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .git import run


@dataclass
class GateResult:
    name: str
    ok: bool
    output: str = ""
    seconds: float = 0.0
    skipped: bool = False


@dataclass
class GateReport:
    results: list[GateResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok or r.skipped for r in self.results)

    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.ok and not r.skipped]

    def to_dict(self) -> dict:
        return {"ok": self.ok, "results": [asdict(r) for r in self.results]}

    def summary(self) -> str:
        lines = []
        for r in self.results:
            mark = "skipped" if r.skipped else ("passed" if r.ok else "FAILED")
            lines.append(f"- {r.name}: {mark} ({r.seconds:.1f}s)")
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


async def run_gates(worktree: Path, changed: list[str], test_command: str = "") -> GateReport:
    report = GateReport()
    python = sys.executable
    data_dir = Path(tempfile.mkdtemp(prefix="weebo-gate-"))
    env = {"PYTHONPATH": str(worktree), "WEEBO_DATA_DIR": str(data_dir), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONIOENCODING": "utf-8"}
    try:
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
                result = await run([node, "--check", js], worktree, timeout=60)
                if not result.ok:
                    ok = False
                    outputs.append(f"{js}:\n{result.text()}")
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
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    return report
