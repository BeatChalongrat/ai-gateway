"""Streaming credential head-hold guard (Ecosystem Fix Phase 11, Part D).

Why this exists
---------------
The incremental SSE emitters in ``public_chat.py`` scan each provider
chunk against a rolling window (retained masked tail + new chunk) and
emit everything except the last ``MIN_TAIL_CHARS`` (120) characters.
That protects any sensitive pattern whose FULL match fits inside the
retained tail — but a credential-class pattern LONGER than the tail
(PEM private-key material, a long ``postgres://user:pass@host/db`` URL,
a multi-KB JWT, a long ``password=...`` value) can have its leading
fragment emitted raw before the detector ever sees the completed match:
the leading fragment alone is not a detector match, so it streams out,
and by the time the pattern completes the head is already on the wire.
The credential/secret class must never egress raw, even partially.

What it does
------------
A :class:`CredentialStreamGuard` sits on the RAW side of the streaming
DLP pipeline (before the per-window scan). It watches for the small,
fixed set of *credential head signatures* (``-----BEGIN``, DB URI
schemes, ``sk-`` / ``eyJ`` / ``AKIA`` / ``AIza`` / GitHub / Slack token
prefixes, ``Bearer`` and password/secret assignment labels). When a
head signature appears whose potential match could still be growing
past the end of the buffered text, the guard HOLDS everything from the
signature onward until the candidate is either

* **complete** — the maximal possible extent is fully buffered (for
  non-PEM candidates: the surrounding whitespace-free run, or the
  closing quote of a quoted assignment value, has terminated). The
  whole candidate is then released as one unit, so the downstream DLP
  window scan sees the COMPLETE pattern and the normal policy decision
  (mask/redact/block/allow) applies to the whole value at once; or
* **ruled out** — e.g. ``-----BEGIN CERTIFICATE-----`` is not a
  private-key header, so the text is released unchanged; or
* **the stream ends** — ``flush()`` force-resolves whatever is held so
  the final tail flush can scan it before emitting.

PEM private keys are special: the ``private_key`` detector matches only
the ``-----BEGIN ... PRIVATE KEY-----`` header line (the base64 body is
not a detector target), so masking alone would still emit the key
material. Once a private-key header completes, the guard holds until
the matching ``-----END ... PRIVATE KEY-----`` footer (or stream end)
and then releases ONLY the header — the body and footer are dropped.
The header still flows through the normal outbound scan, so the policy
action, findings accounting and audit trail for the key are unchanged;
the key material itself never reaches the wire. A stream that ends
mid-PEM holds until the end and drops the partial body — acceptable by
design: an unterminated key block is still key material.

Latency contract
----------------
* Trigger-free prose is completely unaffected: text is only retained
  across a chunk boundary when its trailing characters are a partial
  head signature (a prefix of ``-----BEGIN``, of a DB URI scheme, of a
  token prefix or of a secret label — max ``_HEAD_RETENTION`` = 32
  chars), so a signature split across two provider chunks reunites on
  the next feed. Ordinary sentences never end in such a prefix, so
  they release byte-identically to the pre-guard behavior.
* Prose containing a trigger-shaped word (``password``, ``Bearer``,
  ``pwd``, a word starting ``sk-`` ...) is held only until the next
  whitespace — one word, typically one chunk.
* Genuine credentials are held until complete; unbounded candidates
  (an unterminated PEM block, an unterminated quoted secret) are held
  until stream end. That is the point: text that may be the head of a
  credential must not be emitted until it is proven complete or benign.

Identity-class content (names, emails, phones ...) has NO trigger here
by design — its latency behavior is entirely unchanged.

State is per-stream and per-channel: instantiate one guard per SSE
channel (content and reasoning must not share a buffer).
"""

from __future__ import annotations

import re

__all__ = ["CredentialStreamGuard"]


# Reunite a head signature split across two provider chunks: the guard
# retains a trailing suffix ONLY when it is a partial head signature
# (see ``_suspicious_suffix_len``). Cap on how far back that check
# looks — must be >= the longest trigger literal below
# (``aws_secret_access_key`` + slack).
_HEAD_RETENTION = 32

# PEM private-key block markers — mirror
# ``app.services.dlp.detectors.private_key._PEM_HEADER_RE`` exactly so
# the guard's hold window and the detector's match stay in lockstep.
_PEM_TRIGGER = "-----BEGIN"
_PEM_HEADER_RE = re.compile(
    r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
)
_PEM_FOOTER_RE = re.compile(
    r"-----END\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
)
# A real private-key header is <= 36 chars; once this many characters
# follow ``-----BEGIN`` without completing the header regex, the block
# is something else (certificate, CSR, prose art) and is released.
_PEM_HEADER_MAX = 64

# Head signatures of the credential/secret detector patterns
# (api_key_secret / bearer_token / db_connection_string /
# password_field). Three families:
#
#   pem    — ``-----BEGIN`` (stateful BEGIN/END hold, see above)
#   simple — the match extent is a single whitespace-free run starting
#            at the signature (DB URI schemes, issued-token prefixes)
#   gap    — the pattern allows whitespace between a label and its
#            value (``Bearer <tok>``, ``password = <val>``); the hold
#            ends when the VALUE's run (or quoted span) terminates
_TRIGGER_RE = re.compile(
    r"(?P<pem>-----BEGIN)"
    r"|(?P<simple>"
    r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql)://"
    r"|\bsk-[A-Za-z0-9_\-]"
    r"|\beyJ[A-Za-z0-9_\-]"
    r"|\bAIza"
    r"|\bAKIA"
    r"|\b(?:ghp|gho|ghs)_"
    r"|\bgithub_pat_"
    r"|\bxox[bp]-"
    r")"
    r"|(?P<gap>"
    r"\b(?i:Bearer"
    r"|password|passwd|pwd"
    r"|api[_\-]?key|api[_\-]?secret"
    r"|secret(?:[_\-]?key)?"
    r"|access[_\-]?token|auth[_\-]?token"
    r"|aws[_\-]?secret[_\-]?access[_\-]?key"
    r"|db[_\-]?password"
    r")\s*[:=]?\s*"
    r")"
)

_WS_RE = re.compile(r"\s")


def _build_trigger_prefixes() -> frozenset[str]:
    """Every proper prefix (plus the full literal) of every trigger head
    signature, lowercased.

    Used by :func:`_suspicious_suffix_len` to decide whether the tail of
    otherwise-releasable text could be the beginning of a signature that
    completes in the NEXT provider chunk. Full literals are included
    because some triggers require a following character to match at all
    (``sk-`` needs one body char, ``eyJ`` likewise), so a buffer ending
    exactly on the literal must still be retained.
    """
    from itertools import product

    literals: set[str] = {
        "-----BEGIN",
        "postgresql://", "postgres://", "mysql://",
        "mongodb+srv://", "mongodb://", "redis://", "mssql://",
        "sk-", "eyJ", "AIza", "AKIA",
        "ghp_", "gho_", "ghs_", "github_pat_",
        "xoxb-", "xoxp-",
        "Bearer", "password", "passwd", "pwd",
    }
    # Multi-part labels where each joint may be "", "_" or "-".
    for parts in (
        ("api", "key"),
        ("api", "secret"),
        ("secret", "key"),
        ("access", "token"),
        ("auth", "token"),
        ("db", "password"),
        ("aws", "secret", "access", "key"),
    ):
        for seps in product(("", "_", "-"), repeat=len(parts) - 1):
            word = parts[0]
            for sep, part in zip(seps, parts[1:]):
                word += sep + part
            literals.add(word)
    literals.add("secret")

    prefixes: set[str] = set()
    for lit in literals:
        low = lit.lower()
        for i in range(1, len(low) + 1):
            prefixes.add(low[:i])
    return frozenset(prefixes)


_TRIGGER_PREFIXES = _build_trigger_prefixes()


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _suspicious_suffix_len(buf: str, pos: int) -> int:
    """Length of the longest suffix of ``buf`` (not reaching back before
    ``pos``) that could be a partial trigger head signature.

    0 for ordinary prose — a sentence ending in ``...the reply.`` shares
    no prefix with any signature, so trigger-free text streams with NO
    added latency. Word-start signatures additionally require a word
    boundary before the suffix (mirroring the ``\\b`` in the trigger
    regex), so ``...this`` is not retained just because ``s`` prefixes
    ``sk-``/``secret``.
    """
    n = len(buf)
    max_k = min(_HEAD_RETENTION, n - pos)
    for k in range(max_k, 0, -1):
        start = n - k
        cand = buf[start:].lower()
        if cand not in _TRIGGER_PREFIXES:
            continue
        first = buf[start]
        if _is_word_char(first) and start > 0 and _is_word_char(buf[start - 1]):
            # ``\b``-anchored signature can't start mid-word.
            continue
        return k
    return 0


def _resolve_pem(buf: str, p: int, at_eof: bool) -> tuple[str, int] | None:
    """Resolve a ``-----BEGIN`` trigger at ``p``.

    Returns ``(text_to_release, resume_pos)`` when resolved, or ``None``
    to hold from ``p``. For a completed (or stream-terminated) private
    key block, the released text is the HEADER ONLY — body and footer
    are dropped so key material never reaches the downstream emit path.
    """
    header = _PEM_HEADER_RE.match(buf, p)
    if header is None:
        if not at_eof and len(buf) - p < _PEM_HEADER_MAX:
            # The header may still be forming across chunk boundaries.
            return None
        # Ruled out (or stream over): not a private-key header. Release
        # the literal ``-----BEGIN`` and keep scanning after it.
        return buf[p : p + len(_PEM_TRIGGER)], p + len(_PEM_TRIGGER)
    footer = _PEM_FOOTER_RE.search(buf, header.end())
    if footer is not None:
        # Complete block: release the header (normal detector/policy
        # path), drop body + footer.
        return header.group(), footer.end()
    if at_eof:
        # Stream ended mid-key: release the header, drop the partial
        # body — an unterminated BEGIN block is still key material.
        return header.group(), len(buf)
    return None


def _resolve_simple(buf: str, p: int, at_eof: bool) -> tuple[str, int] | None:
    """Resolve a whitespace-free-run trigger (URI scheme / token prefix).

    The candidate extent is the maximal non-whitespace run starting at
    the signature; every matching detector pattern cannot cross
    whitespace, so once the run terminates inside the buffer the full
    possible match is buffered and safe to release for scanning.
    """
    ws = _WS_RE.search(buf, p)
    if ws is not None:
        return buf[p : ws.start()], ws.start()
    if at_eof:
        return buf[p:], len(buf)
    return None


def _resolve_gap(
    buf: str, p: int, trigger_end: int, at_eof: bool
) -> tuple[str, int] | None:
    """Resolve a label+separator trigger (``Bearer ...``, ``password=...``).

    ``trigger_end`` points just past the label/separator/whitespace; the
    value starts there. Quoted values resolve at the closing quote
    (matching ``password_field``'s quoted alternatives); bare values
    resolve at the next whitespace.
    """
    if trigger_end >= len(buf):
        # Value hasn't started yet.
        return (buf[p:], len(buf)) if at_eof else None
    quote = buf[trigger_end]
    if quote in ('"', "'"):
        close = buf.find(quote, trigger_end + 1)
        if close != -1:
            return buf[p : close + 1], close + 1
        return (buf[p:], len(buf)) if at_eof else None
    ws = _WS_RE.search(buf, trigger_end)
    if ws is not None:
        return buf[p : ws.start()], ws.start()
    if at_eof:
        return buf[p:], len(buf)
    return None


def _split_releasable(buf: str, at_eof: bool) -> tuple[str, str]:
    """Split ``buf`` into (releasable_text, held_text).

    ``releasable_text`` is safe to feed to the downstream window scan:
    it contains no credential head whose match could still be growing.
    For completed PEM blocks the released text is TRANSFORMED (header
    kept, key material dropped), so it is not necessarily a prefix of
    ``buf``. With ``at_eof=True`` nothing is held back.
    """
    out: list[str] = []
    pos = 0
    n = len(buf)
    while pos < n:
        m = _TRIGGER_RE.search(buf, pos)
        if m is None:
            break
        p = m.start()
        out.append(buf[pos:p])
        if m.lastgroup == "pem":
            res = _resolve_pem(buf, p, at_eof)
        elif m.lastgroup == "simple":
            res = _resolve_simple(buf, p, at_eof)
        else:
            res = _resolve_gap(buf, p, m.end(), at_eof)
        if res is None:
            # Unresolved head — hold everything from the signature on.
            return "".join(out), buf[p:]
        released, pos = res
        out.append(released)
    if at_eof:
        out.append(buf[pos:])
        return "".join(out), ""
    # Retain the trailing suffix ONLY if it could be a partial head
    # signature that completes in the next provider chunk; ordinary
    # prose releases in full (zero added latency).
    keep = _suspicious_suffix_len(buf, pos)
    keep_from = n - keep
    out.append(buf[pos:keep_from])
    return "".join(out), buf[keep_from:]


class CredentialStreamGuard:
    """Per-stream, per-channel raw-side hold buffer (see module doc).

    Usage::

        guard = CredentialStreamGuard(enabled=outbound_scan_active)
        ...
        released = guard.feed(chunk_delta)   # may be "" while holding
        if released:
            window = tail_masked + released  # existing scan/emit cycle
        ...
        remainder = guard.flush()            # at stream end, before the
                                             # final tail flush + scan

    When ``enabled`` is False (outbound DLP scanning off for this
    request) the guard is a byte-exact passthrough.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._held = ""

    @property
    def holding(self) -> bool:
        return bool(self._held)

    def feed(self, delta: str) -> str:
        if not self._enabled:
            return delta
        if not delta and not self._held:
            return ""
        buf = self._held + delta
        released, self._held = _split_releasable(buf, at_eof=False)
        return released

    def flush(self) -> str:
        """Force-resolve everything still held at stream end.

        The caller MUST route the returned text through the outbound
        DLP scan before emitting (it may contain a complete credential
        that was still growing when the stream ended). Unterminated PEM
        bodies are dropped here, never returned.
        """
        if not self._enabled or not self._held:
            return ""
        buf, self._held = self._held, ""
        released, _rest = _split_releasable(buf, at_eof=True)
        return released
