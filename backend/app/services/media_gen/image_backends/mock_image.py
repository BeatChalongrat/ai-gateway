"""Mock image backend (FIX67 Part 3) — TEST-ONLY, zero cost, no external call.

Mirrors ``media_gen/adapters/mock_video.py``: a second destination that always
produces, so the full fallback path (primary fails → second tier → artifact →
attribution) can be proven from the day this ships — no real second key, no
credits. It returns a synthesised solid-colour PNG (pure stdlib, no bundled
asset) so a viewer can SEE it is a placeholder.

Safety (Part 3.3): this backend is registered unconditionally, but the
destination resolver never treats ``provider="mock"`` as usable unless
``media_allow_mock`` is explicitly on (default OFF), and the admin order
validator refuses to store a mock destination while that flag is off. A test
goes red if a mock destination reaches a production order. A mock artifact handed
to a real user unaware is worse than no artifact (the ``mock-chat-1`` incident).
"""

from __future__ import annotations

import base64
import struct
import zlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.media_gen.image_backends.base import ImageBackend, ImageGenResult


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A valid 8-bit RGB PNG of a solid colour, built with the stdlib only."""

    def _chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # colour type 2 = RGB
    row = b"\x00" + bytes(rgb) * width  # filter byte 0 + pixels
    raw = row * height
    idat = zlib.compress(raw, 9)
    return signature + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


# A distinctive slate-blue 512×512 placeholder, computed once.
_SAMPLE_B64 = base64.b64encode(_solid_png(512, 512, (73, 92, 122))).decode("ascii")


class MockImageBackend(ImageBackend):
    provider_name = "mock"

    async def generate(
        self,
        session: AsyncSession,
        *,
        model: str,
        prompt: str,
        n: int = 1,
        size: str = "1024x1024",
        quality: str = "auto",
        style: str = "vivid",
        response_format: str = "b64_json",
        request_id: str | None = None,
    ) -> ImageGenResult:
        return ImageGenResult(
            provider="mock",
            model=model or "mock-image-1",
            data=[{"b64_json": _SAMPLE_B64, "revised_prompt": None}],
            cost_usd=0.0,
            raw={"mock": True},
        )
