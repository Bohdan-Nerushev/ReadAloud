"""Free-disk-space pre-flight check for a generation task."""

import os
import shutil
from pathlib import Path
from typing import Dict

from src.domain.exceptions import InsufficientDiskSpaceException

# Generous per-character audio size (one copy of the MP3 data). Speech is ~15 characters per
# second and Edge/Piper/XTTS MP3s are 32-128 kbit/s, i.e. roughly 250-1100 bytes per character.
BYTES_PER_CHAR = 1024
# Chunk files, batch parts and the final MP3 exist side by side for a while.
WORKSPACE_COPIES = 2
OUTPUT_COPIES = 1
SAFETY_MARGIN_BYTES = 64 * 1024 * 1024


def estimate_bytes(total_chars: int, copies: int) -> int:
    return total_chars * BYTES_PER_CHAR * copies


def _existing_ancestor(path: Path) -> Path:
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def ensure_free_space(total_chars: int, workspace_dir: str, output_dir: str) -> None:
    """
    Raises InsufficientDiskSpaceException when the workspace (chunks, parts) or the output
    folder (final MP3) probably cannot hold the audio. If both are on one device the needs add up.
    """
    workspace = _existing_ancestor(Path(workspace_dir).expanduser())
    output = _existing_ancestor(Path(output_dir).expanduser()) if output_dir else workspace

    needs: Dict[str, int] = {}
    paths: Dict[str, Path] = {}
    for path, copies in ((workspace, WORKSPACE_COPIES), (output, OUTPUT_COPIES)):
        device = str(os.stat(path).st_dev)
        needs[device] = needs.get(device, SAFETY_MARGIN_BYTES) + estimate_bytes(total_chars, copies)
        paths.setdefault(device, path)

    for device, required in needs.items():
        free = shutil.disk_usage(paths[device]).free
        if free < required:
            raise InsufficientDiskSpaceException(
                f"Not enough free disk space on '{paths[device]}': about "
                f"{required / 1024 ** 2:.0f} MB needed, {free / 1024 ** 2:.0f} MB free."
            )
