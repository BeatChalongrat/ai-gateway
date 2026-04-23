"""Phase 3.5 Commit 28 — email template rendering tests.

EN + TH variants for verification, password reset, invite. Unknown
language codes fall back to English. First line of each .txt file
is the subject and must be pulled out by the renderer.
"""

from __future__ import annotations

import pytest

from app.services.email import templates


class TestVerificationRender:
    def test_en_template_uses_english_subject(self):
        subject, html, text = templates.render_verification_email(
            display_name="Alice",
            verification_link="https://ex.example/v?t=x",
            language="en",
        )
        assert "Verify" in subject
        assert "Alice" in text
        assert "https://ex.example/v?t=x" in text
        assert "https://ex.example/v?t=x" in html
        # Subject line must NOT appear in body (it's extracted).
        assert subject not in text

    def test_th_template_uses_thai_subject_and_body(self):
        subject, html, text = templates.render_verification_email(
            display_name="สมชาย",
            verification_link="https://ex.example/v?t=y",
            language="th",
        )
        assert "ยืนยัน" in subject
        assert "สมชาย" in text
        assert "สวัสดี" in text
        # HTML has a Thai lang attribute.
        assert 'lang="th"' in html

    def test_unknown_language_falls_back_to_english(self):
        subject, _, text = templates.render_verification_email(
            display_name="Alice",
            verification_link="https://ex.example/v?t=z",
            language="fr",  # not supported
        )
        assert "Verify" in subject
        assert "Welcome" in text

    def test_missing_display_name_defaults_to_there(self):
        _, _, text = templates.render_verification_email(
            display_name=None,
            verification_link="https://ex.example/v?t=w",
            language="en",
        )
        assert "Hi there," in text


class TestPasswordResetRender:
    def test_en_subject_and_expiry(self):
        subject, html, text = templates.render_password_reset_email(
            display_name="Bob",
            reset_link="https://ex.example/r?t=a",
            expires_in_minutes=60,
            language="en",
        )
        assert "Reset" in subject
        assert "60" in text
        assert "60" in html
        assert "https://ex.example/r?t=a" in text

    def test_th_subject_and_expiry(self):
        subject, _, text = templates.render_password_reset_email(
            display_name="สมหญิง",
            reset_link="https://ex.example/r?t=b",
            expires_in_minutes=60,
            language="th",
        )
        assert "รีเซ็ต" in subject
        assert "60 นาที" in text
        assert "สมหญิง" in text


class TestInviteRender:
    def test_en_carries_code_and_tenant(self):
        subject, html, text = templates.render_invite_email(
            inviter_name="Alice",
            tenant_name="Acme",
            invite_link="https://ex.example/s?c=xyz",
            invite_code="XYZ-ABC-123",
            language="en",
        )
        assert "Acme" in subject
        assert "XYZ-ABC-123" in text
        assert "XYZ-ABC-123" in html
        assert "Alice" in text

    def test_th_carries_code_and_tenant(self):
        subject, _, text = templates.render_invite_email(
            inviter_name="สมชาย",
            tenant_name="Acme",
            invite_link="https://ex.example/s?c=xyz",
            invite_code="XYZ-ABC-123",
            language="th",
        )
        assert "Acme" in subject
        assert "XYZ-ABC-123" in text
        assert "สมชาย" in text
        assert "คำเชิญ" in text

    def test_missing_inviter_name_defaults(self):
        _, _, text = templates.render_invite_email(
            inviter_name=None,
            tenant_name="Acme",
            invite_link="https://ex.example/s?c=xyz",
            invite_code="XYZ-ABC-123",
            language="en",
        )
        # Fallback "A teammate" from templates.render_invite_email.
        assert "A teammate" in text


class TestPreferredLanguageColumn:
    """Migration 032 introduces the column used to drive language
    selection at send time."""

    def test_orm_model_has_preferred_language_attr(self):
        from app.models.portal_user import PortalUserORM

        # mapped_column attributes live on the class; this will
        # AttributeError if 032 wasn't applied to the ORM.
        assert hasattr(PortalUserORM, "preferred_language")
