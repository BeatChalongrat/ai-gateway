#!/usr/bin/env python3
"""AM7 — two admin-ui accounts on the TEST stack only: an admin and an
agent_publisher (the scoped role of phase 2). Their passwords are made here,
kept in scripts/probes/AM7/results/ts_admins.json (gitignored, test stack
only) and never printed. The live stack is never touched: the SQL goes to
the test stack's Postgres container, the hash is made in the test backend.

    python scripts/agent-market/seed_admins.py
"""
from __future__ import annotations

import importlib.util
import json
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("seed", ROOT / "scripts" / "test_stack_seed.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)

OUT = ROOT / "scripts" / "probes" / "AM7" / "results" / "ts_admins.json"
ACCOUNTS = [
    ("7a7a7a7a-0000-4000-8000-0000000000a1", "am7-it-admin", "am7-admin@fix128.test", "admin"),
    ("7a7a7a7a-0000-4000-8000-0000000000a2", "หัวหน้าบัญชี", "am7-publisher@fix128.test", "agent_publisher"),
    ("7a7a7a7a-0000-4000-8000-0000000000a3", "am7-viewer", "am7-viewer@fix128.test", "viewer"),
]


def main() -> int:
    kept = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    for uid, username, email, role in ACCOUNTS:
        pw = kept.get(email, {}).get("password") or secrets.token_urlsafe(18)
        r = subprocess.run(["docker", "exec", "-i", seed.TEST_BACKEND, "python", "-c",
                            "import bcrypt,sys; print(bcrypt.hashpw(sys.stdin.read().encode(), bcrypt.gensalt()).decode())"],
                           input=pw, capture_output=True, text=True, encoding="utf-8")
        hashed = r.stdout.strip()
        if r.returncode != 0 or not hashed.startswith("$2"):
            raise SystemExit("hashing in the test backend failed")
        seed.test_sql(f"""
          insert into admin_users (id, username, email, hashed_password, role, is_active, created_at, updated_at)
          values ('{uid}', '{username}', '{email}', '{hashed}', '{role}', true, now(), now())
          on conflict (id) do update set username = excluded.username, email = excluded.email,
              hashed_password = excluded.hashed_password, role = excluded.role, is_active = true, updated_at = now();
        """)
        kept[email] = {"id": uid, "username": username, "role": role, "password": pw}
        print(f"test stack admin-ui account: {email} role={role}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
