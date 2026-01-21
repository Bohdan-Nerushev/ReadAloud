"""
Dependency Injection Container.

This module provides a container for managing application dependencies,
centralizing object creation and wiring.
"""

from typing import Optional

from src.infrastructure.file_manager import FileManager
from src.infrastructure.retry_handler import RetryHandler
from src.infrastructure.thread_manager import ThreadManager
from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.domain.audio_generator import AudioGenerator
from src.domain.audio_assembler import AudioAssembler
from src.application.services.queue_service import QueueService
from src.application.services.generation_service import GenerationService
from src.application.services.assembly_service import AssemblyService
from src.application.app_controller import ApplicationController


class Container:
    """
    IoC Container for the ReadAloud application.
    """
    
    def __init__(self) -> None:
        self._queue_service: Optional[QueueService] = None
        self._text_processor: Optional[TextProcessor] = None
        self._text_chunker: Optional[TextChunker] = None
        self._audio_generator: Optional[AudioGenerator] = None
        self._audio_assembler: Optional[AudioAssembler] = None
        self._file_manager: Optional[FileManager] = None
        self._retry_handler: Optional[RetryHandler] = None
        
        self._generation_service: Optional[GenerationService] = None
        self._assembly_service: Optional[AssemblyService] = None
        self._app_controller: Optional[ApplicationController] = None

    @property
    def queue_service(self) -> QueueService:
        if not self._queue_service:
            self._queue_service = QueueService()
        return self._queue_service

    @property
    def text_processor(self) -> TextProcessor:
        if not self._text_processor:
            self._text_processor = TextProcessor()
        return self._text_processor

    @property
    def text_chunker(self) -> TextChunker:
        if not self._text_chunker:
            self._text_chunker = TextChunker()
        return self._text_chunker

    @property
    def audio_generator(self) -> AudioGenerator:
        if not self._audio_generator:
            self._audio_generator = AudioGenerator()
        return self._audio_generator

    @property
    def audio_assembler(self) -> AudioAssembler:
        if not self._audio_assembler:
            self._audio_assembler = AudioAssembler()
        return self._audio_assembler

    @property
    def file_manager(self) -> FileManager:
        if not self._file_manager:
            self._file_manager = FileManager()
        return self._file_manager

    @property
    def retry_handler(self) -> RetryHandler:
        if not self._retry_handler:
            self._retry_handler = RetryHandler()
        return self._retry_handler

    @property
    def generation_service(self) -> GenerationService:
        if not self._generation_service:
            self._generation_service = GenerationService(
                audio_generator=self.audio_generator,
                retry_handler=self.retry_handler
            )
        return self._generation_service

    @property
    def assembly_service(self) -> AssemblyService:
        if not self._assembly_service:
            self._assembly_service = AssemblyService(
                audio_assembler=self.audio_assembler
            )
        return self._assembly_service

    @property
    def app_controller(self) -> ApplicationController:
        if not self._app_controller:
            self._app_controller = ApplicationController(
                queue_service=self.queue_service,
                text_processor=self.text_processor,
                text_chunker=self.text_chunker,
                file_manager=self.file_manager,
                generation_service=self.generation_service,
                assembly_service=self.assembly_service
            )
        return self._app_controller
