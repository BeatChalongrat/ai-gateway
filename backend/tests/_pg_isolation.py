"""FIX128 B — no unit test can reach a real Postgres.

The lane runs inside the live backend container, whose settings resolve to the
live databases. ``conftest.py`` already routes ``SessionLocal`` to the test
sqlite (autouse), but that is a redirect, not isolation: code that builds its
own engine or connection from the settings URL still reached the live server.
(The chat service had the same shape until FIX118/119: 2,582 synthetic tenants
and 3,145 conversations landed in the live chat DB, the last on 2026-09-14.)
FIX124 made Redis impossible to reach; this does the same for Postgres.

**Tripwire:** every psycopg3 connect — sync ``psycopg.connect`` /
``Connection.connect`` and async ``AsyncConnection.connect`` (the two ways
SQLAlchemy's psycopg dialect and any direct caller open a socket) — raises
:class:`RealPostgresInTests` before a socket is opened. sqlite is untouched.

``SRAN_ALLOW_REAL_POSTGRES_IN_TESTS=1`` lifts it, for the ``integration``
modules that exist to exercise a real database; the bare lane never sets it.
"""
from __future__ import annotations

import os

ALLOW_ENV = "SRAN_ALLOW_REAL_POSTGRES_IN_TESTS"

_installed = False


class RealPostgresInTests(RuntimeError):
    """A unit test tried to open a connection to a real Postgres server."""


def allowed() -> bool:
    return os.environ.get(ALLOW_ENV, "").strip() == "1"


def _refuse(*args, **kwargs):
    target = kwargs.get("host") or (args[0] if args and isinstance(args[0], str) else "")
    raise RealPostgresInTests(
        "a unit test tried to open a real Postgres connection"
        + (f" ({str(target)[:60]})" if target else "")
        + f" — tests use the sqlite fixtures; set {ALLOW_ENV}=1 only in the integration lane"
    )


async def _refuse_async(*args, **kwargs):
    _refuse(*args, **kwargs)


def install() -> bool:
    """Idempotent. Returns whether the tripwire is active."""
    global _installed
    if allowed():
        return False
    if _installed:
        return True
    try:
        import psycopg
    except ImportError:  # pragma: no cover — no driver, nothing can connect
        _installed = True
        return True
    psycopg.connect = _refuse  # type: ignore[assignment]
    psycopg.Connection.connect = classmethod(lambda cls, *a, **k: _refuse(*a, **k))  # type: ignore[method-assign]
    psycopg.AsyncConnection.connect = classmethod(  # type: ignore[method-assign]
        lambda cls, *a, **k: _refuse_async(*a, **k)
    )
    _installed = True
    return True
