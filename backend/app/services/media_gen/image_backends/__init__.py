"""Image backends register here at import time (mirrors ``media_gen/__init__``).

Adding a real second image backend is: drop a module with an ``ImageBackend``
subclass, register it here, and add a catalogue row + an order entry (data). No
frozenset to edit — that is the FIX67 Part 1 success criterion.
"""

from app.services.media_gen.image_backends.base import (  # noqa: F401
    ImageBackend,
    ImageGenResult,
    get_image_backend,
    register_image_backend,
    registered_image_providers,
)
from app.services.media_gen.image_backends.mock_image import MockImageBackend
from app.services.media_gen.image_backends.openai_image import OpenAIImageBackend

register_image_backend(OpenAIImageBackend())
# Registered unconditionally, but gated at resolution time by ``media_allow_mock``
# (default OFF) and the admin order validator — see mock_image.py's Safety note.
register_image_backend(MockImageBackend())
