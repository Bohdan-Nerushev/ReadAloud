#!/bin/bash

# Define installation directory relative to where the command is executed
INSTALL_DIR="$PWD/ReadAloud"

echo "=================================================="
echo "    🗑️ ReadAloud Application Uninstaller 🗑️"
echo "=================================================="

# 1. Remove Desktop Shortcut
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
if [ -d "$DESKTOP_DIR" ]; then
    DESKTOP_FILE="$DESKTOP_DIR/readaloud.desktop"
    if [ -f "$DESKTOP_FILE" ]; then
        rm -f "$DESKTOP_FILE"
        echo "🧹 Removed desktop shortcut: $DESKTOP_FILE"
    else
        echo "ℹ️ Desktop shortcut not found."
    fi
fi

# 2. Remove System Menu Entry
APPS_DIR="$HOME/.local/share/applications"
APP_MENU_FILE="$APPS_DIR/readaloud.desktop"
if [ -f "$APP_MENU_FILE" ]; then
    rm -f "$APP_MENU_FILE"
    if command -v update-desktop-database &> /dev/null; then
        update-desktop-database "$APPS_DIR" 2>/dev/null || true
    fi
    echo "🧹 Removed system applications menu entry: $APP_MENU_FILE"
else
    echo "ℹ️ System menu entry not found."
fi

# 3. Remove Program Files
if [ -d "$INSTALL_DIR" ]; then
    echo "🗑️ Removing ReadAloud program files from $INSTALL_DIR..."
    rm -rf "$INSTALL_DIR"
    echo "✨ Program files successfully removed."
else
    echo "⚠️ ReadAloud installation directory not found at $INSTALL_DIR."
    echo "If you installed it elsewhere, please delete the folder manually."
fi

echo "=================================================="
echo "    🎉 Uninstallation completed successfully! 🎉"
echo "=================================================="
