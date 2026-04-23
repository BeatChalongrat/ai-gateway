"""Transactional email service (Phase 3.5).

Provides ``send_email`` and three high-level wrappers
(``send_verification_email``, ``send_password_reset_email``,
``send_invite_email``). Production path uses Resend; development
writes to the logger so tests never hit the real API.

See ``PORTAL_TENANCY_MIGRATION_PLAN.md`` §8 + Beat's Phase 3.5
decisions for scope (internal-tool launch, no bounce handling,
no unsubscribe UI yet).
"""

from app.services.email.sender import (
    EmailSendResult,
    send_email,
    send_invite_email,
    send_password_reset_email,
    send_verification_email,
)


__all__ = [
    "EmailSendResult",
    "send_email",
    "send_invite_email",
    "send_password_reset_email",
    "send_verification_email",
]
