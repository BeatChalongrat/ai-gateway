"""Thai word boundaries, for laying out table cells that Word must never wrap.

The sandbox image already carries libthai (and its word dictionary) as part
of its text stack — the dictionary-based breaker GTK/Pango use. It is called
through ctypes: no new package, nothing added to the image. Where libthai is
missing (a developer machine), text breaks only at spaces.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools


@functools.lru_cache(maxsize=1)
def _breaker():
    for name in (ctypes.util.find_library("thai"), "libthai.so.0"):
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.th_brk_new.restype = ctypes.c_void_p
        lib.th_brk_new.argtypes = [ctypes.c_char_p]
        lib.th_brk_find_breaks.restype = ctypes.c_int
        lib.th_brk_find_breaks.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int), ctypes.c_size_t]
        brk = lib.th_brk_new(None)
        if brk:
            return lib, brk
    return None


def available() -> bool:
    return _breaker() is not None


def _tis(text: str) -> bytes:
    # TIS-620 is one byte per character, so libthai's positions are character indices;
    # a character it cannot hold (a symbol, a Latin accent) stands in as one '?'.
    return b"".join(ch.encode("tis-620", errors="replace") if ch != "\x00" else b"?" for ch in text)


def breaks(text: str) -> list[int]:
    """Indices (0 < i < len) where a line may break inside ``text`` by Thai word boundaries."""
    b = _breaker()
    if b is None or not text:
        return []
    lib, brk = b
    raw = _tis(text)
    pos = (ctypes.c_int * (len(raw) + 1))()
    n = lib.th_brk_find_breaks(brk, raw, pos, len(raw) + 1)
    return sorted({pos[i] for i in range(max(n, 0)) if 0 < pos[i] < len(text)})


def words(text: str) -> list[tuple[str, bool]]:
    """``text`` as its words, each with whether a space came before it."""
    out: list[tuple[str, bool]] = []
    for k, chunk in enumerate(text.split()):
        cuts = [0] + breaks(chunk) + [len(chunk)]
        for i in range(len(cuts) - 1):
            out.append((chunk[cuts[i]:cuts[i + 1]], k > 0 and i == 0))
    return out
