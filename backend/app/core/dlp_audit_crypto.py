"""AES-256-GCM encrypt/decrypt for the Safe Trace audit channel.

Encrypts the user's raw prompt (stage 1 of the 4-stage trace) before
the orchestrator masks it, so an admin viewing
``/admin/requests/{id}/trace`` can see what the user actually sent
without persisting raw PII in any plaintext column.

Wire format
-----------
The returned token is base64-urlsafe of::

    nonce (12 bytes) || ciphertext || tag (16 bytes)

The trailing tag is appended automatically by
``AESGCM.encrypt`` — we never split it out manually. A fresh
12-byte nonce is generated for every call (96-bit GCM nonce, safe
to randomize per NIST SP 800-38D).

Key resolution
--------------
The encryption key is 32 bytes, derived as follows:

  1. If ``settings.dlp_audit_key`` is non-empty, the value is parsed
     as hex (preferred — 64 hex chars → 32 bytes). If hex parsing
     fails, the UTF-8 bytes of the string are HKDF-expanded to 32
     bytes so any non-empty value still produces a stable key.
  2. If empty, the dev fallback HKDF-expands
     ``settings.master_encryption_key`` with the literal info
     string ``b"dlp-audit-key-v1"``. This gives a stable key on a
     dev box without any extra config; production enforces a
     dedicated key via :meth:`Settings.validate_secrets`.

A module-level cache holds the derived key so subsequent calls
skip the KDF. Changing the env var requires a process restart.
"""

from __future__ import annotations

import base64
import os
import threading
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes


_NONCE_BYTES = 12
_KEY_BYTES = 32
_HKDF_INFO = b"dlp-audit-key-v1"

_lock = threading.Lock()
_cached_key: bytes | None = None


class DlpAuditCryptoError(RuntimeError):
    """Raised when encrypt / decrypt fails for any reason.

    Callers in the orchestrator swallow this for the encrypt path
    (it must never break a request); the /trace endpoint surfaces
    it as a 500 with a generic message so the failure is debuggable
    without leaking key material.
    """


def _derive_key() -> bytes:
    """Resolve the 32-byte AES key from settings.

    Cached after first call; tests can flush via :func:`reset_cache`.
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key
    with _lock:
        if _cached_key is not None:
            return _cached_key
        from app.core.config import settings

        raw = (settings.dlp_audit_key or "").strip()
        if raw:
            # Prefer hex-encoded 32-byte keys. Fall back to HKDF over
            # the raw string so any non-empty value still works.
            try:
                candidate = bytes.fromhex(raw)
            except ValueError:
                candidate = None
            if candidate is not None and len(candidate) == _KEY_BYTES:
                _cached_key = candidate
            else:
                _cached_key = HKDF(
                    algorithm=hashes.SHA256(),
                    length=_KEY_BYTES,
                    salt=None,
                    info=_HKDF_INFO,
                ).derive(raw.encode("utf-8"))
            return _cached_key

        # Dev fallback — HKDF over master_encryption_key.
        # validate_secrets() rejects this path in production.
        fallback_source = (
            settings.master_encryption_key
            or "dev-master-encryption-key-fallback"
        )
        _cached_key = HKDF(
            algorithm=hashes.SHA256(),
            length=_KEY_BYTES,
            salt=None,
            info=_HKDF_INFO,
        ).derive(fallback_source.encode("utf-8"))
        return _cached_key


def reset_cache() -> None:
    """Drop the cached key — only used by tests after monkeypatching settings."""
    global _cached_key
    with _lock:
        _cached_key = None


def encrypt(plaintext: str) -> str:
    """Return a base64-urlsafe token containing ``plaintext`` under AES-256-GCM.

    Empty / None inputs return an empty string so callers can use
    the result as a direct DB column value without a separate
    NULL-vs-token branch.
    """
    if not plaintext:
        return ""
    try:
        key = _derive_key()
        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.urlsafe_b64encode(nonce + ct).decode("ascii")
    except Exception as exc:  # pragma: no cover - defensive
        raise DlpAuditCryptoError(f"encrypt failed: {exc}") from exc


def decrypt(token: str) -> str:
    """Return the plaintext for ``token`` (the inverse of :func:`encrypt`).

    Empty / None tokens return an empty string. Any malformed input
    or tag-mismatch raises :class:`DlpAuditCryptoError` so the caller
    can return a 4xx/5xx with a non-leaky message.
    """
    if not token:
        return ""
    try:
        key = _derive_key()
        blob = base64.urlsafe_b64decode(token.encode("ascii"))
        if len(blob) < _NONCE_BYTES + 16:
            raise DlpAuditCryptoError("ciphertext too short")
        nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
    except DlpAuditCryptoError:
        raise
    except Exception as exc:
        raise DlpAuditCryptoError(f"decrypt failed: {exc}") from exc
