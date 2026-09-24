"""FIX132 C — the OAuth sign-in refuses an account that is not active.

The password login and the refresh both refused a non-active account; the
Google/GitHub door (``oauth_service.login_or_create``) minted tokens for
any existing account, whatever its status — so an account the user had
asked to delete (or an admin had suspended) could sign straight back in,
and chat-edge would have accepted the fresh token.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM
from tests.conftest import _integration_engine

_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    db = _Session()
    db.query(PortalUserORM).filter(PortalUserORM.email == "gone@t.com").delete(synchronize_session=False)
    db.query(TenantORM).filter(TenantORM.tenant_id == "t-oauth").delete(synchronize_session=False)
    db.commit()
    db.close()
    yield


@pytest.mark.parametrize("status", ["pending_deletion", "suspended", "deleted"])
def test_oauth_sign_in_refuses_an_account_that_is_not_active(status):
    from app.core.exceptions import AuthenticationError
    from app.services.portal import oauth_service

    db = _Session()
    db.add(TenantORM(tenant_id="t-oauth", name="T", slug="t-oauth"))
    db.add(
        PortalUserORM(
            id=str(uuid.uuid4()), email="gone@t.com", password_hash="!oauth:x",
            status=status, email_verified=True, oauth_provider="google",
            oauth_id="g-123", tenant_id="t-oauth", tenant_role="member", token_version=2,
        )
    )
    db.commit()
    for oauth_id in ("g-123", "g-other"):  # the linked identity, and link-by-email
        with pytest.raises(AuthenticationError):
            oauth_service.login_or_create(
                db, provider="google", oauth_id=oauth_id, email="gone@t.com",
                display_name=None, avatar_url=None, email_verified=True,
            )
        db.rollback()
    db.close()
