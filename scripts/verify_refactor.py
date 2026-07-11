import sys
from pathlib import Path

# Add project root to sys.path
project_root = str(Path(__file__).parent.parent)
sys.path.insert(0, project_root)

try:
    print("Importing modules...")
    from src.application.services.generation_service import GenerationService
    from src.application.services.assembly_service import AssemblyService
    from src.application.app_controller import ApplicationController
    from src.domain.audio_generator import AudioGenerator
    from src.domain.audio_assembler import AudioAssembler
    from src.infrastructure.thread_manager import ThreadManager
    from src.infrastructure.retry_handler import RetryHandler
    
    print("Instantiating AudioGenerator (Background Loop Check)...")
    audio_gen = AudioGenerator()
    print("AudioGenerator instantiated.")

    print("Instantiating Services...")
    gen_service = GenerationService(audio_gen, RetryHandler())
    asm_service = AssemblyService(AudioAssembler())
    print("Services instantiated.")
    
    print("Verification Successful!")

except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Runtime Error: {e}")
    sys.exit(1)
