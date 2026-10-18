"""Launch a vetted connector with an explicit environment, not Weebo's credentials.

This is environment isolation, NOT a filesystem/network/OS sandbox. Kept standalone
so Codex can invoke it with ``python -I /absolute/path/launch.py`` from any cwd.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Mapping

BASE_ENV = frozenset({"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
                      "LANG", "LC_ALL", "LC_CTYPE"})


def validate_names(names: object) -> list[str]:
    if not isinstance(names, list) or any(not isinstance(n, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n)
                                         for n in names):
        raise ValueError("env_names must be a list of exact environment variable names (no wildcards or values).")
    return list(dict.fromkeys(names))


def connector_environment(names: list[str] | None = None, source: Mapping[str, str] | None = None) -> dict[str, str]:
    allowed = BASE_ENV | set(validate_names(names or []))
    source = os.environ if source is None else source
    if os.name == "nt":
        allowed = {n.upper() for n in allowed}
        return {k: v for k, v in source.items() if k.upper() in allowed}
    return {k: v for k, v in source.items() if k in allowed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-names", default="[]")
    parser.add_argument("--cwd")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a connector command is required after --")
    return subprocess.call(command, env=connector_environment(validate_names(json.loads(args.env_names))),
                           cwd=args.cwd)


if __name__ == "__main__":
    raise SystemExit(main())
