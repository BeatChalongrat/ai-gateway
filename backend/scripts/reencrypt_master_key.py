"""FIX133 B — re-encrypt every secret stored at rest under a NEW master key.

Provider API keys (provider_keys.api_key_encrypted), web-search keys
(search_providers.api_key_encrypted) and connector OAuth tokens
(connector_tokens.access_token_encrypted / refresh_token_encrypted) are
AES-256-GCM blobs made by app.core.security.encrypt_provider_key with
settings.effective_master_key. In dev/test that key is the all-zero fallback
("0" * 64); production demands PROVIDER_KEY_MASTER_KEY. Switching a running
deployment to production therefore needs every blob rewritten under the new
key first — otherwise the service boots with keys it cannot read.

    python -m scripts.reencrypt_master_key --old dev --new <64 hex> [--apply]
    python -m scripts.reencrypt_master_key --old <64 hex> --new <64 hex> --apply

Dry run by default: says how many rows would change and how many already
decrypt under the new key (idempotent: a second run changes nothing). Each
row is decrypted with the old key and re-encrypted with the new one inside
one transaction; a row that decrypts with neither key stops the run before
anything is written (a legacy or foreign blob must be looked at, not
guessed). Nothing secret is ever printed. Take a backup first.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

DEV_KEY = "0" * 64

TARGETS = (
    ("provider_keys", "id", ("api_key_encrypted",)),
    ("search_providers", "id", ("api_key_encrypted",)),
    ("connector_tokens", "id", ("access_token_encrypted", "refresh_token_encrypted")),
)


@dataclass
class Plan:
    table: str
    column: str
    to_rewrite: int = 0
    already_new: int = 0
    empty: int = 0


def _key(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.lower() == "dev":
        return DEV_KEY
    try:
        if len(bytes.fromhex(raw)) == 32:
            return raw
    except ValueError:
        pass
    raise SystemExit("a master key is 64 hex characters (or the word 'dev' for the dev fallback)")


def rewrite_value(value: str | None, old_key: str, new_key: str):
    """(new_value, state) — state is 'rewritten' | 'already_new' | 'empty'.

    Raises ValueError when the value decrypts under neither key.
    """
    from app.core.security import decrypt_provider_key, encrypt_provider_key

    if not value:
        return value, "empty"
    try:
        decrypt_provider_key(value, new_key)
        return value, "already_new"
    except Exception:  # noqa: BLE001 — not the new key; try the old one
        pass
    plain = decrypt_provider_key(value, old_key)  # raises on a foreign blob
    return encrypt_provider_key(plain, new_key), "rewritten"


def run(conn, old_key: str, new_key: str, apply: bool) -> list[Plan]:
    """conn: a SQLAlchemy Connection (the app's sync engine). Returns the per-column plan."""
    from sqlalchemy import text

    plans: list[Plan] = []
    for table, pk, columns in TARGETS:
        if conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() is None:
            continue
        for column in columns:
            plan = Plan(table, column)
            rows = conn.execute(text(f"SELECT {pk}, {column} FROM {table} ORDER BY {pk}")).all()
            updates = []
            for row_id, value in rows:
                try:
                    new_value, state = rewrite_value(value, old_key, new_key)
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    raise SystemExit(
                        f"{table}.{column} row {row_id}: decrypts under neither key "
                        f"({type(exc).__name__}); nothing was written"
                    ) from exc
                if state == "rewritten":
                    plan.to_rewrite += 1
                    updates.append({"v": new_value, "id": row_id})
                elif state == "already_new":
                    plan.already_new += 1
                else:
                    plan.empty += 1
            if apply and updates:
                conn.execute(text(f"UPDATE {table} SET {column} = :v WHERE {pk} = :id"), updates)
            plans.append(plan)
    if apply:
        conn.commit()
    else:
        conn.rollback()
    return plans


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--old", required=True, help="the key the rows are encrypted with now (64 hex or 'dev')")
    ap.add_argument("--new", required=True, help="the key to encrypt with (64 hex)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--dsn", default=os.environ.get("REENCRYPT_DSN", ""), help="SQLAlchemy URL (default: the app's own database)")
    args = ap.parse_args(argv)
    old_key, new_key = _key(args.old), _key(args.new)
    if old_key == new_key:
        raise SystemExit("old and new keys are the same")
    from sqlalchemy import create_engine

    if args.dsn:
        engine = create_engine(args.dsn)
    else:
        from app.core.db import engine  # the app's own sync engine (psycopg 3)
    with engine.connect() as conn:
        plans = run(conn, old_key, new_key, args.apply)
    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"{mode}:")
    for p in plans:
        print(f"  {p.table}.{p.column}: rewritten={p.to_rewrite} already_new={p.already_new} empty={p.empty}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
