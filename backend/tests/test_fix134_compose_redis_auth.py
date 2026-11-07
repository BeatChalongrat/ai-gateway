"""FIX134 — every consumer of the password-protected Redis sends the password.

Redis runs with ``--requirepass`` since the port-close round; the Go gateway's
``REDIS_URL`` was left as ``redis://redis:6379/0``. It logged ``NOAUTH
Authentication required`` once at start ("auth cache disabled until it
recovers") and then resolved every API key from Postgres, silently, for as
long as it ran (found on live 2026-09-25). This test reads the compose file
and refuses any Redis URL for the ``redis`` service without the password.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[1]
COMPOSE = next((p for p in (BACKEND.parent / "docker-compose.yml", Path("/docker-compose.yml")) if p.is_file()), None)

_REDIS_URL = re.compile(r"^rediss?://(?P<auth>[^@/]*@)?(?P<host>[A-Za-z0-9_.-]+)(:\d+)?(/\d+)?$")


def _env_items(service: dict):
    env = service.get("environment") or {}
    if isinstance(env, dict):
        yield from env.items()
    else:
        for item in env:
            k, _, v = str(item).partition("=")
            yield k, v


@pytest.mark.skipif(COMPOSE is None, reason="docker-compose.yml is not reachable from this lane")
def test_every_url_to_the_protected_redis_carries_the_password():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert "--requirepass" in str(services["redis"].get("command")), "the redis service no longer requires a password"
    offenders, checked = [], 0
    for name, svc in services.items():
        items = list(_env_items(svc or {}))
        # redis_exporter's convention: the address in REDIS_ADDR, the password in REDIS_PASSWORD
        separate_password = any(k == "REDIS_PASSWORD" and "${REDIS_PASSWORD" in str(v) for k, v in items)
        for key, value in items:
            m = _REDIS_URL.match(str(value or "").strip().strip('"'))
            if not m or m.group("host") != "redis":
                continue
            checked += 1
            if "${REDIS_PASSWORD" not in (m.group("auth") or "") and not separate_password:
                offenders.append(f"{name}.{key}={value}")
    assert checked >= 2, f"expected several Redis URLs in the compose file, found {checked}"
    assert not offenders, f"Redis URLs without the password (NOAUTH at runtime): {offenders}"
