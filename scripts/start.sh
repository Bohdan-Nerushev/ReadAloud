#!/bin/bash
# Quick start script for ReadAloud application

# Change to project root directory
cd "$(dirname "$0")/.." || exit 1

echo "========================================"
echo "ReadAloud - Text to Speech Application"
echo "========================================"
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
    echo ""
fi

# Check if dependencies are installed
if [ ! -f "venv/bin/gtts-cli" ]; then
    echo "Installing dependencies..."
    ./venv/bin/pip install -r requirements.txt
    echo "✓ Dependencies installed"
    echo ""
fi

# Check if FFmpeg is installed
if ! command -v ffmpeg &> /dev/null; then
    echo "⚠ WARNING: FFmpeg is not installed!"
    echo "Please install it using:"
    echo "  Ubuntu/Debian: sudo apt-get install ffmpeg"
    echo "  Fedora: sudo dnf install ffmpeg"
    echo "  Arch: sudo pacman -S ffmpeg"
    echo ""
    read -p "Press Enter to continue anyway..."
fi

echo "Starting ReadAloud application..."
export PYTHONPATH=$PYTHONPATH:.
./venv/bin/python src/main.py
