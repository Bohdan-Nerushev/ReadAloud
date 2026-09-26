#!/usr/bin/env python3
"""
Generates reference speaker WAV files for XTTS-v2 using Edge TTS.

Creates mono 24kHz WAV reference samples for all supported languages (uk, en, de, ru)
and genders (male, female) in src/resource/xtts_speakers/.
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import edge_tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PROJECT_ROOT = Path(__file__).parent.parent
SPEAKERS_DIR = PROJECT_ROOT / "src" / "resource" / "xtts_speakers"

SPEAKER_VOICES = {
    "uk": {
        "male": ("uk-UA-OstapNeural", "Доброго дня, це тестовий зразок голосу для клонування."),
        "female": ("uk-UA-PolinaNeural", "Доброго дня, це тестовий зразок голосу для клонування."),
    },
    "en": {
        "male": ("en-US-GuyNeural", "Hello, this is a reference sample voice for XTTS cloning."),
        "female": ("en-US-JennyNeural", "Hello, this is a reference sample voice for XTTS cloning."),
    },
    "de": {
        "male": ("de-DE-KillianNeural", "Hallo, das ist eine Referenz-Sprachprobe für XTTS."),
        "female": ("de-DE-KatjaNeural", "Hallo, das ist eine Referenz-Sprachprobe für XTTS."),
    },
    "ru": {
        "male": ("ru-RU-DmitryNeural", "Здравствуйте, это эталонный образец голоса для клонирования."),
        "female": ("ru-RU-SvetlanaNeural", "Здравствуйте, это эталонный образец голоса для клонирования."),
    },
}


async def generate_sample(voice: str, text: str, output_wav: Path) -> None:
    """Generates an MP3 via Edge TTS and converts to 24kHz Mono WAV via ffmpeg."""
    output_wav.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
        tmp_mp3_path = tmp_mp3.name

    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(tmp_mp3_path)

        cmd = [
            "ffmpeg",
            "-y",
            "-i", tmp_mp3_path,
            "-ar", "24000",
            "-ac", "1",
            str(output_wav),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logging.info(f"Generated speaker WAV: {output_wav.relative_to(PROJECT_ROOT)}")
    finally:
        if os.path.exists(tmp_mp3_path):
            os.remove(tmp_mp3_path)


async def main() -> None:
    logging.info("Generating default reference speaker WAV files for XTTS-v2...")
    tasks = []
    for lang, genders in SPEAKER_VOICES.items():
        for gender, (voice, text) in genders.items():
            wav_path = SPEAKERS_DIR / lang / f"{gender}.wav"
            if not wav_path.exists() or wav_path.stat().st_size == 0:
                tasks.append(generate_sample(voice, text, wav_path))
            else:
                logging.info(f"Skipping existing file: {wav_path.relative_to(PROJECT_ROOT)}")

    if tasks:
        await asyncio.gather(*tasks)
        logging.info("All missing speaker reference files generated successfully.")
    else:
        logging.info("All speaker reference WAV files are already present.")


if __name__ == "__main__":
    asyncio.run(main())
