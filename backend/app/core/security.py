import base64
import hashlib
import os
import secrets
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 1-byte version tag prefixed to every AES-GCM blob so a stored value's
# scheme is detectable by exact byte match — this is what makes the
# Base64 → AES migration idempotent (a re-run skips already-encrypted
# rows) and lets decrypt fail fast on a legacy Base64 value.
_VERSION_AES_GCM = b"\x01"


def mask_value(value: str, visible_suffix: int = 4) -> str:
    if len(value) <= visible_suffix:
        return "*" * len(value)
    return "*" * (len(value) - visible_suffix) + value[-visible_suffix:]


def hash_api_key(raw_key: str) -> str:
    """Deterministic SHA-256 hash of a raw API key. Returns hex digest (64 chars)."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "gw") -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        (raw_key, key_prefix, key_hash) where:
        - raw_key: the full key to return to the caller once (e.g. "gw_a1b2c3d4...")
        - key_prefix: first 8 chars for display/identification
        - key_hash: SHA-256 hex digest for storage
    """
    token = secrets.token_hex(24)  # 48 hex chars
    raw_key = f"{prefix}_{token}"
    key_prefix = raw_key[:8]
    key_hash = hash_api_key(raw_key)
    return raw_key, key_prefix, key_hash


def decrypt_secret(secret_ref: Optional[str]) -> Optional[str]:
    # TODO: integrate real secret manager or encryption service
    return secret_ref


# ---------------------------------------------------------------------------
# Provider API key encryption (AES-256-GCM)
#
# Provider keys were historically stored as plain Base64 in
# ``provider_keys.api_key_encrypted`` / ``providers.api_key_encrypted`` —
# reversible by anyone with DB read access. These helpers replace that with
# authenticated AES-256-GCM. The on-disk format is:
#
#     base64( 0x01 ‖ 12-byte nonce ‖ ciphertext ‖ 16-byte GCM tag )
#
# The 0x01 version byte makes the scheme self-describing so the one-time
# migration (scripts/migrate_provider_keys.py) is idempotent.
# ---------------------------------------------------------------------------

# Minimum decoded blob length: 1 version + 12 nonce + 0 plaintext + 16 tag.
_MIN_AES_BLOB_LEN = 1 + 12 + 16


def encrypt_provider_key(plaintext: str, master_key_hex: str) -> str:
    """Encrypt a provider API key with AES-256-GCM.

    ``master_key_hex`` must be 64 hex chars (32 bytes / 256 bits).
    Returns the base64 of ``0x01 + nonce + ciphertext + tag``.
    """
    key = bytes.fromhex(master_key_hex)
    if len(key) != 32:
        raise ValueError("master key must be 32 bytes (64 hex chars)")
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(_VERSION_AES_GCM + nonce + ct).decode("ascii")


def decrypt_provider_key(encrypted_b64: str, master_key_hex: str) -> str:
    """Decrypt a value produced by :func:`encrypt_provider_key`.

    Raises ``ValueError`` if the value is not a versioned AES-GCM blob
    (e.g. a legacy Base64 value that hasn't been migrated yet), and the
    cryptography library raises ``InvalidTag`` on a wrong key / tampered
    ciphertext.
    """
    blob = base64.b64decode(encrypted_b64)
    if len(blob) < _MIN_AES_BLOB_LEN or blob[0:1] != _VERSION_AES_GCM:
        raise ValueError("not an AES-GCM encrypted value (missing version byte)")
    key = bytes.fromhex(master_key_hex)
    nonce = blob[1:13]
    ct = blob[13:]
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")


def is_aes_encrypted(encoded_value: str) -> bool:
    """True when ``encoded_value`` is already a versioned AES-GCM blob.

    Used by the migration to skip rows that were already converted.
    """
    try:
        blob = base64.b64decode(encoded_value)
    except Exception:
        return False
    return len(blob) >= _MIN_AES_BLOB_LEN and blob[0:1] == _VERSION_AES_GCM
