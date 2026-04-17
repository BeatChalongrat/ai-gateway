import hashlib
import secrets
from typing import Optional


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
