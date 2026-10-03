#!/usr/bin/env sh
# Weebo 2.0 launcher for macOS/Linux.
cd "$(dirname "$0")" || exit 1
exec python3 -m weebo "$@"
