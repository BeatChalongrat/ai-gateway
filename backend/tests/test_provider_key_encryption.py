import base64
import secrets

import pytest

from app.core.security import (
    decrypt_provider_key,
    encrypt_provider_key,
    is_aes_encrypted,
)


def test_encrypt_decrypt_roundtrip():
    master = secrets.token_hex(32)
    original = "sk-ant-api03-realkey123456"
    encrypted = encrypt_provider_key(original, master)
    assert encrypted != original
    assert is_aes_encrypted(encrypted)
    assert decrypt_provider_key(encrypted, master) == original


def test_version_byte_present():
    master = secrets.token_hex(32)
    blob = base64.b64decode(encrypt_provider_key("test", master))
    assert blob[0:1] == b"\x01"


def test_nonce_random_per_call():
    master = secrets.token_hex(32)
    a = encrypt_provider_key("same", master)
    b = encrypt_provider_key("same", master)
    assert a != b  # fresh nonce each call → different ciphertext
    assert decrypt_provider_key(a, master) == "same"
    assert decrypt_provider_key(b, master) == "same"


def test_is_aes_encrypted_false_for_legacy_base64():
    legacy = base64.b64encode(b"sk-ant-api03-some-real-looking-key-value").decode()
    assert not is_aes_encrypted(legacy)


def test_decrypt_rejects_legacy_base64():
    legacy = base64.b64encode(b"sk-test-key").decode()
    with pytest.raises(ValueError, match="version byte"):
        decrypt_provider_key(legacy, secrets.token_hex(32))


def test_wrong_master_key_fails():
    encrypted = encrypt_provider_key("secret", secrets.token_hex(32))
    with pytest.raises(Exception):  # InvalidTag from AESGCM
        decrypt_provider_key(encrypted, secrets.token_hex(32))


def test_bad_master_key_length_rejected():
    with pytest.raises(ValueError, match="32 bytes"):
        encrypt_provider_key("x", "abcd")  # 2 bytes, not 32


def test_thai_plaintext_roundtrip():
    master = secrets.token_hex(32)
    original = "คีย์ลับ-sk-ant-๑๒๓"
    assert decrypt_provider_key(encrypt_provider_key(original, master), master) == original
