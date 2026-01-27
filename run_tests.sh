#!/bin/bash

# ReadAloud Test Runner
# This script executes all unit, integration, and UI tests.

# Exit on any failure
set -e

echo "------------------------------------------------"
echo "🚀 Starting ReadAloud Test Suite"
echo "------------------------------------------------"

# Ensure we are in the project root
cd "$(dirname "$0")"

# Running experiments to see if python3 -m unittest discover works
# but for maximum reliability we list the main test files.

echo "📦 Running core test suite..."
PYTHONPATH=. ./venv/bin/python3 -m unittest \
    tests/unit/test_queue_service.py \
    tests/unit/test_generation_service.py \
    tests/unit/test_assembly_service.py \
    tests/unit/test_text_processor.py \
    tests/unit/test_text_chunker.py \
    tests/unit/test_file_manager.py \
    tests/unit/test_progress_tracker.py \
    tests/unit/test_thread_manager.py \
    tests/unit/test_audio_generator.py \
    tests/unit/test_app_controller.py \
    tests/test_integration.py \
    tests/test_concurrency.py \
    tests/test_gui.py

echo "📦 Running GUI logic suite (offscreen)..."
PYTHONPATH=. QT_QPA_PLATFORM=offscreen ./venv/bin/python3 tests/unit/test_gui_logic.py

echo ""
echo "------------------------------------------------"
echo "✅ All tests passed successfully!"
echo "------------------------------------------------"
