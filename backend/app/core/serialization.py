"""Shared orjson ``default=`` callback.

Re-audit H16: ``orjson.dumps`` only knows a fixed set of native types
(str/int/float/bool/None/dict/list plus, when the matching ``OPT_*``
flag is set, a handful of stdlib types). Anything else raises
``TypeError`` *unless* a ``default=`` callback is supplied to coerce it.

Several routes return raw dicts that can contain ``Decimal`` (billing /
cost math), ``set`` (collected ids), ``datetime``/``date``, ``UUID``,
``bytes`` (DLP trace blobs), ``Enum``, or Pydantic v2 models. Without a
``default=`` those bodies blew up with a 500 at render time. This single
callback gives every orjson serializer in the codebase the same coercion
contract — keep it here (a leaf module with no app imports) so both
``app.core.responses`` and ``app.services.cache_service`` can import it
without an import cycle.

Re-audit M17 (NaN/Inf): orjson handles ``float`` entirely at the C/Rust
level and emits ``null`` for NaN/Inf *before* ``default=`` is ever
consulted — a ``default`` callback can NOT intercept them, and orjson
3.10.x exposes no option to raise on non-finite floats (verified: the
only OPT_* flags are append-newline / indent / naive-utc / non-str-keys
/ omit-microseconds / passthrough-* / serialize-* / sort-keys /
strict-integer / utc-z — none of them touch NaN). The real mitigation is
guarding cost/latency math upstream so a NaN never reaches the
serializer; that is out of scope for this module. This callback does NOT
and CANNOT change NaN/Inf -> null behavior.
"""

from __future__ import annotations

import datetime
import decimal
import enum
import uuid


def orjson_default(obj):  # noqa: ANN001 - orjson hands us an arbitrary object
    """Coerce non-native types into something orjson can serialize.

    Mirrors the stdlib-json behaviour the codebase relied on before the
    switch to orjson, plus Pydantic v2 model support. Raises ``TypeError``
    for genuinely unsupported types so the failure is explicit rather than
    silently dropped.

    Note: ``float('nan')`` / ``float('inf')`` never reach here — orjson
    serializes floats natively and emits ``null`` for non-finite values
    before consulting ``default=`` (see module docstring, M17).
    """
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    # datetime is a subclass of date, so this ``isinstance`` covers both;
    # ``.isoformat()`` exists on each and produces the right shape.
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, enum.Enum):
        return obj.value
    # Pydantic v2 models expose ``model_dump``; guard with hasattr so we
    # don't hard-depend on pydantic being importable here.
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    raise TypeError(
        f"Object of type {type(obj).__name__} is not JSON serializable"
    )
