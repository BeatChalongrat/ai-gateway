"""Shared response classes.

``UTF8JSONResponse`` is the project's default response class. It
overrides Starlette's stock ``JSONResponse`` to call ``json.dumps``
with ``ensure_ascii=False`` so non-ASCII characters (Thai, CJK,
combining marks) ride through as their natural UTF-8 bytes instead
of being escaped to ``\\uXXXX``.

Why this matters here even though browsers tolerate ``\\uXXXX``:

* Operator-facing surfaces (logs, ``curl`` smoke tests, the admin
  UI's request-trace view) display the literal bytes, so escapes
  show up as noise that's hard to read at a glance.
* Round-tripping through the DLP trace columns and the
  ``request_logs`` audit table preserves exact bytes — useful when
  diff-ing input/output for the streaming-restore unit tests.
* Payload size shrinks ~3-6x on Thai responses (one UTF-8 byte
  vs. six escape characters per code point).

The exception handlers in ``app.middleware.error_handler`` import
from this module instead of starlette so error envelopes share the
same encoding contract.
"""

from __future__ import annotations

import orjson

from fastapi.responses import JSONResponse

from app.core.serialization import orjson_default


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"

    def render(self, content) -> bytes:
        # Phase-1 B9: orjson is a Rust serializer ~3-10x faster than the
        # stdlib (and it releases the GIL), which matters on large
        # completion bodies. It already emits compact UTF-8 with non-ASCII
        # unescaped (== the old ensure_ascii=False contract) and returns
        # bytes directly (no .encode). OPT_NON_STR_KEYS matches stdlib's
        # int-key coercion.
        #
        # Re-audit H16: ``default=orjson_default`` coerces Decimal / set /
        # datetime / UUID / bytes / Enum / Pydantic-v2 models so a route
        # returning a raw dict with one of those no longer 500s at render
        # time. See app.core.serialization for the full contract.
        #
        # Re-audit M17 (NaN/Inf): orjson serializes floats natively and
        # emits ``null`` for NaN/Inf *before* ``default=`` runs — the
        # callback can NOT intercept them, and orjson 3.10.x has no option
        # to raise on non-finite floats (verified against this build's
        # OPT_* set). The old stdlib ``allow_nan=False`` would have raised;
        # orjson silently yields null. The correct fix is to guard the
        # cost/latency math upstream so a NaN never reaches here (out of
        # scope for this response class).
        return orjson.dumps(
            content,
            option=orjson.OPT_NON_STR_KEYS,
            default=orjson_default,
        )
