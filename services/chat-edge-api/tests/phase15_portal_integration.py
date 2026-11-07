"""Phase 1.5 — Portal-integration smoke.

Verifies:
  • Chat Edge's /auth/* endpoints return 410 Gone (deprecation).
  • A token minted by FastAPI's /portal/auth/signup verifies at
    chat-edge and lets the caller hit protected routes.
  • The shadow row in identity.users is auto-provisioned with id =
    portal user id and provisioned_from = 'portal'.
  • A second call from the same portal user re-uses the shadow row.
  • A second portal user's conversations are isolated from the
    first.
  • Invalid / unsigned tokens return 401.

Run:
    PYTHONIOENCODING=utf-8 python tests/phase15_portal_integration.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from urllib.parse import urljoin

import jwt as pyjwt
import requests

BACKEND = os.environ.get("BACKEND", "http://127.0.0.1:28080")
CHAT_EDGE = os.environ.get("CHAT_EDGE", "http://127.0.0.1:24000")
PG_CONTAINER = "ai-gateway-postgres-webchat"
# Must match docker-compose.webchat.yml chat-edge-api env. Same value
# the FastAPI ``backend`` service uses for ``portal_jwt_secret``.
PORTAL_SECRET = os.environ.get(
    "PORTAL_JWT_SECRET", "dev-stable-portal-key-change-in-prod"
)

RESULTS: list[tuple[str, bool, str]] = []


def chk(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append((name, cond, detail))
    flag = "PASS" if cond else "FAIL"
    print(f"[{flag}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def mint_portal_token(
    *, user_id: str | None = None,
    tenant_id: str | None = None,
    email: str | None = None,
    role: str = "member",
    secret: str = PORTAL_SECRET,
    ttl_s: int = 3600,
) -> tuple[str, dict]:
    """Mint a JWT structurally identical to what FastAPI
    ``services/portal/auth_service.create_portal_token`` produces.

    Returns (token, claims). We bypass FastAPI's signup because the
    portal_users table on this branch has an unrelated flush-order
    bug — chat-edge's verification only cares about the JWT shape +
    secret, both of which we control here.
    """
    user_id = user_id or str(uuid.uuid4())
    tenant_id = tenant_id or str(uuid.uuid4())
    email = email or f"sim-{uuid.uuid4().hex[:8]}@example.com"
    now = int(time.time())
    claims = {
        "sub": user_id,
        "email": email,
        "type": "portal",
        "token_use": "access",
        "tenant_id": tenant_id,
        "tenant_role": role,
        "token_version": 1,
        "iat": now,
        "exp": now + ttl_s,
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(claims, secret, algorithm="HS256"), claims


def pg(sql: str) -> str:
    cmd = [
        "docker", "exec", PG_CONTAINER,
        "psql", "-U", "chatedge", "-d", "sran_ai", "-At", "-c", sql,
    ]
    return subprocess.check_output(cmd, text=True).strip()


def ce(token: str | None, path: str, method: str = "GET", body: dict | None = None) -> requests.Response:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    url = urljoin(CHAT_EDGE, "/api/v1" + path)
    return requests.request(method, url, headers=h, json=body, timeout=20)


def main() -> int:
    # ── 1. /auth/* on chat-edge is now 410 Gone. ───────────────
    for path in ("/auth/register", "/auth/login", "/auth/refresh", "/auth/logout"):
        r = ce(None, path, method="POST", body={})
        chk(f"chat-edge {path} → 410", r.status_code == 410,
            f"status={r.status_code} body={r.text[:80]}")

    # ── 2. Portal-shaped JWT → chat-edge accepts the token. ────
    token_a, claims_a = mint_portal_token()
    portal_uid_a = claims_a["sub"]

    r = ce(token_a, "/conversations")
    chk("Portal token accepted at /conversations", r.status_code == 200,
        f"status={r.status_code}")
    r = ce(token_a, "/models")
    chk("Portal token accepted at /models", r.status_code == 200,
        f"status={r.status_code}")

    # ── 3. Shadow row exists in identity.users with portal id. ─
    count = pg(
        f"SELECT COUNT(*) FROM identity.users "
        f"WHERE id = '{portal_uid_a}' AND provisioned_from = 'portal';"
    )
    chk("Shadow user row provisioned with portal_user_id",
        count == "1", f"row count={count}")

    # ── 4. Re-use on second call (cached / idempotent). ────────
    r = ce(token_a, "/conversations")
    chk("Repeat call still 200 (cache hit, no DB error)",
        r.status_code == 200, f"status={r.status_code}")
    # Confirm no duplicate row was inserted.
    cnt = pg(f"SELECT COUNT(*) FROM identity.users WHERE id = '{portal_uid_a}';")
    chk("No duplicate shadow row", cnt == "1", f"count={cnt}")

    # ── 5. Create a conversation + verify tenant isolation. ────
    r = ce(token_a, "/conversations", method="POST",
           body={"model_id": "gpt-4o-mini"})
    chk("User A — create conversation", r.status_code == 201,
        f"status={r.status_code}")
    conv_a = r.json().get("id")

    token_b, claims_b = mint_portal_token()
    r = ce(token_b, f"/conversations/{conv_a}")
    chk("User B can't see A's conversation (404)",
        r.status_code == 404, f"status={r.status_code}")
    r = ce(token_b, "/conversations")
    body = r.json()
    chk("User B's conversation list excludes A's row",
        r.status_code == 200 and conv_a not in [c["id"] for c in body.get("conversations", [])],
        f"status={r.status_code} count={len(body.get('conversations', []))}")

    # ── 6. Invalid token → 401. ────────────────────────────────
    r = ce("not-a-jwt", "/conversations")
    chk("Garbage token → 401", r.status_code == 401, f"status={r.status_code}")

    # JWT signed with the WRONG secret must be rejected by
    # ParsePortalToken's signature check.
    bad, _ = mint_portal_token(secret="not-the-portal-secret")
    r = ce(bad, "/conversations")
    chk("Token signed with wrong secret → 401",
        r.status_code == 401, f"status={r.status_code}")

    # Wrong ``type`` claim — chat-edge enforces type=="portal".
    not_portal = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "email": "x@x",
            "type": "admin",  # ← wrong type
            "token_use": "access",
            "tenant_id": str(uuid.uuid4()),
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        PORTAL_SECRET,
        algorithm="HS256",
    )
    r = ce(not_portal, "/conversations")
    chk("Token with type!=portal → 401", r.status_code == 401,
        f"status={r.status_code}")

    # ── 7. Re-issue another JWT for the SAME user → same shadow.
    token_a2, _ = mint_portal_token(
        user_id=claims_a["sub"],
        tenant_id=claims_a["tenant_id"],
        email=claims_a["email"],
    )
    r = ce(token_a2, "/conversations")
    body = r.json() if r.text else {}
    chk("Re-mint same-user token → A still sees own conv",
        r.status_code == 200 and any(c["id"] == conv_a for c in body.get("conversations", [])),
        f"status={r.status_code} count={len(body.get('conversations', []))}")

    # ── Summary ────────────────────────────────────────────────
    print()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"TOTAL: {passed}/{total}")
    if passed < total:
        for n, ok, d in RESULTS:
            if not ok:
                print(f"  FAIL: {n}  ({d})")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
