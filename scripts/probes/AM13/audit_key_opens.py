"""Run INSIDE the live backend: does the application itself — its own decrypt, the key it was started
with — open the stored prompts? Prints counts only: no prompt, no key.

    docker exec -i ai-gateway-backend-1 python - < scripts/probes/AM13/audit_key_opens.py
"""
from sqlalchemy import text

from app.core import dlp_audit_crypto
from app.core.db import SessionLocal

s = SessionLocal()
try:
    for which, order in (("oldest", "asc"), ("newest", "desc")):
        rows = s.execute(text(f"SELECT input_raw_encrypted, created_at FROM request_logs WHERE input_raw_encrypted IS NOT NULL ORDER BY created_at {order} LIMIT 200")).all()
        opened = 0
        for token, _ in rows:
            try:
                opened += 1 if dlp_audit_crypto.decrypt(token) is not None else 0
            except Exception:  # noqa: BLE001 — a row that does not open is what is being counted
                pass
        print(f"the application opens {opened} of the {len(rows)} {which} stored prompts (to {rows[-1][1]:%Y-%m-%d %H:%M})")
finally:
    s.close()
