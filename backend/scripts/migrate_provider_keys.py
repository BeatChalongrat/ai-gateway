"""One-time migration: re-encrypt provider keys from Base64 to AES-256-GCM.

Idempotent — rows already carrying the 0x01 AES-GCM version byte are
skipped, so re-running is safe.

Targets BOTH tables that store provider keys (the audit confirmed two):
  1. provider_keys.api_key_encrypted   (PK column: id)
  2. providers.api_key_encrypted        (PK column: provider_id)

Usage:
  cd backend
  PROVIDER_KEY_MASTER_KEY=<64-hex> python -m scripts.migrate_provider_keys

Notes:
  - Uses the app's async engine (app.core.db.get_async_session_factory);
    there is no module-level ``async_engine`` to import.
  - Wrapped in a single transaction — either both tables migrate or none.
"""

from __future__ import annotations

import asyncio
import base64
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_async_session_factory
from app.core.security import encrypt_provider_key, is_aes_encrypted


async def _migrate_table(session: AsyncSession, table: str, id_col: str) -> tuple[int, int]:
    """Re-encrypt every legacy row in one table. Returns (migrated, skipped).

    ``table`` / ``id_col`` are fixed code constants (never user input), so
    interpolating them into the SQL text is safe.
    """
    rows = (
        await session.execute(
            text(
                f"SELECT {id_col}, api_key_encrypted FROM {table} "
                "WHERE api_key_encrypted IS NOT NULL"
            )
        )
    ).fetchall()

    migrated = skipped = 0
    for row_id, encoded in rows:
        if not encoded or is_aes_encrypted(encoded):
            skipped += 1
            continue
        try:
            plaintext = base64.b64decode(encoded.encode()).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN {table}.{id_col}={row_id}: cannot Base64-decode ({exc}); skipping")
            skipped += 1
            continue
        new_value = encrypt_provider_key(plaintext, settings.effective_master_key)
        await session.execute(
            text(f"UPDATE {table} SET api_key_encrypted = :val WHERE {id_col} = :id"),
            {"val": new_value, "id": row_id},
        )
        migrated += 1
    return migrated, skipped


async def main() -> None:
    if not settings.provider_key_master_key:
        print("ERROR: set PROVIDER_KEY_MASTER_KEY (64 hex chars) before running.")
        sys.exit(1)

    factory = get_async_session_factory()
    async with factory() as session:
        async with session.begin():
            m1, s1 = await _migrate_table(session, "provider_keys", "id")
            print(f"provider_keys: {m1} migrated, {s1} skipped")
            m2, s2 = await _migrate_table(session, "providers", "provider_id")
            print(f"providers:     {m2} migrated, {s2} skipped")

    print(f"TOTAL: {m1 + m2} migrated, {s1 + s2} skipped")


if __name__ == "__main__":
    asyncio.run(main())
