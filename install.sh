#!/bin/bash

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

if ! command -v python3 &> /dev/null; then
    echo "Installing Python 3..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y python3 python3-pip
    elif command -v pacman &> /dev/null; then
        sudo pacman -S --noconfirm python
    else
        echo "Please install Python 3 manually."
        exit 1
    fi
elif ! python3 -c "import venv" &> /dev/null; then
    echo "Installing Python 3 venv module..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y python3-venv
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y python3-virtualenv
    else
        echo "Please install Python 3 venv module manually."
        exit 1
    fi
fi

if ! command -v ffmpeg &> /dev/null; then
    echo "Installing FFmpeg..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y ffmpeg
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y ffmpeg
    elif command -v pacman &> /dev/null; then
        sudo pacman -S --noconfirm ffmpeg
    else
        echo "Please install FFmpeg manually."
    fi
fi

if [ ! -d "$PROJECT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$PROJECT_DIR/venv"
fi

echo "Installing Python dependencies..."
"$PROJECT_DIR/venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")

if [ -d "$DESKTOP_DIR" ]; then
    DESKTOP_FILE="$DESKTOP_DIR/readaloud.desktop"
    
    cat <<EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=ReadAloud
Comment=Text to Speech Application
Exec=$PROJECT_DIR/start.sh
Icon=$PROJECT_DIR/src/resource/v_2.png
Path=$PROJECT_DIR
Terminal=false
Categories=Utility;AudioVideo;
EOF

    chmod +x "$DESKTOP_FILE"
    if command -v gio &> /dev/null; then
        gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
    fi
    echo "Desktop shortcut created at $DESKTOP_FILE"
else
    echo "Desktop directory not found."
fi

echo "Installation complete."
