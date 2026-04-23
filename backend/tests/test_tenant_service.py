"""Phase 3 Commit 19 — tenant_service + portal JWT claim tests.

Covers:

- ``create_tenant_with_owner``: atomic, slug collision resolution,
  bumps token_version so the old session invalidates.
- ``generate_invite``: owner-only gate, 43-char code, 7-day default
  expiry.
- ``redeem_invite``: happy path, expired, already-used, invalid,
  cross-tenant guard, information-leak-free wording.
- ``list_members`` + ``remove_member``: owner-only removal, refuses
  to strip the last owner.
- Portal JWT: new claims round-trip, ``get_portal_user`` rejects
  stale ``token_version``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM
from app.models.tenant_invite import TenantInviteORM
from app.services.portal import auth_service, tenant_service
import app.models  # noqa: F401 — register all ORM


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _make_user(
    db,
    *,
    user_id: str | None = None,
    email: str | None = None,
    tenant_id: str | None = None,
    tenant_role: str = "member",
    token_version: int = 1,
) -> PortalUserORM:
    user = PortalUserORM(
        id=user_id or str(uuid.uuid4()),
        email=email or f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hash",
        plan="free_trial",
        status="active",
        email_verified=True,
        credit_balance=0,
        credit_used=0,
        tenant_id=tenant_id,
        tenant_role=tenant_role,
        token_version=token_version,
    )
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# create_tenant_with_owner
# ---------------------------------------------------------------------------


class TestCreateTenantWithOwner:
    def test_creates_tenant_and_promotes_owner_atomically(self, db):
        user = _make_user(db, email="alice@example.com")
        tenant = tenant_service.create_tenant_with_owner(
            db, tenant_name="Acme", owner_user_id=user.id
        )
        db.refresh(user)
        assert user.tenant_id == tenant.tenant_id
        assert user.tenant_role == "owner"
        # token_version bumped to invalidate whatever session they had
        # before they owned a tenant.
        assert user.token_version == 2

    def test_trims_and_rejects_empty_name(self, db):
        user = _make_user(db)
        with pytest.raises(ValidationError, match="empty"):
            tenant_service.create_tenant_with_owner(
                db, tenant_name="   ", owner_user_id=user.id
            )

    def test_rejects_overly_long_name(self, db):
        user = _make_user(db)
        with pytest.raises(ValidationError, match="255"):
            tenant_service.create_tenant_with_owner(
                db, tenant_name="x" * 256, owner_user_id=user.id
            )

    def test_slug_collision_generates_distinct_slug(self, db):
        # Pre-seed a tenant with the slug this name would generate.
        db.add(
            TenantORM(
                tenant_id=str(uuid.uuid4()),
                name="Acme",
                slug="acme",
                status="active",
                region="global",
                ner_enabled=False,
            )
        )
        db.flush()
        user = _make_user(db)
        tenant = tenant_service.create_tenant_with_owner(
            db, tenant_name="Acme", owner_user_id=user.id
        )
        assert tenant.slug != "acme"
        assert tenant.slug.startswith("acme-")


# ---------------------------------------------------------------------------
# generate_invite
# ---------------------------------------------------------------------------


class TestGenerateInvite:
    def _owned_tenant(self, db):
        owner = _make_user(db, email="owner@example.com")
        tenant = tenant_service.create_tenant_with_owner(
            db, tenant_name="Acme", owner_user_id=owner.id
        )
        db.refresh(owner)
        return owner, tenant

    def test_owner_can_generate_invite(self, db):
        owner, tenant = self._owned_tenant(db)
        invite = tenant_service.generate_invite(
            db, tenant_id=tenant.tenant_id, creator_user_id=owner.id
        )
        assert invite.tenant_id == tenant.tenant_id
        assert len(invite.code) >= 43  # 32 bytes -> 43-char urlsafe
        # 7-day default TTL.
        now = datetime.now(timezone.utc)
        expires = invite.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        assert expires > now + timedelta(days=6, hours=23)

    def test_member_cannot_generate_invite(self, db):
        owner, tenant = self._owned_tenant(db)
        member = _make_user(
            db, tenant_id=tenant.tenant_id, tenant_role="member"
        )
        with pytest.raises(AuthorizationError, match="owners"):
            tenant_service.generate_invite(
                db, tenant_id=tenant.tenant_id, creator_user_id=member.id
            )

    def test_stranger_cannot_generate_invite_for_someone_elses_tenant(
        self, db
    ):
        _, tenant = self._owned_tenant(db)
        stranger_owner = _make_user(db, email="s@example.com")
        tenant_service.create_tenant_with_owner(
            db, tenant_name="Other", owner_user_id=stranger_owner.id
        )
        db.refresh(stranger_owner)
        with pytest.raises(AuthorizationError):
            tenant_service.generate_invite(
                db,
                tenant_id=tenant.tenant_id,
                creator_user_id=stranger_owner.id,
            )

    def test_invite_codes_are_distinct(self, db):
        owner, tenant = self._owned_tenant(db)
        codes = {
            tenant_service.generate_invite(
                db, tenant_id=tenant.tenant_id, creator_user_id=owner.id
            ).code
            for _ in range(5)
        }
        assert len(codes) == 5


# ---------------------------------------------------------------------------
# redeem_invite
# ---------------------------------------------------------------------------


class TestRedeemInvite:
    def _setup(self, db):
        owner = _make_user(db, email="owner@example.com")
        tenant = tenant_service.create_tenant_with_owner(
            db, tenant_name="Acme", owner_user_id=owner.id
        )
        db.refresh(owner)
        invite = tenant_service.generate_invite(
            db, tenant_id=tenant.tenant_id, creator_user_id=owner.id
        )
        newcomer = _make_user(db, email="new@example.com")
        return owner, tenant, invite, newcomer

    def test_happy_path_joins_tenant_as_member(self, db):
        _, tenant, invite, newcomer = self._setup(db)
        tenant_service.redeem_invite(
            db, code=invite.code, redeeming_user_id=newcomer.id
        )
        db.refresh(newcomer)
        db.refresh(invite)
        assert newcomer.tenant_id == tenant.tenant_id
        assert newcomer.tenant_role == "member"
        assert invite.used_at is not None
        assert invite.used_by == newcomer.id
        # Token version bumped so any in-flight session invalidates.
        assert newcomer.token_version == 2

    def test_revoked_invite_rejected(self, db):
        """Post-invite-redesign (2026-04-24): codes are multi-use until
        revoked or expired. The pre-redesign variant of this test
        asserted that a second redemption failed as ``already_used``;
        the current model only kills a code via explicit revocation."""
        _, _, invite, newcomer = self._setup(db)
        tenant_service.redeem_invite(
            db, code=invite.code, redeeming_user_id=newcomer.id
        )
        # Mark the invite revoked by the owner; a second redemption
        # must now fail with ``invite_revoked``.
        invite.revoked_at = datetime.now(timezone.utc)
        db.flush()
        stranger = _make_user(db, email="str@example.com")
        with pytest.raises(ConflictError, match="revoked"):
            tenant_service.redeem_invite(
                db, code=invite.code, redeeming_user_id=stranger.id
            )

    def test_expired_invite_rejected(self, db):
        _, _, invite, newcomer = self._setup(db)
        # Manually expire.
        invite.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.flush()
        with pytest.raises(ConflictError, match="expired"):
            tenant_service.redeem_invite(
                db, code=invite.code, redeeming_user_id=newcomer.id
            )

    def test_invalid_code_yields_generic_not_found(self, db):
        _, _, _, newcomer = self._setup(db)
        with pytest.raises(NotFoundError, match="invalid"):
            tenant_service.redeem_invite(
                db, code="nonexistent-code", redeeming_user_id=newcomer.id
            )

    def test_user_already_in_a_different_tenant_rejected(self, db):
        _, _, invite, newcomer = self._setup(db)
        # Put them in a different tenant first.
        other_owner = _make_user(db, email="other-owner@example.com")
        tenant_service.create_tenant_with_owner(
            db, tenant_name="Other", owner_user_id=newcomer.id
        )
        db.refresh(newcomer)
        with pytest.raises(ConflictError, match="different tenant"):
            tenant_service.redeem_invite(
                db, code=invite.code, redeeming_user_id=newcomer.id
            )


# ---------------------------------------------------------------------------
# list_members + remove_member
# ---------------------------------------------------------------------------


class TestListAndRemoveMembers:
    def _tenant_with_members(self, db):
        owner = _make_user(db, email="owner@example.com")
        tenant = tenant_service.create_tenant_with_owner(
            db, tenant_name="Acme", owner_user_id=owner.id
        )
        db.refresh(owner)
        m1 = _make_user(
            db, email="m1@example.com", tenant_id=tenant.tenant_id
        )
        m2 = _make_user(
            db, email="m2@example.com", tenant_id=tenant.tenant_id
        )
        return owner, tenant, m1, m2

    def test_list_returns_all_members_in_creation_order(self, db):
        owner, tenant, m1, m2 = self._tenant_with_members(db)
        rows = tenant_service.list_members(db, tenant_id=tenant.tenant_id)
        assert [r.id for r in rows] == [owner.id, m1.id, m2.id]

    def test_owner_can_remove_member(self, db):
        owner, tenant, m1, _ = self._tenant_with_members(db)
        tenant_service.remove_member(
            db,
            tenant_id=tenant.tenant_id,
            actor_user_id=owner.id,
            target_user_id=m1.id,
        )
        db.refresh(m1)
        assert m1.tenant_id is None
        # Token version bumped to invalidate their session.
        assert m1.token_version >= 2

    def test_member_cannot_remove_another_member(self, db):
        _, tenant, m1, m2 = self._tenant_with_members(db)
        with pytest.raises(AuthorizationError):
            tenant_service.remove_member(
                db,
                tenant_id=tenant.tenant_id,
                actor_user_id=m1.id,
                target_user_id=m2.id,
            )

    def test_member_can_self_remove(self, db):
        _, tenant, m1, _ = self._tenant_with_members(db)
        tenant_service.remove_member(
            db,
            tenant_id=tenant.tenant_id,
            actor_user_id=m1.id,
            target_user_id=m1.id,
        )
        db.refresh(m1)
        assert m1.tenant_id is None

    def test_last_owner_cannot_be_removed(self, db):
        owner, tenant, _, _ = self._tenant_with_members(db)
        with pytest.raises(ConflictError, match="last owner"):
            tenant_service.remove_member(
                db,
                tenant_id=tenant.tenant_id,
                actor_user_id=owner.id,
                target_user_id=owner.id,
            )


# ---------------------------------------------------------------------------
# Portal JWT: new claims + token_version verification
# ---------------------------------------------------------------------------


class TestPortalJwtClaims:
    def test_mint_includes_tenant_and_version_claims(self):
        tok = auth_service.create_portal_token(
            "u-1",
            "a@example.com",
            "access",
            tenant_id="t-1",
            tenant_role="owner",
            token_version=3,
        )
        payload = auth_service.verify_portal_token(tok)
        assert payload["tenant_id"] == "t-1"
        assert payload["tenant_role"] == "owner"
        assert payload["token_version"] == 3

    def test_default_claims_for_legacy_callers(self):
        """Calls passing only (user_id, email, token_type) still work;
        the claims default to None/member/1 so the JWT is still
        well-formed."""
        tok = auth_service.create_portal_token(
            "u-2", "b@example.com", "access"
        )
        payload = auth_service.verify_portal_token(tok)
        assert payload["tenant_id"] is None
        assert payload["tenant_role"] == "member"
        assert payload["token_version"] == 1


class TestGetPortalUserTokenVersionRejection:
    """Concern 1 verification: stale JWTs fail closed at the dep layer."""

    def test_matching_token_version_accepted(self, db):
        from app.api.deps import get_portal_user

        user = _make_user(db, email="v@example.com", token_version=5)
        tok = auth_service.create_portal_token(
            user.id,
            user.email,
            "access",
            tenant_id=None,
            tenant_role="member",
            token_version=5,
        )
        # Pass through the dep directly.
        result = get_portal_user(
            authorization=f"Bearer {tok}", session=db
        )
        assert result.id == user.id

    def test_stale_token_version_rejected(self, db):
        from app.api.deps import get_portal_user
        from app.core.exceptions import AuthenticationError

        user = _make_user(db, email="s@example.com", token_version=5)
        # Mint with an older version than the DB row.
        tok = auth_service.create_portal_token(
            user.id,
            user.email,
            "access",
            tenant_id=None,
            tenant_role="member",
            token_version=3,
        )
        with pytest.raises(AuthenticationError, match="log in again"):
            get_portal_user(authorization=f"Bearer {tok}", session=db)

    def test_absent_version_claim_treated_as_stale_after_bump(self, db):
        """Pre-Phase-3 tokens have no token_version claim. After the
        029 migration bumps every user to 2, those tokens must be
        rejected outright — the Phase 3 cutover's one-time re-login."""
        from app.api.deps import get_portal_user
        from app.core.exceptions import AuthenticationError
        import jwt as pyjwt
        from app.core.config import settings as app_settings

        user = _make_user(db, email="old@example.com", token_version=2)
        # Forge a pre-migration token: correct signature, but no
        # token_version claim at all.
        legacy_payload = {
            "sub": user.id,
            "email": user.email,
            "type": "portal",
            "token_use": "access",
            "iat": 1700000000,
            "exp": 9999999999,
            "jti": "legacy",
        }
        tok = pyjwt.encode(
            legacy_payload, app_settings.portal_jwt_secret, algorithm="HS256"
        )
        with pytest.raises(AuthenticationError, match="log in again"):
            get_portal_user(authorization=f"Bearer {tok}", session=db)
