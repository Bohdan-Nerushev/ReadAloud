"""Shared builders for task-lifecycle tests (real QueueService/PersistenceService/FileManager)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from src.application.app_controller import ApplicationController
from src.application.services.persistence_service import PersistenceService
from src.application.services.queue_service import QueueService
from src.domain.models import ProjectConfig, TtsBackend
from src.infrastructure.file_manager import FileManager


def make_config(tmp: Path, name: str = "Book", backend: TtsBackend = TtsBackend.EDGE_TTS) -> ProjectConfig:
    input_file = tmp / f"{name}.txt"
    input_file.write_text("hello", encoding="utf-8")
    return ProjectConfig(
        project_name=name,
        input_file_path=str(input_file),
        language="en",
        gender="male",
        thread_count=1,
        output_dir_path=str(tmp),
        tts_backend=backend,
    )


def make_controller(state_file: Path, generation_service=None, assembly_service=None):
    """Controller wired to real queue/persistence/file services and mocked synthesis services."""
    if generation_service is None:
        generation_service = MagicMock()
        generation_service.get_progress_info.return_value = (0, 0, "...", 0.0)
    assembly_service = assembly_service or MagicMock()
    queue = QueueService()
    persistence = PersistenceService(str(state_file))
    controller = ApplicationController(
        queue_service=queue,
        text_processor=MagicMock(),
        text_chunker=MagicMock(),
        file_manager=FileManager(),
        generation_service=generation_service,
        assembly_service=assembly_service,
        persistence_service=persistence,
    )
    return controller, queue, persistence, generation_service, assembly_service


def make_temp_dirs(base: Path, label: str):
    """Creates <label>_text / <label>_audio directories holding a chunk file each."""
    text_dir = base / f"{label}_text"
    audio_dir = base / f"{label}_audio"
    for d in (text_dir, audio_dir):
        d.mkdir(parents=True)
        (d / "1.dat").write_bytes(b"x")
    return str(text_dir), str(audio_dir)
