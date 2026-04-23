"""FastAPI dependency wiring for admin sessions + login rate limiter.

These functions are plain Python callables usable as ``Depends(...)``.
Tests override them via ``app.dependency_overrides`` to swap in a
fake-redis backed manager; production boots the real Redis client.
"""

from __future__ import annotations

import redis as redis_lib

from app.core.config import settings
from app.services.admin.login_service import LoginRateLimiter
from app.services.admin.session_manager import SessionManager


_session_manager: SessionManager | None = None
_rate_limiter: LoginRateLimiter | None = None


def _build_redis_client():
    return redis_lib.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
    )


def get_session_manager() -> SessionManager:
    """FastAPI dep: lazy singleton SessionManager wired to Redis."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager(
            redis_client=_build_redis_client(),
            signing_secrets=settings.session_signing_secrets_list,
            idle_ttl_seconds=settings.session_idle_ttl_seconds,
            absolute_ttl_seconds=settings.session_absolute_ttl_seconds,
        )
    return _session_manager


def get_login_rate_limiter() -> LoginRateLimiter:
    """FastAPI dep: lazy singleton LoginRateLimiter wired to Redis."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = LoginRateLimiter(
            redis_client=_build_redis_client(),
            per_email_limit=5,
            per_email_window_seconds=15 * 60,
            per_ip_limit=20,
            per_ip_window_seconds=60,
        )
    return _rate_limiter
