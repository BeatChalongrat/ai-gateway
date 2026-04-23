"""OAuth login/link logic for the portal.

Called by the portal's NextAuth ``signIn`` callback AFTER it has
verified the provider's token. This module is the policy layer:

1. If a row exists with (oauth_provider, oauth_id) → return tokens for it.
2. Else if a row exists with the same email → LINK the OAuth identity
   onto that existing account and return tokens. This lets users who
   signed up with email and a password later "continue with Google"
   on the same email without creating a duplicate account.
3. Else create a new account:
   - password_hash = a random string that won't validate (user must
     keep using OAuth, or do a future password-reset flow)
   - credit_balance seeded from ``free_trial_tokens`` system setting
   - signup_bonus logged in credit_transactions

The upstream ``oauth_id`` values are already unique per provider —
we rely on the composite unique index on (oauth_provider, oauth_id)
to prevent duplicates at write time in addition to the lookup.
"""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models.portal_user import PortalUserORM
from app.services.portal import auth_service, credit_service


def login_or_create(
    db: Session,
    *,
    provider: str,
    oauth_id: str,
    email: str,
    display_name: str | None,
    avatar_url: str | None,
) -> dict:
    if provider not in ("google", "github"):
        raise ValidationError(
            "Unsupported OAuth provider.",
            code="invalid_provider",
            details={"provider": provider},
        )

    email_norm = (email or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        raise ValidationError(
            "Provider did not return a valid email.",
            code="invalid_email",
        )

    # 1. Existing OAuth account?
    user = db.execute(
        select(PortalUserORM).where(
            and_(
                PortalUserORM.oauth_provider == provider,
                PortalUserORM.oauth_id == oauth_id,
            )
        )
    ).scalar_one_or_none()

    if user is None:
        # 2. Link to an email match?
        user = db.execute(
            select(PortalUserORM).where(PortalUserORM.email == email_norm)
        ).scalar_one_or_none()
        if user is not None:
            user.oauth_provider = provider
            user.oauth_id = oauth_id
            if avatar_url and not user.avatar_url:
                user.avatar_url = avatar_url
            db.flush()
        else:
            # 3. Create a new account. Signups may be disabled — the
            # system_settings check is intentionally run here and NOT on
            # the login path for existing OAuth users, so an admin can
            # pause new signups without kicking existing users.
            signup_enabled = (
                credit_service.get_setting(db, "portal_signup_enabled", "true")
                or "true"
            ).strip().lower()
            if signup_enabled not in ("1", "true", "yes", "on"):
                raise ValidationError(
                    "New signups are temporarily disabled.",
                    code="signup_disabled",
                )

            free_trial_raw = (
                credit_service.get_setting(db, "free_trial_tokens", "0") or "0"
            )
            try:
                free_trial = max(0, int(free_trial_raw))
            except ValueError:
                free_trial = 0

            user = PortalUserORM(
                id=str(uuid.uuid4()),
                email=email_norm,
                # Unusable placeholder — bcrypt.checkpw will return False
                # for any input. Users who signed up via OAuth need to go
                # through a future password-reset flow to set a usable
                # password (not implemented yet).
                password_hash="!oauth:" + secrets.token_urlsafe(24),
                display_name=(display_name or "").strip() or None,
                credit_balance=0,
                credit_used=0,
                plan="free_trial",
                status="active",
                email_verified=True,  # the provider has already verified
                oauth_provider=provider,
                oauth_id=oauth_id,
                avatar_url=avatar_url,
            )
            db.add(user)
            db.flush()

            if free_trial > 0:
                credit_service.topup_credits(
                    db,
                    user_id=user.id,
                    amount=free_trial,
                    created_by="system",
                    description=f"Signup bonus ({provider})",
                    type="signup_bonus",
                )
                db.refresh(user)

    return {
        "access_token": auth_service.create_portal_token(
            user.id,
            user.email,
            "access",
            tenant_id=user.tenant_id,
            tenant_role=user.tenant_role or "member",
            token_version=user.token_version or 1,
        ),
        "refresh_token": auth_service.create_portal_token(
            user.id,
            user.email,
            "refresh",
            tenant_id=user.tenant_id,
            tenant_role=user.tenant_role or "member",
            token_version=user.token_version or 1,
        ),
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "plan": user.plan,
            "status": user.status,
            "credit_balance": int(user.credit_balance),
            "credit_used": int(user.credit_used),
            "avatar_url": user.avatar_url,
            "oauth_provider": user.oauth_provider,
            "created_at": user.created_at,
        },
    }
