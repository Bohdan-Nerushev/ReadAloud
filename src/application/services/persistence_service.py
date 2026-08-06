"""
Persistence service for saving and loading queue state.

Uses atomic write semantics (write-to-temp + rename) to ensure the state file
is never partially written even if the process is killed mid-operation.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.domain.models import ProjectConfig, GenerationTask, TaskStatus


class PersistenceService:
    """
    Responsible for serialising and deserialising the task queue to disk.

    Atomic writes:
        State is written to a sibling temp file first, then atomically renamed
        over the target path. On POSIX systems, ``os.replace`` is guaranteed
        atomic at the filesystem level, so the state file is always either
        the previous complete version or the new complete version — never
        a partial/corrupt file.
    """

    def __init__(self, file_path: str) -> None:
        self._file_path = Path(file_path)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_state(self, tasks: List[GenerationTask]) -> bool:
        """
        Atomically saves the current task list to disk in a thread-safe manner.
        """
        with self._lock:
            tmp_path: Optional[Path] = None
            try:
                self._file_path.parent.mkdir(parents=True, exist_ok=True)
                serialized = [self._task_to_dict(task) for task in tasks]
                payload = json.dumps(serialized, indent=4, ensure_ascii=False)

                tmp_path = self._file_path.with_name(f"{self._file_path.name}.{uuid.uuid4().hex}.tmp")
                tmp_path.write_text(payload, encoding="utf-8")

                os.replace(str(tmp_path), str(self._file_path))

                logging.debug(f"State saved atomically ({len(tasks)} task(s)).")
                return True

            except Exception as e:
                logging.error(f"Failed to save state: {e}", exc_info=True)
                if tmp_path and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                return False

    def load_state(self) -> List[GenerationTask]:
        """
        Loads the saved task list from disk.

        Returns an empty list (and removes the corrupted file) if the state
        file does not exist or cannot be parsed.
        """
        if not self._file_path.exists():
            return []

        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                self._handle_corrupted()
                return []
            return self._parse_tasks(data)
        except Exception as e:
            logging.warning(f"Failed to load state: {e}")
            self._handle_corrupted()
            return []

    # ------------------------------------------------------------------
    # Manifest checkpoint API
    # ------------------------------------------------------------------

    def save_manifest(self, directory: str, manifest_data: Dict[str, Any]) -> bool:
        """
        Atomically saves task manifest metadata (manifest.json) into the specified directory.
        """
        with self._lock:
            tmp_path: Optional[Path] = None
            try:
                target_dir = Path(directory)
                target_dir.mkdir(parents=True, exist_ok=True)
                manifest_path = target_dir / "manifest.json"
                tmp_path = target_dir / f"manifest.json.{uuid.uuid4().hex}.tmp"

                payload = json.dumps(manifest_data, indent=4, ensure_ascii=False)
                tmp_path.write_text(payload, encoding="utf-8")
                os.replace(str(tmp_path), str(manifest_path))
                logging.debug(f"Manifest saved atomically in {directory}.")
                return True
            except Exception as e:
                logging.error(f"Failed to save manifest in {directory}: {e}", exc_info=True)
                if tmp_path and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                return False

    def load_manifest(self, directory: str) -> Optional[Dict[str, Any]]:
        """
        Loads manifest metadata (manifest.json) from the specified directory.
        """
        try:
            manifest_path = Path(directory) / "manifest.json"
            if not manifest_path.exists():
                return None
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            return None
        except Exception as e:
            logging.warning(f"Failed to load manifest from {directory}: {e}")
            return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_tasks(self, data: List[Dict[str, Any]]) -> List[GenerationTask]:
        tasks = []
        skipped = 0
        for item in data:
            task = self._dict_to_task(item)
            if task:
                tasks.append(task)
            else:
                skipped += 1
        if skipped > 0:
            logging.info(f"Pruning {skipped} stale/invalid task(s) from state file.")
            self.save_state(tasks)
        return tasks

    def _handle_corrupted(self) -> None:
        """Removes a corrupt state file to prevent repeated failures on startup."""
        for path in (self._file_path, self._file_path.with_suffix(".tmp")):
            try:
                if path.exists():
                    path.unlink()
                    logging.warning(f"Removed corrupted state file: {path}")
            except Exception as e:
                logging.error(f"Failed to remove corrupted state file {path}: {e}", exc_info=True)

    def _task_to_dict(self, task: GenerationTask) -> Dict[str, Any]:
        return {
            "id": str(task.id),
            "status": task.status.value,
            "progress": task.progress,
            "message": task.message,
            "created_at": task.created_at.isoformat(),
            "text_dir": task.text_dir,
            "audio_dir": task.audio_dir,
            "config": {
                "project_name": task.config.project_name,
                "input_file_path": task.config.input_file_path,
                "language": task.config.language,
                "gender": task.config.gender,
                "thread_count": task.config.thread_count,
                "output_dir_path": task.config.output_dir_path,
                "speed": task.config.speed
            }
        }

    def _dict_to_task(self, data: Dict[str, Any]) -> Optional[GenerationTask]:
        try:
            cfg_data = data["config"]
            input_file = cfg_data.get("input_file_path", "")
            if not input_file or not Path(input_file).expanduser().exists():
                logging.warning(
                    f"Skipping task '{cfg_data.get('project_name')}' ({data.get('id')}): "
                    f"Input file no longer exists: {input_file}"
                )
                return None

            config = ProjectConfig(
                project_name=cfg_data["project_name"],
                input_file_path=input_file,
                language=cfg_data["language"],
                gender=cfg_data["gender"],
                thread_count=cfg_data["thread_count"],
                output_dir_path=cfg_data["output_dir_path"],
                speed=cfg_data["speed"]
            )
            task = GenerationTask(
                config=config,
                id=uuid.UUID(data["id"]),
                status=TaskStatus(data["status"]),
                progress=float(data["progress"]),
                message=data["message"],
                created_at=datetime.fromisoformat(data["created_at"])
            )
            task.text_dir = data.get("text_dir")
            task.audio_dir = data.get("audio_dir")
            return task
        except Exception as e:
            logging.error(f"Failed to parse task from dict: {e}", exc_info=True)
            return None
