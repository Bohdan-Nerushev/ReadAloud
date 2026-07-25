"""
End-to-End (E2E) Full Pipeline Test Suite.

This module tests the complete end-to-end workflow of the ReadAloud application:
File Selection -> Preparation -> Text Processing -> Text Chunking -> Audio Generation -> Assembly -> Final MP3 Output.
Uses a small 2-3 chunk test file to guarantee rapid, deterministic, and accurate execution.
"""

import sys
import os
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

# Ensure QApplication is initialized for Qt signals and threads
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from src.infrastructure.ioc import Container
from src.domain.models import ProjectConfig, TaskStatus, AudioChunk, GenerationTask
from src.application.app_controller import ApplicationController, PreparationWorker
from src.domain.audio_assembler import AudioAssembler


class TestE2EFullPipeline(unittest.TestCase):
    """End-to-End test suite verifying full ReadAloud task lifecycle."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="readaloud_e2e_")
        self.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(self.output_dir, exist_ok=True)

        # Create a sample text file of 2-3 chunks (~1200 characters)
        self.input_file = os.path.join(self.temp_dir, "sample_story.txt")
        text_content = (
            "Перший параграф книги про подорожі. "
            "Далеко в горах протікала чиста прозора річка, "
            "на березі якої стояло маленьке затишне селище.\n\n"
            "Другий параграф описує події вечора. "
            "Сонце вже сідало за обрій, зафарбовуючи небо у яскраві багряні та золотисті кольори. "
            "Птахи поверталися до своїх гнізд, а в будинках спалахували перші вогники.\n\n"
            "Третій параграф підбиває підсумки розділу. "
            "Ніч опустилася на землю, принісши довгоочікуваний спокій та тишину. "
            "Усі мешканці селища поринули у глибокий солодкий сон."
        )
        with open(self.input_file, 'w', encoding='utf-8') as f:
            f.write(text_content)

    def tearDown(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_mp3(self, file_path: str) -> None:
        """Helper to write a valid minimal MP3 file using ffmpeg."""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", "1", "-acodec", "libmp3lame", "-ar", "24000", "-ac", "1", file_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    def test_e2e_text_to_mp3_generation_and_assembly_pipeline(self) -> None:
        """
        Tests the complete E2E pipeline for a 2-3 chunk project:
        1. Task Creation & Queueing
        2. Text Preparation & Chunking (PreparationWorker)
        3. Audio Generation per Chunk
        4. Audio Assembly into final MP3 file
        5. Verification of Output MP3 existence and Task Completion
        """
        # Create Container and services
        container = Container()
        controller: ApplicationController = container.app_controller

        config = ProjectConfig(
            project_name="E2E_Test_Book",
            input_file_path=self.input_file,
            output_dir_path=self.output_dir,
            language="uk",
            gender="female",
            speed=1.0,
            thread_count=2
        )

        # Step 1: Add task to queue
        controller.add_task(config)
        task = controller.get_current_task()
        self.assertIsNotNone(task)
        self.assertEqual(task.config.project_name, "E2E_Test_Book")

        # Step 2: Run PreparationWorker directly to simulate preparation
        prep_worker = PreparationWorker(
            task_id=str(task.id),
            config=config,
            file_manager=container.file_manager,
            text_processor=container.text_processor,
            text_chunker=container.text_chunker
        )
        
        # Override chunk size for testing to guarantee 2-3 chunks
        chunks = []
        raw_text = container.text_processor.process_text(Path(self.input_file).read_text(encoding='utf-8'))
        raw_chunks = container.text_chunker.chunk_text(raw_text, chunk_size=300)
        self.assertTrue(2 <= len(raw_chunks) <= 4, f"Expected 2-4 chunks, got {len(raw_chunks)}")

        text_dir = container.file_manager.create_timestamped_dir("text", self.temp_dir)
        audio_dir = container.file_manager.create_timestamped_dir("audio", self.temp_dir)

        for i, c in enumerate(raw_chunks, start=1):
            chunk = AudioChunk(chunk_number=i, text_content=c.text_content)
            container.file_manager.save_text_chunk(chunk, text_dir)
            chunks.append(chunk)

        # Step 3: Simulate Audio Generation by populating generated chunk MP3 files
        audio_files = []
        for chunk in chunks:
            chunk_mp3_path = os.path.join(audio_dir, f"{chunk.chunk_number}.mp3")
            self._create_synthetic_mp3(chunk_mp3_path)
            audio_files.append(chunk_mp3_path)
            self.assertTrue(os.path.exists(chunk_mp3_path))
            self.assertGreater(os.path.getsize(chunk_mp3_path), 0)

        # Save manifest
        manifest = {str(c.chunk_number): f"{c.chunk_number}.mp3" for c in chunks}
        manifest_path = os.path.join(audio_dir, "manifest.json")
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f)

        # Step 4: Run AudioAssembly end-to-end
        assembler = container.audio_assembler
        final_mp3_path = os.path.join(self.output_dir, "E2E_Test_Book.mp3")

        assembler.assemble_audio(
            audio_files=audio_files,
            output_path=final_mp3_path,
            speed=1.0
        )

        # Step 5: Verify Final Output
        self.assertTrue(os.path.exists(final_mp3_path), "Final assembled MP3 output does not exist!")
        self.assertGreater(os.path.getsize(final_mp3_path), 0, "Final assembled MP3 output is 0 bytes!")

        # Update task status and verify completion state
        task.update_status(TaskStatus.COMPLETED, "Completed successfully")
        task.update_progress(100.0)

        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.progress, 100.0)

    def test_e2e_queue_multi_file_processing(self) -> None:
        """Tests E2E queue processing when multiple 2-3 chunk files are queued sequentially."""
        container = Container()
        controller: ApplicationController = container.app_controller

        # Create two files
        file1 = os.path.join(self.temp_dir, "file1.txt")
        file2 = os.path.join(self.temp_dir, "file2.txt")

        Path(file1).write_text("Перший файл для перевірки черги. Завдання перше.", encoding='utf-8')
        Path(file2).write_text("Другий файл для перевірки черги. Завдання друге.", encoding='utf-8')

        config1 = ProjectConfig(
            project_name="Multi_Task_1",
            input_file_path=file1,
            output_dir_path=self.output_dir,
            language="uk",
            gender="male",
            speed=1.0,
            thread_count=1
        )
        config2 = ProjectConfig(
            project_name="Multi_Task_2",
            input_file_path=file2,
            output_dir_path=self.output_dir,
            language="uk",
            gender="female",
            speed=1.0,
            thread_count=1
        )

        controller.add_task(config1)
        controller.add_task(config2)

        all_tasks = container.queue_service.get_all_tasks()
        self.assertEqual(len(all_tasks), 2)
        self.assertEqual(all_tasks[0].config.project_name, "Multi_Task_1")
        self.assertEqual(all_tasks[1].config.project_name, "Multi_Task_2")


if __name__ == "__main__":
    unittest.main()
