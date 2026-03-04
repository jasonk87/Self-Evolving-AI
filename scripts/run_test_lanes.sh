#!/usr/bin/env bash
set -euo pipefail

LANE=${1:-unit}

case "$LANE" in
  unit)
    pytest -q -m "not integration and not smoke"
    ;;
  integration)
    pytest -q -m "integration"
    ;;
  smoke)
    pytest -q -m "smoke"
    ;;
  all)
    pytest -q
    ;;
  *)
    echo "Unknown lane: $LANE"
    echo "Usage: $0 [unit|integration|smoke|all]"
    exit 2
    ;;
esac
