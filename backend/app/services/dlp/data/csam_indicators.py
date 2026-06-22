"""Content-moderation indicators for the CSAM detector — Sprint 4.

The indicator phrases are intentionally base64-ENCODED, not stored as
searchable plaintext, so this repository never contains a plaintext CSAM
term list. They are decoded once at import into a frozenset and used solely
to BLOCK such requests at the MEDIA_GEN DLP boundary.

The seed covers common, unambiguous indicator phrases (minor-age + sexual
co-occurrence). This is a defensive blocklist; it is intentionally short and
conservative, not exhaustive.
"""

from __future__ import annotations

import base64

# Each entry is base64(utf-8(lowercase phrase)). Categories (not spelled out
# here): age-term + sexual-modifier co-occurrences and well-known acronyms.
_ENCODED_INDICATORS = [
    "Y2hpbGQgcG9ybg==",
    "Y2hpbGQgcG9ybm9ncmFwaHk=",
    "Y2hpbGQgc2V4dWFsIGFidXNl",
    "Y3NhbQ==",
    "dW5kZXJhZ2Ugc2V4",
    "dW5kZXJhZ2UgbnVkZQ==",
    "bWlub3IgbnVkZQ==",
    "cHJldGVlbiBzZXg=",
    "Y2hpbGQgZXhwbG9pdGF0aW9u",
    "a2lkZGllIHBvcm4=",
    "amFpbGJhaXQ=",
    "Y2hpbGQgc2V4",
    "c2V4dWFsaXplZCBjaGlsZA==",
    "bnVkZSBjaGlsZA==",
    "c2V4dWFsIG1pbm9y",
]

# Decoded, lowercased indicator phrases. Transient in-process only.
INDICATORS: frozenset[str] = frozenset(
    base64.b64decode(s).decode("utf-8").lower() for s in _ENCODED_INDICATORS
)
