"""Repository for ``ProviderKeyORM``.

Owns every SQLAlchemy query that touches the ``provider_keys`` table.
``ProviderKeyService`` delegates all session access here and keeps
business logic (round-robin across active keys, encryption, label
generation, async HTTP key validation, legacy ``providers`` table
fallback) above this boundary.

Design notes
------------
- ``delete()`` is a HARD delete (SQL DELETE). The service's
  ``delete_key()`` public API still soft-deletes by flipping
  ``is_active`` — the repository's hard-delete primitive is available
  for admin tooling and tests.
- ``set_active(key_id, provider_name)`` atomically flips one key on
  and every sibling off. Atomicity is per-session — the caller owns
  the transaction boundary.
- The repo never decodes keys. All encryption / decryption stays in
  the service layer so encrypted bytes never leak through return
  values of the repository.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update as sa_update

from app.models.provider_key import ProviderKeyORM
from app.repositories.base_repository import BaseRepository


class ProviderKeyRepository(BaseRepository[ProviderKeyORM]):
    model_class = ProviderKeyORM

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_by_provider(self, provider_name: str) -> list[ProviderKeyORM]:
        """Return every key (active or not) for *provider_name*, by created_at."""
        stmt = (
            select(ProviderKeyORM)
            .where(ProviderKeyORM.provider_name == provider_name)
            .order_by(ProviderKeyORM.created_at)
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_active_by_provider(
        self, provider_name: str
    ) -> list[ProviderKeyORM]:
        """Return active keys for *provider_name*, ordered by created_at.

        Convenience over ``list_by_provider`` — the service's round-robin
        selection operates on this list and filters by
        ``last_test_status`` in Python (the filter is not DB-encoded
        because ``untested`` should still be reachable).
        """
        stmt = (
            select(ProviderKeyORM)
            .where(
                ProviderKeyORM.provider_name == provider_name,
                ProviderKeyORM.is_active == True,  # noqa: E712
            )
            .order_by(ProviderKeyORM.created_at)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_active_for_provider(
        self, provider_name: str
    ) -> ProviderKeyORM | None:
        """Return the oldest active key for *provider_name*, or None.

        Convenience for tests and one-off admin paths. The production
        request path uses ``list_active_by_provider`` + round-robin.
        """
        rows = self.list_active_by_provider(provider_name)
        return rows[0] if rows else None

    def get_by_provider_and_label(
        self, provider_name: str, label: str
    ) -> ProviderKeyORM | None:
        """Return the row occupying a provider/label slot, if any.

        Labels are unique across active and soft-deleted rows. The add-key
        flow uses this lookup to reactivate an inactive slot instead of
        attempting an INSERT that can only fail the unique constraint.
        """
        stmt = select(ProviderKeyORM).where(
            ProviderKeyORM.provider_name == provider_name,
            ProviderKeyORM.label == label,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def count_by_provider(self, provider_name: str) -> int:
        """Total key count (active or not) — used for label generation."""
        stmt = select(func.count()).select_from(ProviderKeyORM).where(
            ProviderKeyORM.provider_name == provider_name
        )
        return int(self.session.execute(stmt).scalar() or 0)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create(
        self,
        provider_name: str,
        label: str | None,
        api_key_encrypted: str,
        base_url: str | None = None,
    ) -> ProviderKeyORM:
        """Insert a new key row and return it with a populated id.

        ``base_url`` (FIX85) is the endpoint this key talks to; ``None``
        means the provider-level row, as every row did before.
        """
        row = ProviderKeyORM(
            id=str(uuid.uuid4()),
            provider_name=provider_name,
            label=label,
            api_key_encrypted=api_key_encrypted,
            last_test_status="untested",
            base_url=base_url,
        )
        return self.add(row)

    def update(self, key: ProviderKeyORM, **changes) -> ProviderKeyORM:
        """Apply ``changes`` to *key* and flush.

        Only attributes that already exist on the model are touched —
        unknown kwargs raise ``AttributeError`` so typos surface at
        call time rather than silently vanishing.
        """
        for field, value in changes.items():
            if not hasattr(key, field):
                raise AttributeError(
                    f"ProviderKeyORM has no attribute {field!r}"
                )
            setattr(key, field, value)
        self.session.flush()
        return key

    def delete(self, key_id: str) -> bool:
        """Hard-delete the row with the given id.

        Returns True if a row was deleted, False if it did not exist.
        The service's ``delete_key`` still soft-deletes — this is a
        primitive, not a drop-in replacement.
        """
        row = self.get_by_id(key_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def set_active(
        self, key_id: str, provider_name: str
    ) -> ProviderKeyORM | None:
        """Mark *key_id* active and every sibling for *provider_name* inactive.

        Atomic within the caller's session. Returns the now-active row
        or None if *key_id* does not exist.
        """
        target = self.get_by_id(key_id)
        if target is None or target.provider_name != provider_name:
            return None

        self.session.execute(
            sa_update(ProviderKeyORM)
            .where(
                ProviderKeyORM.provider_name == provider_name,
                ProviderKeyORM.id != key_id,
            )
            .values(is_active=False)
        )
        target.is_active = True
        self.session.flush()
        return target

    def record_test_result(
        self,
        key_id: str,
        status: str,
        latency_ms: int | None,
        tested_base_url: str | None = None,
    ) -> ProviderKeyORM | None:
        """Stamp the last-tested fields for *key_id*. No-op if unknown.

        ``tested_base_url`` (FIX85) is the endpoint the verdict was reached
        against, so a later reader can say WHICH box said yes or no.
        """
        row = self.get_by_id(key_id)
        if row is None:
            return None
        row.last_tested_at = datetime.now(timezone.utc)
        row.last_test_status = status
        row.last_test_latency_ms = latency_ms
        row.last_tested_base_url = tested_base_url
        self.session.flush()
        return row
