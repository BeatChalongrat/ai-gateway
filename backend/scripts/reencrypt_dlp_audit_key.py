"""Re-encrypt the stored original prompts under a NEW ``DLP_AUDIT_KEY``.

``request_logs.input_raw_encrypted`` is an AES-256-GCM blob of what a person typed, before masking, made
by ``app.core.dlp_audit_crypto`` with ``DLP_AUDIT_KEY``; the admin trace endpoint decrypts it on demand.
Nothing else is made with that key: it signs nothing and hashes nothing. So rotating it breaks one thing
— every row written under the old key stops opening in the trace view — unless those rows are rewritten
under the new key. This does that.

The two keys are read from the ENVIRONMENT, by name, and are never taken on the command line, never
printed and never logged:

    docker exec -i -e DLP_AUDIT_KEY_OLD -e DLP_AUDIT_KEY_NEW <backend container> \\
        python - [--apply] < backend/scripts/reencrypt_dlp_audit_key.py

Dry run by default: it says how many rows already open under the new key, how many would be rewritten,
and how many open under neither. A row that opens under neither key stops the run before anything is
written, unless ``--leave-unreadable`` says to leave such rows as they are (they were unreadable before
and stay so; the 90-day retention job clears them in time). It is idempotent: a second run rewrites
nothing. Rows are rewritten in batches inside transactions. Take a backup first.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys

NONCE_BYTES = 12
KEY_BYTES = 32
HKDF_INFO = b"dlp-audit-key-v1"

# The table's own key: request_logs is partitioned by created_at and a row is named by request_id. (The
# first version of this script said "id", a column the table does not have — its dry run stopped on live
# before anything was read; backend/tests/test_reencrypt_dlp_audit_key.py now holds these to the model.)
KEY_COLUMN = "request_id"
SELECT_IDS = f"SELECT {KEY_COLUMN} FROM request_logs WHERE input_raw_encrypted IS NOT NULL ORDER BY created_at"
SELECT_ROWS = f"SELECT {KEY_COLUMN}, input_raw_encrypted FROM request_logs WHERE {KEY_COLUMN} = ANY(:ids)"
UPDATE_ROW = f"UPDATE request_logs SET input_raw_encrypted = :t WHERE {KEY_COLUMN} = :id"


def key_from(raw: str) -> bytes:
    """The 32-byte key a non-empty DLP_AUDIT_KEY stands for — the rule of dlp_audit_crypto._derive_key."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    raw = (raw or "").strip()
    if not raw:
        raise ValueError("an empty key")
    try:
        candidate = bytes.fromhex(raw)
    except ValueError:
        candidate = None
    if candidate is not None and len(candidate) == KEY_BYTES:
        return candidate
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=HKDF_INFO).derive(raw.encode("utf-8"))


def opens(key: bytes, token: str) -> str | None:
    """The plaintext of ``token`` under ``key``; None when it does not open."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        blob = base64.urlsafe_b64decode(token.encode("ascii"))
        return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], None).decode("utf-8")
    except Exception:  # noqa: BLE001 — any failure is "does not open"; the reason is never shown
        return None


def seal(key: bytes, plaintext: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(NONCE_BYTES)
    return base64.urlsafe_b64encode(nonce + AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)).decode("ascii")


def rewrite(rows: list[tuple[object, str]], old: bytes, new: bytes) -> tuple[list[tuple[object, str]], dict]:
    """(id, token) rows → the (id, new token) to write, and the counts. Nothing is written here."""
    out, n = [], {"already_new": 0, "to_rewrite": 0, "unreadable": 0, "empty": 0}
    for row_id, token in rows:
        if not token:
            n["empty"] += 1
        elif opens(new, token) is not None:
            n["already_new"] += 1
        else:
            plain = opens(old, token)
            if plain is None:
                n["unreadable"] += 1
            else:
                n["to_rewrite"] += 1
                out.append((row_id, seal(new, plain)))
    return out, n


def main(argv: list[str] | None = None) -> int:
    # allow_abbrev off: with it on, ``--old <key>`` was read as ``--old-env <key>`` — a key taken for a NAME,
    # and then printed in the message that asks for the variable to be set (the test found it).
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--apply", action="store_true", help="write (default: say what would be written)")
    ap.add_argument("--leave-unreadable", action="store_true", help="go on when rows open under neither key, leaving them")
    ap.add_argument("--old-env", default="DLP_AUDIT_KEY_OLD", help="NAME of the variable holding the old key")
    ap.add_argument("--new-env", default="DLP_AUDIT_KEY_NEW", help="NAME of the variable holding the new key")
    ap.add_argument("--batch", type=int, default=500)
    args, unknown = ap.parse_known_args(argv)
    if unknown:
        # (not echoed: the likeliest unknown argument is a key someone tried to pass)
        print("[audit-key] arguments not understood (not shown). A key is never given here: set it in the environment "
              "and pass the variable's NAME with --old-env / --new-env.", file=sys.stderr)
        return 2
    import re

    if not all(re.fullmatch(r"[A-Z_][A-Z0-9_]{2,63}", n) for n in (args.old_env, args.new_env)):
        # (a NAME is capitals, digits and underscores; anything else is not echoed — it may be a key)
        print("[audit-key] --old-env / --new-env take the NAME of an environment variable (what was given is not shown)", file=sys.stderr)
        return 2
    try:
        old, new = key_from(os.environ.get(args.old_env, "")), key_from(os.environ.get(args.new_env, ""))
    except ValueError:
        print(f"[audit-key] {args.old_env} and {args.new_env} must both be set in the environment (their values are never shown)", file=sys.stderr)
        return 2
    if old == new:
        print("[audit-key] the old and the new key are the same key: nothing to do", file=sys.stderr)
        return 2

    from sqlalchemy import text

    from app.core.db import SessionLocal

    session = SessionLocal()
    total = {"already_new": 0, "to_rewrite": 0, "unreadable": 0, "empty": 0}
    written = 0
    try:
        ids = [r[0] for r in session.execute(text(SELECT_IDS)).all()]
        # first pass: count, and stop before any write if something opens under neither key
        plans = []
        for i in range(0, len(ids), args.batch):
            chunk = ids[i:i + args.batch]
            rows = session.execute(text(SELECT_ROWS), {"ids": chunk}).all()
            updates, n = rewrite([(r[0], r[1]) for r in rows], old, new)
            for k in total:
                total[k] += n[k]
            plans.append(updates)
        print(f"[audit-key] rows with an encrypted prompt: {len(ids)} · already under the new key: {total['already_new']} · "
              f"to rewrite: {total['to_rewrite']} · open under neither key: {total['unreadable']}")
        if total["unreadable"] and not args.leave_unreadable:
            print("[audit-key] STOPPED: some rows open under neither key; nothing was written. Look at them, or pass --leave-unreadable.")
            return 3
        if not args.apply:
            print("[audit-key] dry run: nothing was written (pass --apply)")
            return 0
        for updates in plans:
            for row_id, token in updates:
                session.execute(text(UPDATE_ROW), {"t": token, "id": row_id})
                written += 1
            session.commit()
        print(f"[audit-key] rewritten: {written}")
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
