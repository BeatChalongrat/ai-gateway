"""Media-generation services.

Plumbing for async video / music / TTS generation: the adapter contract
(``base.MediaGenAdapter``), the in-process ``registry``, and the lifespan
job loop (``job_loop.run_generation_job_loop``).

Adapters auto-register here at import time. Sprint 2A ships the mock video
adapter (zero external calls); Sprint 2B adds Replicate.
"""

from app.services.media_gen.adapters.mock_video import MockVideoAdapter
from app.services.media_gen.registry import register_adapter

register_adapter(MockVideoAdapter)

