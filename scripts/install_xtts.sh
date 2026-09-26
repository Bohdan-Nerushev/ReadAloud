#!/usr/bin/env bash
# =============================================================================
# install_xtts.sh
#
# Installs Coqui XTTS-v2 dependencies into the current Python virtual environment.
# Requires:
#   - Active venv (source venv/bin/activate)
#   - NVIDIA GPU with CUDA 12.x drivers
#   - Internet connection (~4-6 GB download)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== ReadAloud: XTTS-v2 dependency installer ==="
echo "Project root: $PROJECT_ROOT"

# Verify we are inside a virtual environment.
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    echo "ERROR: No active virtual environment detected."
    echo "Run: source venv/bin/activate"
    exit 1
fi

echo ""
echo "Step 1/3: Installing PyTorch with CUDA 12.1 support..."
pip install torch torchaudio \
    --index-url https://download.pytorch.org/whl/cu121

echo ""
echo "Step 2/3: Installing Coqui TTS and audio dependencies..."
pip install "coqui-tts>=0.24.0" "transformers<4.45.0" "soundfile>=0.12.1"

echo ""
echo "Step 3/3: Verifying installation..."
python -c "import torch; print(f'  PyTorch: {torch.__version__}')"
python -c "import torch; print(f'  CUDA available: {torch.cuda.is_available()}')"
python -c "import TTS; print(f'  TTS package: OK')"
python -c "import soundfile; print(f'  soundfile: OK')"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Launch ReadAloud and select 'XTTS-v2 (Local GPU)' in TTS Engine."
echo "  2. Click 'Download Model' to fetch XTTS-v2 weights (~1.8 GB)."
echo "  3. Place speaker reference WAV files in src/resource/xtts_speakers/ (see README)."
