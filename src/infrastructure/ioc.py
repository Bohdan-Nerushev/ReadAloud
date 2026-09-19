"""
Dependency Injection Container.

This module provides a container for managing application dependencies,
centralizing object creation and wiring.
"""

from pathlib import Path
from typing import Optional

from src.infrastructure.file_manager import FileManager
from src.infrastructure.thread_manager import ThreadManager
from src.infrastructure.network_manager import NetworkManager
from src.infrastructure.docker_manager import DockerManager
from src.infrastructure.piper_model_manager import PiperModelManager
from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.domain.audio_generator import AudioGenerator
from src.domain.piper_audio_generator import PiperAudioGenerator
from src.domain.audio_assembler import AudioAssembler
from src.domain.models import TtsBackend
from src.domain.tts_generator_protocol import TtsGeneratorProtocol
from src.application.services.queue_service import QueueService
from src.application.services.generation_service import GenerationService
from src.application.services.assembly_service import AssemblyService
from src.application.services.persistence_service import PersistenceService
from src.application.services.piper_setup_service import PiperSetupService
from src.application.app_controller import ApplicationController



class Container:
    """
    IoC Container for the ReadAloud application.
    """
    
    def __init__(self, state_file_path: Optional[str] = None) -> None:
        self._state_file_path = state_file_path
        self._queue_service: Optional[QueueService] = None
        self._text_processor: Optional[TextProcessor] = None
        self._text_chunker: Optional[TextChunker] = None
        self._audio_generator: Optional[AudioGenerator] = None
        self._piper_audio_generator: Optional[PiperAudioGenerator] = None
        self._audio_assembler: Optional[AudioAssembler] = None
        self._file_manager: Optional[FileManager] = None
        self._network_manager: Optional[NetworkManager] = None
        self._docker_manager: Optional[DockerManager] = None
        self._piper_model_manager: Optional[PiperModelManager] = None
        
        self._generation_service: Optional[GenerationService] = None
        self._assembly_service: Optional[AssemblyService] = None
        self._persistence_service: Optional[PersistenceService] = None
        self._piper_setup_service: Optional[PiperSetupService] = None
        self._app_controller: Optional[ApplicationController] = None


    @property
    def network_manager(self) -> NetworkManager:
        if not self._network_manager:
            self._network_manager = NetworkManager()
        return self._network_manager

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
            self._audio_generator = AudioGenerator(
                network_manager=self.network_manager,
                max_concurrency=5
            )
        return self._audio_generator

    @property
    def piper_audio_generator(self) -> PiperAudioGenerator:
        """Lazy-initialised Piper generator. Created only when first requested."""
        if not self._piper_audio_generator:
            self._piper_audio_generator = PiperAudioGenerator()
        return self._piper_audio_generator

    def audio_generator_for_backend(self, backend: TtsBackend) -> TtsGeneratorProtocol:
        """
        Returns the appropriate TTS generator for the given backend.

        Piper generator is initialised lazily only when first used,
        so there is no overhead for users who never switch to Piper.
        """
        if backend == TtsBackend.PIPER:
            return self.piper_audio_generator
        return self.audio_generator

    @property
    def docker_manager(self) -> DockerManager:
        if not self._docker_manager:
            self._docker_manager = DockerManager()
        return self._docker_manager

    @property
    def piper_model_manager(self) -> PiperModelManager:
        if not self._piper_model_manager:
            self._piper_model_manager = PiperModelManager()
        return self._piper_model_manager

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
    def generation_service(self) -> GenerationService:
        if not self._generation_service:
            self._generation_service = GenerationService(
                audio_generator=self.audio_generator
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
    def persistence_service(self) -> PersistenceService:
        if not self._persistence_service:
            if self._state_file_path:
                state_file = Path(self._state_file_path)
            else:
                project_root = Path(__file__).parent.parent.parent
                state_file = project_root / ".data" / "state.json"
            self._persistence_service = PersistenceService(str(state_file.absolute()))
        return self._persistence_service

    @property
    def piper_setup_service(self) -> PiperSetupService:
        if not self._piper_setup_service:
            self._piper_setup_service = PiperSetupService(
                docker_manager=self.docker_manager,
                model_manager=self.piper_model_manager,
            )
        return self._piper_setup_service

    @property
    def app_controller(self) -> ApplicationController:
        if not self._app_controller:
            self._app_controller = ApplicationController(
                queue_service=self.queue_service,
                text_processor=self.text_processor,
                text_chunker=self.text_chunker,
                file_manager=self.file_manager,
                generation_service=self.generation_service,
                assembly_service=self.assembly_service,
                persistence_service=self.persistence_service,
                audio_generator_resolver=self.audio_generator_for_backend,
                piper_setup_service=self.piper_setup_service,
            )
        return self._app_controller

