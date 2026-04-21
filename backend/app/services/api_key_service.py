"""API Key domain service: create, verify, revoke.

Pure service logic — takes a SQLAlchemy session, no HTTP/FastAPI concerns.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import generate_api_key, hash_api_key
from app.models.api_key import ApiKeyORM


class ApiKeyService:

    def create_key(
        self,
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        name: str,
        expires_at: datetime | None = None,
        portal_user_id: str | None = None,
    ) -> tuple[str, ApiKeyORM]:
        """Create a new API key.

        Returns (raw_key, api_key_orm). The raw_key is shown to the caller
        exactly once — only the hash is persisted. ``portal_user_id`` is
        set when the key is created via the self-service portal, so the
        gateway's credit-check path can find the owning portal user.
        """
        raw_key, key_prefix, key_hash = generate_api_key()

        api_key = ApiKeyORM(
            key_id=str(uuid.uuid4()),
            key_prefix=key_prefix,
            key_hash=key_hash,
            tenant_id=tenant_id,
            project_id=project_id,
            name=name,
            expires_at=expires_at,
            portal_user_id=portal_user_id,
        )
        session.add(api_key)
        session.flush()
        return raw_key, api_key

    def verify_key(self, session: Session, raw_key: str) -> ApiKeyORM | None:
        """Verify a raw API key. Returns the ApiKeyORM if valid, None otherwise.

        A key is valid when:
        1. Its hash matches a row in api_keys
        2. Its status is "active"
        3. It has not expired
        """
        key_hash = hash_api_key(raw_key)
        stmt = select(ApiKeyORM).where(ApiKeyORM.key_hash == key_hash)
        api_key = session.execute(stmt).scalar_one_or_none()

        if api_key is None:
            return None

        if api_key.status != "active":
            return None

        if api_key.expires_at is not None:
            now = datetime.now(timezone.utc)
            expires = api_key.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now >= expires:
                return None

        api_key.last_used_at = datetime.now(timezone.utc)
        session.flush()
        return api_key

    def revoke_key(self, session: Session, key_id: str) -> bool:
        """Revoke an API key by key_id. Returns True if the key was found and revoked."""
        api_key = session.get(ApiKeyORM, key_id)
        if api_key is None:
            return False
        api_key.status = "revoked"
        session.flush()
        return True
