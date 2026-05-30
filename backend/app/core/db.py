import os
import sys

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings

# Defaults preserve the pre-Phase-2 production behaviour exactly
# (10 / 20). Env overrides are opt-in; unset env → identical to the
# previous hardcoded constants.
_DEFAULT_POOL_SIZE = 10
_DEFAULT_MAX_OVERFLOW = 20


def _env_int(name: str, default: int) -> int:
    """Read a non-negative int from env; fall back to default on absent /
    invalid values. Kept intentionally forgiving — a bad env string
    should not prevent the backend from starting."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        parsed = int(raw)
        if parsed < 0:
            return default
        return parsed
    except ValueError:
        return default


_pool_size = _env_int("DB_POOL_SIZE", _DEFAULT_POOL_SIZE)
_max_overflow = _env_int("DB_MAX_OVERFLOW", _DEFAULT_MAX_OVERFLOW)
_peak_per_worker = _pool_size + _max_overflow
# Display the Postgres max_connections value we expect on the other end
# of the wire so a misconfigured cap is obvious in the boot log.
# ``POSTGRES_MAX_CONNECTIONS`` is informational only — the value is
# enforced server-side by the postgres ``-c max_connections=…`` flag
# in docker-compose.yml.
_pg_max_connections = os.environ.get("POSTGRES_MAX_CONNECTIONS", "200")

# Use print() rather than logging — db.py is imported BEFORE
# configure_logging() runs in main.py, so a logging.info() call here
# is suppressed by Python's default root level (WARNING). Emitting via
# stdout guarantees the operator sees the effective pool settings on
# every boot. Tag matches the grep target documented in the runbook.
print(
    f"db_pool_configured pool_size={_pool_size} "
    f"max_overflow={_max_overflow} peak_per_worker={_peak_per_worker} "
    f"pg_max_connections={_pg_max_connections} "
    f"(defaults={_DEFAULT_POOL_SIZE}/{_DEFAULT_MAX_OVERFLOW})",
    file=sys.stderr,
    flush=True,
)

# Pin client_encoding=utf8 explicitly on Postgres. The postgres:16
# image we use defaults to UTF-8, so this is belt-and-braces — but
# making the contract explicit at the SQLAlchemy boundary survives a
# future psycopg release or a non-default image. SQLite (used by the
# in-memory test engine) doesn't accept this connect_arg, so we
# guard on the URL scheme.
_is_postgres = settings.database_url.startswith(
    ("postgres://", "postgresql://", "postgresql+")
)
_connect_args = {"client_encoding": "utf8"} if _is_postgres else {}

engine = create_engine(
    settings.database_url,
    future=True,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_timeout=10,
    pool_recycle=300,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Phase-2: async engine + session, ALONGSIDE the sync engine (strangler).
#
# The chat hot path converts to ``AsyncSession`` so a DB call no longer
# blocks the event loop on connection checkout under load (Phase-1
# benchmark: 40-concurrent sync checkout → 9.5s loop-block + pool-timeout
# 500s). Admin/portal CRUD keep the sync ``engine`` / ``SessionLocal``.
#
# Everything here is LAZY: the async engine is built on first use, so
# importing ``db`` never requires the async driver (asyncpg / aiosqlite).
# This keeps every existing sync-only code path and test import-safe.
# ---------------------------------------------------------------------------

_async_engine = None
_async_session_factory = None


def _to_async_url(url: str) -> str:
    """Map a sync DSN to its async-driver equivalent.

    postgresql:// → postgresql+asyncpg://   (production)
    sqlite://     → sqlite+aiosqlite://      (tests / dev)
    Already-async URLs pass through unchanged.
    """
    if "+asyncpg" in url or "+aiosqlite" in url:
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def get_async_engine():
    """Lazily build (and cache) the async engine. asyncpg/aiosqlite are
    only imported here, so the module stays importable without them."""
    global _async_engine
    if _async_engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine

        async_url = _to_async_url(settings.database_url)
        kwargs = {"future": True, "pool_pre_ping": True}
        if async_url.startswith("postgresql"):
            # Mirror the sync pool sizing for ANY async Postgres driver.
            # The project DSN is ``postgresql+psycopg://`` → psycopg3's
            # async mode (already a dependency), so no separate driver is
            # required; ``asyncpg`` (in requirements) is an optional faster
            # alternative if the DSN is switched to ``+asyncpg``.
            kwargs.update(
                pool_size=_pool_size,
                max_overflow=_max_overflow,
                pool_timeout=10,
                pool_recycle=300,
            )
        # SQLite async (tests) uses a NullPool implicitly; no pool args.
        _async_engine = create_async_engine(async_url, **kwargs)
    return _async_engine


def get_async_session_factory():
    """Lazily build (and cache) the AsyncSession factory.

    ``expire_on_commit=False`` so ORM attributes stay readable after a
    commit without triggering a (now-async) lazy refresh — important on
    the streaming path where the response outlives the commit.
    """
    global _async_session_factory
    if _async_session_factory is None:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        _async_session_factory = async_sessionmaker(
            get_async_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _async_session_factory


async def get_async_db():
    """FastAPI dependency: yield an ``AsyncSession`` for the chat hot path.

    Mirrors the sync ``get_db`` contract — one session per request, closed
    on completion. Use for ``/v1/chat/completions`` only; admin/portal
    keep ``get_db``.
    """
    factory = get_async_session_factory()
    async with factory() as session:
        yield session


async def dispose_async_engine() -> None:
    """Dispose the async engine on app shutdown (lifespan)."""
    global _async_engine
    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
