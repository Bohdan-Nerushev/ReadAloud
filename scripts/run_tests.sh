#!/bin/bash

# ReadAloud Test Runner
# This script executes all unit, integration, and UI tests.

# Exit on any failure
set -e

echo "------------------------------------------------"
echo "🚀 Starting ReadAloud Test Suite"
echo "------------------------------------------------"

# Ensure we are in the project root
cd "$(dirname "$0")/.."

# Tests are listed explicitly to ensure deterministic execution order
# and prevent accidental discovery of non-test utility scripts.

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
    tests/unit/test_audio_assembler.py \
    tests/unit/test_persistence_service.py \
    tests/test_queue.py \
    tests/test_persistence_integration.py \
    tests/test_optimization.py \
    tests/stress_test_large_file.py \
    tests/test_integration.py \
    tests/test_concurrency.py \
    tests/test_gui.py

echo "📦 Running GUI logic suite (offscreen)..."
PYTHONPATH=. QT_QPA_PLATFORM=offscreen ./venv/bin/python3 tests/unit/test_gui_logic.py

echo ""
echo "------------------------------------------------"
echo "✅ All tests passed successfully!"
echo "------------------------------------------------"
