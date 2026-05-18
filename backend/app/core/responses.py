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

import json

from fastapi.responses import JSONResponse


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"

    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")
