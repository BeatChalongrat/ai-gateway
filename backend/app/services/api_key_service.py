"""API Key domain service: create, verify, revoke.

Pure service logic — takes a SQLAlchemy session, no HTTP/FastAPI concerns.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.security import generate_api_key, hash_api_key
from app.models.api_key import ApiKeyORM


# 1-hour grace period between rotation and the old key hard-expiring.
# Matches Beat's Phase 4 F2 spec: "Sets old key to expire in 1 hour".
_ROTATION_GRACE_PERIOD = timedelta(hours=1)

# Phase-1 B7: ``last_used_at`` used to be written + flushed on EVERY
# authenticated request — turning a read-only auth lookup into a write on
# the hot path. Debounce it to at most once per key per interval. The
# field drives "last seen" displays, not security decisions, so coarse
# resolution is fine. State is per-worker-process and best-effort (a
# bounded dict); losing it on restart just means one extra write.
_LAST_USED_WRITE_INTERVAL_S = 300.0  # 5 minutes
_last_used_writes: dict[str, float] = {}
_LAST_USED_MAX_TRACKED = 50_000  # cap the dict so it can't grow unbounded


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

        # B7: debounce the last_used_at write. Only persist (and flush)
        # when the previous write for this key was more than the interval
        # ago — removes a DB write from the vast majority of requests.
        mono = time.monotonic()
        last_write = _last_used_writes.get(api_key.key_id, 0.0)
        if mono - last_write >= _LAST_USED_WRITE_INTERVAL_S:
            api_key.last_used_at = datetime.now(timezone.utc)
            session.flush()
            if len(_last_used_writes) >= _LAST_USED_MAX_TRACKED:
                _last_used_writes.clear()
            _last_used_writes[api_key.key_id] = mono
        return api_key

    async def verify_key_async(
        self, session: AsyncSession, raw_key: str
    ) -> ApiKeyORM | None:
        """Async variant of :meth:`verify_key`.

        Identical validity rules (hash match, ``status == "active"``,
        not expired) and the SAME Phase-1 B7 ``last_used_at`` debounce —
        the write only fires when the previous write for this key was
        more than ``_LAST_USED_WRITE_INTERVAL_S`` ago. Only the DB calls
        are awaited (SELECT + the debounced flush); the debounce dict and
        time bookkeeping stay plain sync.
        """
        key_hash = hash_api_key(raw_key)
        stmt = select(ApiKeyORM).where(ApiKeyORM.key_hash == key_hash)
        result = await session.execute(stmt)
        api_key = result.scalar_one_or_none()

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

        # B7: debounce the last_used_at write (same semantics as the sync
        # path). Only persist (and flush) when the previous write for this
        # key was more than the interval ago.
        mono = time.monotonic()
        last_write = _last_used_writes.get(api_key.key_id, 0.0)
        if mono - last_write >= _LAST_USED_WRITE_INTERVAL_S:
            api_key.last_used_at = datetime.now(timezone.utc)
            await session.flush()
            if len(_last_used_writes) >= _LAST_USED_MAX_TRACKED:
                _last_used_writes.clear()
            _last_used_writes[api_key.key_id] = mono
        return api_key

    def revoke_key(self, session: Session, key_id: str) -> bool:
        """Revoke an API key by key_id. Returns True if the key was found and revoked."""
        api_key = session.get(ApiKeyORM, key_id)
        if api_key is None:
            return False
        api_key.status = "revoked"
        session.flush()
        return True

    def rotate_key(
        self,
        session: Session,
        key_id: str,
    ) -> tuple[str, ApiKeyORM] | None:
        """Rotate an API key: mint a fresh value, grace-expire the old.

        Phase 4 Feature 2. The old row is kept so deployments that
        haven't swapped the key yet keep working for a 1-hour grace
        period; ``verify_key`` already honours ``expires_at``. A brand-
        new row is inserted with the same tenant / project / name /
        owner and a new ``key_id`` + hash — callers update their
        deployment with the returned raw key value (shown exactly once,
        matching ``create_key`` semantics).

        Returns ``None`` when the target key doesn't exist or is
        already revoked — rotating a revoked key would resurrect it
        under a new id, which is not what we want.
        """
        old = session.get(ApiKeyORM, key_id)
        if old is None or old.status != "active":
            return None

        now = datetime.now(timezone.utc)

        # Stamp the grace expiry on the old row. If a shorter expiry
        # was already set manually, don't extend it — keep the earlier
        # one so deliberate short-TTL keys aren't accidentally
        # promoted.
        grace = now + _ROTATION_GRACE_PERIOD
        existing_exp = old.expires_at
        if existing_exp is not None and existing_exp.tzinfo is None:
            existing_exp = existing_exp.replace(tzinfo=timezone.utc)
        if existing_exp is None or grace < existing_exp:
            old.expires_at = grace

        # New row inherits every caller-facing attribute except the
        # secret material. The portal_user_id link is preserved so
        # credit-check + ownership queries still resolve.
        raw_key, key_prefix, key_hash = generate_api_key()
        new = ApiKeyORM(
            key_id=str(uuid.uuid4()),
            key_prefix=key_prefix,
            key_hash=key_hash,
            tenant_id=old.tenant_id,
            project_id=old.project_id,
            name=old.name,
            # Fresh created_at so the audit trail distinguishes the
            # rotated key from the original.
            portal_user_id=old.portal_user_id,
        )
        session.add(new)
        session.flush()
        return raw_key, new
