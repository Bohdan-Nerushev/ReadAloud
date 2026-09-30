"""Tunable numbers of the generation pipeline, named and documented in one place."""

# Text preparation
CHUNK_TARGET_CHARS = 1000            # soft size of one text chunk sent to a TTS engine

# Generation
GENERATION_BATCH_SIZE = 10           # chunks per batch submitted to the worker pool
DEFAULT_MAX_CHUNK_RETRIES = 10       # retries of one chunk (Edge, Piper)
MAX_RETRY_SWEEPS = 5                 # passes that re-submit chunks that are still missing
RETRY_SWEEP_DELAY_MS = 3000          # cooling pause before a retry sweep
MAX_PARALLEL_BATCHES = 10            # upper bound for worker threads / per-batch workers

# Assembly
ASSEMBLY_BATCH_SIZE = 40             # chunks merged into one intermediate part file

# GUI polling
PROGRESS_REFRESH_MS = 500
COMPLETION_POLL_MS = 1000

# Engines
EDGE_SYNTHESIS_TIMEOUT_SECONDS = 60  # one Edge chunk (websocket stream)
NON_SPEECH_SILENCE_SECONDS = 0.2     # XTTS stand-in for text without speakable characters
