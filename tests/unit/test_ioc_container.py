import unittest
from src.infrastructure.ioc import Container
from src.infrastructure.network_manager import NetworkManager
from src.infrastructure.file_manager import FileManager
from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.domain.audio_generator import AudioGenerator
from src.domain.audio_assembler import AudioAssembler
from src.application.services.queue_service import QueueService
from src.application.services.generation_service import GenerationService
from src.application.services.assembly_service import AssemblyService
from src.application.app_controller import ApplicationController


class TestIoCContainer(unittest.TestCase):

    def setUp(self):
        self.container = Container()

    def test_lazy_singleton_instantiation(self):
        """Verifies IoC container lazily instantiates and caches instances."""
        nm1 = self.container.network_manager
        nm2 = self.container.network_manager
        self.assertIsInstance(nm1, NetworkManager)
        self.assertIs(nm1, nm2)

        qs1 = self.container.queue_service
        qs2 = self.container.queue_service
        self.assertIsInstance(qs1, QueueService)
        self.assertIs(qs1, qs2)

        fm = self.container.file_manager
        self.assertIsInstance(fm, FileManager)

        tp = self.container.text_processor
        self.assertIsInstance(tp, TextProcessor)

        tc = self.container.text_chunker
        self.assertIsInstance(tc, TextChunker)

        ag = self.container.audio_generator
        self.assertIsInstance(ag, AudioGenerator)

        aa = self.container.audio_assembler
        self.assertIsInstance(aa, AudioAssembler)

        gs = self.container.generation_service
        self.assertIsInstance(gs, GenerationService)

        as_service = self.container.assembly_service
        self.assertIsInstance(as_service, AssemblyService)


if __name__ == "__main__":
    unittest.main()
