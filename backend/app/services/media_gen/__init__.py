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

# Replicate registers UNCONDITIONALLY (Sprint 2B.5). The key is resolved
# per-request from the DB (provider_keys → legacy providers → env), so a key
# added through the admin /providers UI activates the adapter with no restart.
# A job with no resolvable key fails closed at submit time (ProviderKeyMissing)
# rather than the adapter being absent. Registration stores the class;
# instantiation (cls()) happens per job — the registry contract is unchanged.
try:
    from app.services.media_gen.adapters.replicate_video import (  # noqa: E402
        ReplicateVideoAdapter,
    )

    register_adapter(ReplicateVideoAdapter)
    _log.info("Replicate adapter registered (key resolved per-request)")
except Exception as _exc:  # noqa: BLE001
    _log.error("Failed to register Replicate adapter: %s", _exc)

# Replicate MusicGen (Sprint 4) — same provider key, distinct job_type "music".
# Registered unconditionally; per-request key resolution + fail-closed apply.
try:
    from app.services.media_gen.adapters.replicate_music import (  # noqa: E402
        ReplicateMusicAdapter,
    )

    register_adapter(ReplicateMusicAdapter)
    _log.info("Replicate MusicGen adapter registered (key resolved per-request)")
except Exception as _exc:  # noqa: BLE001
    _log.error("Failed to register Replicate MusicGen adapter: %s", _exc)

# OpenAI TTS (Sprint 4) — reuses the OpenAI key; synchronous fast-path.
try:
    from app.services.media_gen.adapters.openai_tts import (  # noqa: E402
        OpenAITTSAdapter,
    )

    register_adapter(OpenAITTSAdapter)
    _log.info("OpenAI TTS adapter registered (key resolved per-request)")
except Exception as _exc:  # noqa: BLE001
    _log.error("Failed to register OpenAI TTS adapter: %s", _exc)

