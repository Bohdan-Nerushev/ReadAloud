# ReadAloud - High-Performance Text-to-Speech Application

A professional-grade desktop application built with Python and PyQt6 for converting complex text documents into high-quality MP3 audio. Powered by Microsoft Edge TTS for natural-sounding synthesis.

## 🚀 Key Features

- **Queue Management**: Add multiple tasks to a processing queue and manage them concurrently.
- **Next-Gen Synthesis**: Uses Microsoft Edge TTS for superior, human-like voice quality.
- **Advanced Audio Controls**:
  - **Speed Regulation**: Adjust playback speed from 1x to 2.0x.
  - **Gender Selection**: Choose between Male and Female voices for supported languages.
  - **Multi-threaded Generation**: High-speed processing with up to 40 concurrent threads.
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

- **Python**: 3.12 or higher
- **FFmpeg**: Essential for audio stream assembly and speed adjustments.
- **OS**: Linux (was tested on Ubuntu).

## 📦 Installation & Setup

You can install the application automatically in one command using the remote installation script (it installs dependencies, clones/updates the repository, sets up the virtual environment, and generates a desktop shortcut):

### One-line Installation

**Using wget:**
```bash
wget --no-check-certificate -O install.sh "https://git.mam.dev/bnerushev/readaloud/-/raw/master/install.sh?ref_type=heads"
bash install.sh
```

*Note: The installation directory will be created at `./ReadAloud` relative to the directory where the command was executed. The desktop shortcut will be generated on your Desktop (e.g., `~/Desktop` or `~/Schreibtisch`). You may need to right-click the shortcut on your desktop and select **"Allow Launching"** to trust and enable it.*

### Manual Installation (Alternative)

### Installing System Dependencies
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
   git clone https://git.mam.dev/bnerushev/readaloud.git
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

### Uninstallation

If you wish to uninstall ReadAloud, you can download and run the remote uninstallation script in one command:

**Using wget:**
```bash
wget --no-check-certificate -O uninstall.sh "https://git.mam.dev/bnerushev/readaloud/-/raw/master/uninstall.sh?ref_type=heads"
bash uninstall.sh
```

This will automatically clean up the program files, remove the desktop shortcut, and unregister the application from your system menu.

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
4. **Threads**: Set concurrency level (1-40). Higher values speed up large files but require stable internet.

## 📊 Ingestion & Monitoring (ELK Stack)

The application supports structured logging and real-time visualization using the ELK Stack (Elasticsearch, Logstash, Kibana).

### Starting the Logging Infrastructure
Ensure you have Docker and Docker Compose installed, then start the services from the project root:
```bash
docker compose up -d
```

### Accessing the Services
Once the containers are up, the services can be accessed at the following endpoints:

- **Elasticsearch API**: [http://localhost:9200](http://localhost:9200) (Check cluster health and raw indices)
- **Kibana Web Interface**: [http://localhost:5601](http://localhost:5601) (Visualize and search logs)
- **Logstash API**: [http://localhost:9600](http://localhost:9600) (Monitor pipeline performance)

### Analyzing Logs in Kibana
1. Open **Kibana** at [http://localhost:5601](http://localhost:5601).
2. Navigate to **Management** -> **Kibana** -> **Data Views** (or **Index Patterns**).
3. Create a new Data View with the index pattern `app-logs-*` and set `@timestamp` as the primary time field.
4. Go to the **Discover** tab to search and filter logs by structured fields like `correlation_id`, `loglevel`, `logger`, and `log_message`.

### Stopping the Logging Infrastructure
```bash
docker compose down 
```
