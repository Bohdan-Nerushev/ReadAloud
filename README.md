# ReadAloud - Text to Speech Application

A professional desktop application for converting text files to MP3 audio using Google Text-to-Speech (gTTS).

## Features

- **Multi-language Support**: English, Ukrainian, German, and Russian
- **Multi-threaded Processing**: Generate audio files with 1-5 concurrent threads
- **Smart Text Processing**: Automatically handles special characters and text chunking
- **Progress Tracking**: Real-time progress display with estimated completion time
- **Pause/Resume**: Control generation with pause/resume functionality
- **Clean Architecture**: Built following OOP, SOLID, and DRY principles

## System Requirements

- **Python**: 3.10 or higher
- **Operating System**: Linux (Ubuntu/Debian recommended)
- **FFmpeg**: Required for audio processing

### Installing FFmpeg

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install ffmpeg

# Fedora
sudo dnf install ffmpeg

# Arch Linux
sudo pacman -S ffmpeg
```

## Installation

1. Clone or download the repository:
```bash
cd /home/bnerushev/PycharmProjects/ReadAloud
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Running the Application

```bash
python src/main.py
```

### Using the GUI

1. **Project Name**: Enter a name for your output MP3 file (without extension)
2. **Input File**: Click "Browse..." to select a .txt file
3. **Language**: Choose the speech synthesis language from the dropdown
4. **Threads**: Select the number of concurrent threads (1-5)
   - 1 thread: Slower but more stable
   - 5 threads: Faster but may encounter rate limiting
5. **Start Generation**: Click to begin audio generation
6. **Pause**: Temporarily halt generation (can be resumed)
7. **Stop**: Cancel generation and clean up temporary files

### Output

Generated MP3 files are saved to the `final/` directory in your project folder.

## Project Structure

```
ReadAloud/
├── src/
│   ├── domain/              # Core business logic
│   │   ├── models.py        # Domain models
│   │   ├── text_processor.py
│   │   ├── text_chunker.py
│   │   ├── audio_generator.py
│   │   └── audio_assembler.py
│   ├── infrastructure/      # Technical utilities
│   │   ├── file_manager.py
│   │   ├── progress_tracker.py
│   │   ├── thread_manager.py
│   │   └── retry_handler.py
│   ├── gui/                 # User interface
│   │   ├── main_window.py
│   │   ├── styles.py
│   │   └── widgets/
│   ├── application/         # Application coordination
│   │   └── app_controller.py
│   └── main.py              # Entry point
├── tests/                   # Unit tests
├── final/                   # Output directory
└── requirements.txt
```

## Technical Details

### Text Processing

The application automatically:
- Removes dangerous characters: `"`, `'`, `***`, `======`
- Replaces newlines with 4 spaces
- Splits text into ~180 character chunks (respecting word boundaries)

### Audio Generation

- Uses gTTS (Google Text-to-Speech) API
- Implements automatic retry logic with exponential backoff
- Supports concurrent generation with thread pooling
- Assembles chunks into final MP3 using pydub

### Supported Languages

| Language   | Code |
|------------|------|
| English    | en   |
| Ukrainian  | uk   |
| German     | de   |
| Russian    | ru   |

## Troubleshooting

### "No module named 'PyQt6'"
```bash
pip install PyQt6
```

### "ffmpeg: command not found"
Install FFmpeg using the instructions in System Requirements section.

### "Rate limit exceeded" or generation failures
- Reduce the number of threads
- Wait a few minutes and try again
- The application will automatically retry failed chunks

### Application won't start
Ensure Python 3.10+ is installed:
```bash
python --version
```

## Architecture

This application follows enterprise-level coding standards:

- **OOP Principles**: Encapsulation, abstraction, inheritance, polymorphism
- **SOLID Principles**: Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, Dependency Inversion
- **DRY Principle**: No code duplication
- **Clean Code**: 120-character line limit, explicit type hints, comprehensive validation

## License

This project is provided as-is for educational and personal use.

## Support

For issues or questions, please check:
1. This README file
2. The Troubleshooting section
3. Python and FFmpeg documentation
