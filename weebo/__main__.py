"""``python -m weebo`` — start Weebo 2.0.

    python -m weebo              run under the supervisor (recommended)
    python -m weebo --child      run the server directly (no auto-restart)
    python -m weebo --selftest   boot self-test used by self-evolution
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weebo", description="Weebo 2.0 — your proactive, self-evolving AI.")
    parser.add_argument("--child", action="store_true", help="run the server process directly")
    parser.add_argument("--selftest", action="store_true", help="boot self-test (no Codex needed)")
    parser.add_argument("--host", help="override server.host")
    parser.add_argument("--port", type=int, help="override server.port")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = parser.parse_args(argv)

    if args.selftest:
        from .selftest import main as selftest
        return selftest()

    passthrough: list[str] = []
    if args.host:
        passthrough += ["--host", args.host]
    if args.port:
        passthrough += ["--port", str(args.port)]
    if args.no_browser:
        passthrough.append("--no-browser")

    if args.child:
        from .server.main import run
        return run(args.host, args.port, False if args.no_browser else None)

    from .supervisor import supervise
    return supervise(passthrough)


if __name__ == "__main__":
    sys.exit(main())
