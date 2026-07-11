import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.domain.models import ProjectConfig, GenerationTask, TaskStatus


class PersistenceService:
    def __init__(self, file_path: str) -> None:
        self._file_path = file_path

    def save_state(self, tasks: List[GenerationTask]) -> bool:
        try:
            parent_dir = Path(self._file_path).parent
            parent_dir.mkdir(parents=True, exist_ok=True)

            serialized = [self._task_to_dict(task) for task in tasks]
            with open(self._file_path, "w", encoding="utf-8") as f:
                json.dump(serialized, f, indent=4)
            return True
        except Exception as e:
            logging.error(f"Failed to save state: {e}", exc_info=True)
            return False

    def load_state(self) -> List[GenerationTask]:
        if not os.path.exists(self._file_path):
            return []

        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if not isinstance(data, list):
                self._handle_corrupted()
                return []

            return self._parse_tasks(data)
        except Exception as e:
            logging.error(f"Failed to load state: {e}", exc_info=True)
            self._handle_corrupted()
            return []

    def _parse_tasks(self, data: List[Dict[str, Any]]) -> List[GenerationTask]:
        tasks = []
        for item in data:
            task = self._dict_to_task(item)
            if task:
                tasks.append(task)
        return tasks

    def _handle_corrupted(self) -> None:
        try:
            if os.path.exists(self._file_path):
                os.unlink(self._file_path)
                logging.warning(f"Removed corrupted state file: {self._file_path}")
        except Exception as e:
            logging.error(f"Failed to remove corrupted state file: {e}", exc_info=True)

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
            config = ProjectConfig(
                project_name=cfg_data["project_name"],
                input_file_path=cfg_data["input_file_path"],
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
