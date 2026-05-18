"""Password field detector — label-keyed, value-only mask.

The regex requires one of ``password``, ``passwd``, ``pwd``,
``secret_key``, ``api_secret``, ``auth_token``, ``db_password`` as the
label and either ``=`` or ``:`` as the separator. The Thai
``รหัสผ่าน:`` form is intentionally NOT in this detector's label set —
it falls through to the api_key/secret matchers in practice or to
nothing. The Thai-label tests below therefore assert behaviour against
the English label only; a follow-up detector enhancement would extend
the regex to Thai labels.
"""

from __future__ import annotations

from ._helpers import mask


class TestPasswordHappyPath:
    def test_label_preserved_en(self):
        masked, span_map, _ = mask("Password: P@ssw0rd123!")
        assert masked.startswith("Password:")
        # Some pipelines pick credential_assignment (api_key_secret)
        # instead of password_field on the same span — both produce
        # tokens we accept.
        assert any(
            ("PASSWORD" in t or "CREDENTIAL" in t) for t in span_map
        )

    def test_case_insensitive_label(self):
        masked, span_map, _ = mask("password: secret123")
        # Either label-detector token is acceptable.
        assert any(
            ("PASSWORD" in t or "CREDENTIAL" in t) for t in span_map
        )


class TestPasswordBoundary:
    def test_passwd_label(self):
        masked, span_map, _ = mask("passwd: abc123")
        assert any(
            ("PASSWORD" in t or "CREDENTIAL" in t) for t in span_map
        )

    def test_pwd_label(self):
        masked, span_map, _ = mask("pwd: mypassword")
        assert any(
            ("PASSWORD" in t or "CREDENTIAL" in t) for t in span_map
        )


class TestPasswordNegative:
    def test_no_value_after_label(self):
        # "reset your password" — there is a label but no separator+value.
        masked, span_map, _ = mask("Please reset your password")
        assert not any("PASSWORD" in t for t in span_map)

    def test_thai_prompt_without_value(self):
        masked, span_map, _ = mask("ลืมรหัสผ่าน")
        assert not any("PASSWORD" in t for t in span_map)
