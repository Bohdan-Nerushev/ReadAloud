# ReadAloud - High-Performance Text-to-Speech Application

A high-performance Python & PyQt6 desktop application powered by Microsoft Edge TTS to convert text documents into high-quality MP3 audio. It features concurrent queue management with real-time monitoring (progress, ETA) and pause/resume controls, human-like voice synthesis with customizable speed and gender selection, and a multi-threaded architecture (up to 40 threads) with smart text chunking and structured logging.

<p align="center">
  <img src="docs/video_instructions/recording.gif" alt="ReadAloud Usage Demo" width="600" />
</p>

## Table of Contents

- [ReadAloud - High-Performance Text-to-Speech Application](#readaloud---high-performance-text-to-speech-application)
  - [Table of Contents](#table-of-contents)
  - [1. 🛠 System Requirements](#1--system-requirements)
  - [2. Installation \& Setup](#2-installation--setup)
    - [2.1. One-line Installation](#21-one-line-installation)
    - [2.2. Manual Installation (Alternative)](#22-manual-installation-alternative)
    - [2.2.1. Installing System Dependencies](#221-installing-system-dependencies)
    - [2.3. Uninstallation](#23-uninstallation)
  - [3. Usage](#3-usage)
    - [3.1. Starting the Application](#31-starting-the-application)
    - [3.2. Running Tests](#32-running-tests)

## 1. 🛠 System Requirements

- **Python**: 3.12 or higher
- **FFmpeg**: Essential for audio stream assembly and speed adjustments.
- **Docker** *(Optional)*: Required only if using local neural TTS (Piper).
- **libxcb-cursor**: Required for GUI cursor management.
- **OS**: Linux (tested on Ubuntu / Debian / Arch).

---

## TTS Engine Backends & Security

ReadAloud provides two TTS synthesis engines:

1. **Edge TTS (Cloud - Default)**:
   - Powered by Microsoft Edge Text-to-Speech API.
   - Fast online synthesis supporting multi-threaded requests.

2. **Piper TTS (Local Neural TTS - Optional)**:
   - High-quality neural synthesis running locally via Docker (`rhasspy/wyoming-piper`).
   - **100% Free & Open-Source (MIT License)**: Free for personal and commercial use. No API keys, no subscriptions, no payments.
   - **100% Private & Safe**: Performs synthesis entirely offline on your local network interface (`127.0.0.1:10200`). Zero data transmission to external servers. Your text and generated audio files never leave your computer.
   - **Resource Management**: ReadAloud automatically starts the Docker container when synthesis begins and stops it when processing finishes to free RAM and CPU.

### 🌐 Official Piper Resources & Voice Samples

- 🎵 **Official Audio Samples & Demos**: [https://rhasspy.github.io/piper-samples/](https://rhasspy.github.io/piper-samples/)
- 📦 **Official Voice Models Repository (HuggingFace)**: [https://huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)
- 💻 **Official Source Code (GitHub)**: [https://github.com/rhasspy/piper](https://github.com/rhasspy/piper)

---

## 2. Installation & Setup

You can install the application automatically in one command using the remote installation script (it installs dependencies, clones/updates the repository, sets up the virtual environment, and generates a desktop shortcut):

### 2.1. One-line Installation

**Using wget:**
```bash
wget -O install.sh https://raw.githubusercontent.com/Bohdan-Nerushev/ReadAloud/master/scripts/install.sh
bash install.sh
```

*Note: The installation directory will be created at `./ReadAloud` relative to the directory where the command was executed. The desktop shortcut will be generated on your Desktop (e.g., `~/Desktop` or `~/Schreibtisch`). You may need to right-click the shortcut on your desktop and select **"Allow Launching"** to trust and enable it.*

### 2.2. Manual Installation (Alternative)

### 2.2.1. Installing System Dependencies
If you prefer to install manually:
```bash
# Ubuntu/Debian
sudo apt-get update && sudo apt-get install ffmpeg libxcb-cursor0

# Fedora
sudo dnf install ffmpeg libxcb-cursor

# Arch Linux
sudo pacman -S ffmpeg libxcb-cursor
```

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Bohdan-Nerushev/ReadAloud
   cd ReadAloud
   ```

2. **Set up virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

### 2.3. Uninstallation

If you wish to uninstall ReadAloud, you can download and run the remote uninstallation script in one command:

**Using wget:**
```bash
wget -O uninstall.sh https://raw.githubusercontent.com/Bohdan-Nerushev/ReadAloud/master/scripts/uninstall.sh
bash uninstall.sh
```

This will automatically clean up the program files, remove the desktop shortcut, and unregister the application from your system menu.

---

## 3. Usage

### 3.1. Starting the Application
```bash
./scripts/start.sh
```

---

### 3.2. Running Tests
To execute the comprehensive test suite (unit, integration, concurrency, and UI tests):
```bash
./scripts/run_tests.sh
```

venv/bin/python -m pytest -v
