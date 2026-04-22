"""Regression for P0-12: fresh-DB portal onboarding must not 500.

Phase 2-0 added migration ``027_seed_demo_tenant.py`` as a temporary
workaround. This test pins the contract: after ``alembic upgrade head``
on an empty database, a brand-new portal user can sign up and create a
project without hitting the ``IntegrityError → HTTP 500`` that the
Phase 1.4 live audit documented.

The test is skipped unless ``INTEGRATION_PG_URL`` points at a Postgres
admin connection (a superuser who can create / drop databases). A
typical local run:

    INTEGRATION_PG_URL=postgresql://postgres:postgres@localhost:5433 \
    pytest backend/tests/integration/test_fresh_db_onboarding.py -v

The test boots its own backend subprocess on a free port so it does not
interfere with any dev / audit backend already running.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
import pytest

ADMIN_PG_URL = os.environ.get("INTEGRATION_PG_URL")
BACKEND_DIR = Path(__file__).resolve().parents[2]  # .../backend


pytestmark = pytest.mark.skipif(
    not ADMIN_PG_URL,
    reason="Set INTEGRATION_PG_URL to run (Postgres admin conn for DB create/drop).",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pg_admin_params(url: str) -> dict:
    """Split admin URL into params for psycopg.connect (adds dbname='postgres')."""
    u = urlsplit(url)
    return dict(
        host=u.hostname or "localhost",
        port=u.port or 5432,
        user=u.username or "postgres",
        password=u.password or "",
        dbname="postgres",
    )


@pytest.fixture()
def fresh_db():
    """Create a throwaway database on the admin Postgres, yield its name, drop after."""
    name = f"aigw_onboard_{uuid.uuid4().hex[:8]}"
    params = _pg_admin_params(ADMIN_PG_URL)
    conn = psycopg.connect(**params, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
        yield name
    finally:
        with conn.cursor() as cur:
            # Evict stragglers from the backend we booted so DROP doesn't block.
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.close()


def _backend_env(db_name: str, port: int) -> dict:
    admin = _pg_admin_params(ADMIN_PG_URL)
    env = os.environ.copy()
    env.update(
        POSTGRES_HOST=admin["host"],
        POSTGRES_PORT=str(admin["port"]),
        POSTGRES_DB=db_name,
        POSTGRES_USER=admin["user"],
        POSTGRES_PASSWORD=admin["password"],
        # Tests don't use Redis; point at a closed port — rate-limit
        # middleware degrades gracefully and we don't hit it here.
        REDIS_HOST="127.0.0.1",
        REDIS_PORT="1",
        # Dev-env overrides so boot validation doesn't block on "insecure" keys.
        APP_ENV="development",
        APP_PORT=str(port),
        JWT_SECRET="integration-test-jwt-not-for-prod",
        MASTER_ENCRYPTION_KEY="integration-test-master-not-for-prod",
        PORTAL_JWT_SECRET="integration-test-portal-not-for-prod",
        PYTHONPATH=str(BACKEND_DIR),
    )
    return env


def _run(cmd: list[str], env: dict, cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(
            f"{' '.join(cmd)} failed (rc={result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _wait_healthy(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                return
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001 — broad catch is fine for polling
            last = repr(exc)
        time.sleep(0.25)
    pytest.fail(f"Backend at {url} never became healthy (last={last})")


def test_fresh_db_signup_then_create_project_does_not_500(fresh_db):
    """P0-12 regression: full onboarding path must succeed on a fresh DB."""
    port = _free_port()
    env = _backend_env(fresh_db, port)

    # 1. Migrate the fresh DB to head.
    _run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, cwd=BACKEND_DIR)

    # 2. Spot-check: migration 027 seeded demo-tenant-000.
    admin = _pg_admin_params(ADMIN_PG_URL)
    with psycopg.connect(**{**admin, "dbname": fresh_db}) as conn:
        count = conn.execute(
            "SELECT count(*) FROM tenants WHERE tenant_id = 'demo-tenant-000'"
        ).fetchone()[0]
        assert count == 1, "migration 027 did not seed demo-tenant-000"

    # 3. Boot backend subprocess.
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=BACKEND_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_healthy(f"{base}/health")

        # 4. Signup new portal user (random email).
        email = f"newbie-{uuid.uuid4().hex[:8]}@onboard.test"
        signup = httpx.post(
            f"{base}/portal/auth/signup",
            json={"email": email, "password": "OnboardPass123!", "display_name": "Newbie"},
            timeout=10.0,
        )
        assert signup.status_code == 200, signup.text
        token = signup.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # 5. Create first project — the exact path that 500'd pre-fix.
        create = httpx.post(
            f"{base}/portal/projects",
            json={"name": "first-project"},
            headers=hdr,
            timeout=10.0,
        )
        # The core assertion: no 500. A clean 2xx is the happy path;
        # a 4xx (validation) would indicate the endpoint changed, which
        # should also fail the test so someone revisits it.
        assert create.status_code < 500, (
            f"P0-12 regression: portal project create returned "
            f"{create.status_code} on a fresh DB — {create.text}"
        )
        assert create.status_code in (200, 201), create.text
        body = create.json()
        # The project should be persisted under demo-tenant-000 per P0-4.
        # (When P0-4 is fixed, this assertion also needs updating —
        # flag for PORTAL_TENANCY_MIGRATION_PLAN.md.)
        assert body.get("tenant_id") == "demo-tenant-000", body

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
