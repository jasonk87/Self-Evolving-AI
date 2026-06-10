#!/usr/bin/env bash
set -euo pipefail

LANE=${1:-unit}

case "$LANE" in
  unit)
    PYTHONPATH=. pytest -q -m "not integration and not smoke"
    ;;
  integration)
    PYTHONPATH=. pytest -q -m "integration"
    ;;
  smoke)
    PYTHONPATH=. pytest -q -m "smoke"
    ;;
  all)
    PYTHONPATH=. pytest -q
    ;;
  *)
    echo "Unknown lane: $LANE"
    echo "Usage: $0 [unit|integration|smoke|all]"
    exit 2
    ;;
esac
