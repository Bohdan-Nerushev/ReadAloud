# ReadAloud - High-Performance Text-to-Speech Application

A professional-grade desktop application built with Python and PyQt6 for converting complex text documents into high-quality MP3 audio. Powered by Microsoft Edge TTS for natural-sounding synthesis.

## 🚀 Key Features

- **Queue Management**: Add multiple tasks to a processing queue and manage them concurrently.
- **Next-Gen Synthesis**: Uses Microsoft Edge TTS for superior, human-like voice quality.
- **Advanced Audio Controls**:
  - **Speed Regulation**: Adjust playback speed from 0.5x to 2.0x.
  - **Gender Selection**: Choose between Male and Female voices for supported languages.
  - **Multi-threaded Generation**: High-speed processing with up to 30 concurrent threads.
- **Smart Text Engine**:
  - Automatic sanitization of special characters.
  - Intelligent text chunking (word-boundary aware).
  - Parallel I/O operations for text and audio preparation.
- **Real-Time Monitoring**:
  - Per-task progress tracking with percentage and status.
  - Global ETA calculation for the entire queue.
  - Interactive Pause/Resume/Delete controls.
- **Enterprise-Ready Logging**: Structured logging with Correlation IDs for debugging complex async flows.

## 🛠 System Requirements

- **Python**: 3.10 or higher
- **FFmpeg**: Essential for audio stream assembly and speed adjustments.
- **OS**: Linux (tested on Ubuntu/Debian, Fedora, Arch).

### Installing System Dependencies

```bash
# Ubuntu/Debian
sudo apt-get update && sudo apt-get install ffmpeg libxcb-cursor0

# Fedora
sudo dnf install ffmpeg libxcb-cursor

# Arch Linux
sudo pacman -S ffmpeg libxcb-cursor
```

## 📦 Installation

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd ReadAloud
   ```

2. **Set up virtual environment (recommended)**:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## 🖥 Usage

### Starting the Application
```bash
./start.sh
```

### Running Tests
To execute the comprehensive test suite (unit, integration, concurrency, and UI tests):
```bash
./run_tests.sh
```

### Configuration Parameters
1. **Project Name**: Unique identifier (used for the final filename).
2. **Input File**: Select any `.txt` file containing UTF-8 encoded text.
3. **Voice Settings**:
   - **Language**: English, Ukrainian, German, Russian.
   - **Gender**: Switch between available voice models.
   - **Speed**: Fine-tune the speech pace (1.0 is standard).
4. **Threads**: Set concurrency level (1-30). Higher values speed up large files but require stable internet.

## 🏗 Architecture & Design Principles

The project adheres to strict **Senior/Staff Engineer** standards:

- **SOLID Principles**: Each component (Services, Controller, GUI) has a single responsibility.
- **Service Layer Pattern**: Logic is decoupled from the UI into specialized services (`QueueService`, `GenerationService`, `AssemblyService`).
- **Domain-Driven Design**: Core logic uses immutable `ProjectConfig` and `AudioChunk` models.
- **Thread Safety**: Robust GUI interaction using `QThread` and signal/slot mechanisms to prevent race conditions.
- **Defensive Programming**: Comprehensive validation at system boundaries using Jakarta-style patterns and explicit null safety.

### Project Structure
```text
src/
├── application/       # Orchestration and Service layer
│   └── services/      # Business logic implementation
├── domain/            # Models, Exceptions, and core interfaces
├── infrastructure/    # File I/O, Logging, and technical utilities
├── gui/               # PyQt6 components and modern styling
└── main.py            # Application entry point
```

## 🛡 Stability & Performance

- **Graceful Shutdown**: Prevents zombie `ffmpeg` processes and ensures temporary file cleanup.
- **Exponential Backoff**: Automatic retry logic for network-dependent TTS calls.
- **Resource Optimization**: Parallel chunk processing with `ThreadPoolExecutor`.

## 📄 License

This project is developed for enterprise-level demonstration and personal use. All rights reserved.
