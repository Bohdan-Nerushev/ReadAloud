#!/bin/bash

# Define installation directory relative to where the command is executed
INSTALL_DIR="$PWD/ReadAloud"

echo "=================================================="
echo "    ReadAloud Application Installer"
echo "=================================================="

# Update system packages if on Debian/Ubuntu
if command -v apt &> /dev/null; then
    echo "Performing system update and clean up..."
    sudo apt update && sudo apt full-upgrade -y && sudo apt autoremove --purge -y
    if command -v snap &> /dev/null; then
        sudo snap refresh || true
    fi
fi


# 1. Install Git if not present
if ! command -v git &> /dev/null; then
    echo "Installing Git..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y git
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y git
    elif command -v pacman &> /dev/null; then
        sudo pacman -S --noconfirm git
    else
        echo "Git is not installed. Please install git manually and rerun."
        exit 1
    fi
fi

# 2. Clone or update repository
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Repository already exists at $INSTALL_DIR. Updating to the latest master branch version..."
    cd "$INSTALL_DIR" || exit 1
    git -c http.sslVerify=false fetch origin master
    git -c http.sslVerify=false checkout master
    git -c http.sslVerify=false reset --hard origin/master
else
    echo "Cloning ReadAloud repository (master branch) to $INSTALL_DIR..."
    git -c http.sslVerify=false clone -b master https://git.mam.dev/bnerushev/readaloud.git "$INSTALL_DIR"
    cd "$INSTALL_DIR" || exit 1
fi

# 3. Check and install Python 3 & venv
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

# 4. Check and install FFmpeg
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

# 5. Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# 6. Install Python dependencies
echo "Installing Python dependencies..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 7. Create desktop shortcut
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")

if [ -d "$DESKTOP_DIR" ]; then
    DESKTOP_FILE="$DESKTOP_DIR/readaloud.desktop"
    
    cat <<EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=ReadAloud
Comment=Text to Speech Application
Exec=$INSTALL_DIR/start.sh
Icon=$INSTALL_DIR/src/resource/v_2.png
Path=$INSTALL_DIR
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

echo "=================================================="
echo "Installation completed successfully."
echo "You can launch ReadAloud via the Desktop icon"
echo "or by running: $INSTALL_DIR/start.sh"
echo "=================================================="
