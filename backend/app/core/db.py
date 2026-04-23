import logging
import os

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

logging.getLogger(__name__).info(
    "db_pool_configured pool_size=%d max_overflow=%d (defaults=%d/%d)",
    _pool_size,
    _max_overflow,
    _DEFAULT_POOL_SIZE,
    _DEFAULT_MAX_OVERFLOW,
)

engine = create_engine(
    settings.database_url,
    future=True,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_timeout=10,
    pool_recycle=300,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass
