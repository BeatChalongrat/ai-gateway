"""Media-generation services.

Plumbing for async video / music / TTS generation: the adapter contract
(``base.MediaGenAdapter``), the in-process ``registry``, and the lifespan
job loop (``job_loop.run_generation_job_loop``).

Adapters auto-register here at import time. Sprint 2A ships the mock video
adapter (zero external calls); Sprint 2B adds Replicate.
"""

import logging

from app.services.media_gen.adapters.mock_video import MockVideoAdapter
from app.services.media_gen.registry import register_adapter

_log = logging.getLogger(__name__)

# Mock always registers — zero dependencies, used by dev/CI/tests.
register_adapter(MockVideoAdapter)

# Replicate registers ONLY when a token is configured. Without it the
# adapter is absent and a replicate-provider job fails closed
# (no_adapter) rather than crashing the app at import. Registration stores
# the class; instantiation (which re-checks the token) happens per job.
from app.core.config import settings as _settings  # noqa: E402

if _settings.replicate_api_token:
    try:
        from app.services.media_gen.adapters.replicate_video import (  # noqa: E402
            ReplicateVideoAdapter,
        )

        register_adapter(ReplicateVideoAdapter)
        _log.info("Registered adapter: replicate")
    except Exception as _exc:  # noqa: BLE001
        _log.error("Failed to register Replicate adapter: %s", _exc)
else:
    _log.info("Replicate adapter NOT registered (REPLICATE_API_TOKEN absent)")

