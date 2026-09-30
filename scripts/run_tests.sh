#!/bin/bash

# ReadAloud Test Runner
# Runs every test module in its own pytest process (see tests/run_all_tests.py).
# Tests marked docker / gpu / integration are skipped by default:
#   scripts/run_tests.sh --all        # include them (needs Docker / GPU)
#   scripts/run_tests.sh -- -k name   # extra arguments are passed to pytest

set -e

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-./venv/bin/python3}"
[ -x "$PYTHON" ] || PYTHON=python3

echo "------------------------------------------------"
echo "🚀 Starting ReadAloud Test Suite"
echo "------------------------------------------------"

PYTHONPATH=. QT_QPA_PLATFORM=offscreen "$PYTHON" tests/run_all_tests.py "$@"

echo ""
echo "------------------------------------------------"
echo "✅ All tests passed successfully!"
echo "------------------------------------------------"
