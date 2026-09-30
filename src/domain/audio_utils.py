"""Stateless audio helpers that do not depend on any TTS generator instance."""


def get_mp3_duration(file_path: str) -> float:
    """Returns the MP3 duration in seconds, or 0.0 if the file is missing or unreadable."""
    try:
        from mutagen.mp3 import MP3
        return float(MP3(str(file_path)).info.length)
    except Exception:
        return 0.0
