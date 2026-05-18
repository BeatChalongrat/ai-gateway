"""Private key header detector — matches BEGIN line of PEM blocks."""

from __future__ import annotations

from ._helpers import detector_names_for


class TestPrivateKeyHappyPath:
    def test_rsa_header(self):
        names = detector_names_for(
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA..."
        )
        assert "private_key" in names

    def test_generic_pkcs8_header(self):
        names = detector_names_for(
            "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq..."
        )
        assert "private_key" in names

    def test_openssh_header(self):
        names = detector_names_for(
            "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk..."
        )
        assert "private_key" in names

    def test_multiline_block_single_match(self):
        block = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEAvK1J5...\n"
            "FAKE_BASE64_BODY...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        names = detector_names_for(block)
        assert "private_key" in names


class TestPrivateKeyNegative:
    def test_public_key_header(self):
        assert "private_key" not in detector_names_for(
            "-----BEGIN PUBLIC KEY-----"
        )

    def test_certificate_header(self):
        assert "private_key" not in detector_names_for(
            "-----BEGIN CERTIFICATE-----"
        )
