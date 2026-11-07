"""Admin login rate limiter.

Two sliding windows on Redis counters:

* ``login_attempts:<email>`` — N failed attempts per window blocks a
  specific account from logging in (defence against password
  guessing). Incremented on failure, cleared on success.
* ``admin_login_ip:<ip>`` — N total attempts per window blocks a
  source IP (defence against credential stuffing). Incremented on
  EVERY attempt, successful or not.

Both counters auto-expire after their window so no cleanup is needed.

The limiter never raises exceptions — callers are expected to compare
the returned ``allowed`` flag and decide whether to 429.

Redis outage (Phase 12 A4): login guards CREDENTIALS, so it must never
fail open — every outage minute would otherwise be a free brute-force
window. ``check`` falls back to the small in-process per-key cap shared
with the general limiter's conservative mode
(``rate_limit_service._conservative_allow``). This is NOT configurable
via ``RATE_LIMIT_REDIS_OUTAGE_MODE`` on purpose — that knob only
governs general traffic limiters (chat/image/media). The fallback is
per-worker (best-effort: N workers × cap), but it is bounded, which
fail-open is not; legitimate admins get ~10 attempts/minute during the
blip, so an outage still cannot lock a real operator out.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    scope: str | None  # "email" | "ip" | None
    retry_after_seconds: int


class LoginRateLimiter:
    def __init__(
        self,
        *,
        redis_client,
        per_email_limit: int,
        per_email_window_seconds: int,
        per_ip_limit: int,
        per_ip_window_seconds: int,
        email_key_prefix: str = "login_attempts:",
        ip_key_prefix: str = "admin_login_ip:",
    ) -> None:
        # Key prefixes default to the historical admin values so admin
        # behaviour (and any live Redis counters) are untouched. Other
        # credential surfaces (Phase 13 D: portal login/signup) pass
        # their own prefixes so admin + portal never share counters.
        self._redis = redis_client
        self._email_limit = per_email_limit
        self._email_window = per_email_window_seconds
        self._ip_limit = per_ip_limit
        self._ip_window = per_ip_window_seconds
        self._email_prefix = email_key_prefix
        self._ip_prefix = ip_key_prefix

    # ------------------------------------------------------------------
    # Pre-check — called before we run bcrypt, to short-circuit abuse.
    # ------------------------------------------------------------------

    def check(self, *, email: str, ip: str) -> RateLimitDecision:
        email_key = f"{self._email_prefix}{email.lower()}"
        ip_key = f"{self._ip_prefix}{ip}"

        try:
            email_count = int(self._redis.get(email_key) or 0)
            ip_count = int(self._redis.get(ip_key) or 0)
        except Exception:
            # Redis unreachable → conservative in-process cap, ALWAYS
            # (never fail-open; see module docstring — Phase 12 A4).
            return self._conservative_outage_check(
                email_key=email_key, ip_key=ip_key
            )

        if email_count >= self._email_limit:
            ttl = self._ttl(email_key, self._email_window)
            return RateLimitDecision(
                allowed=False, scope="email", retry_after_seconds=ttl
            )
        if ip_count >= self._ip_limit:
            ttl = self._ttl(ip_key, self._ip_window)
            return RateLimitDecision(
                allowed=False, scope="ip", retry_after_seconds=ttl
            )
        return RateLimitDecision(allowed=True, scope=None, retry_after_seconds=0)

    def _conservative_outage_check(
        self, *, email_key: str, ip_key: str
    ) -> RateLimitDecision:
        """Redis-outage fallback: bounded in-process cap, never fail-open.

        Counts THIS attempt against both scopes (``check`` is called
        exactly once per login attempt, so the count stays accurate
        while Redis is down). Uses the shared per-worker sliding window
        from ``rate_limit_service`` — ~10 attempts/minute per email and
        per IP — and meters denials on the same conservative-deny
        counter so an outage-time brute-force attempt is alertable.
        """
        from app.observability.metrics import (
            RATE_LIMIT_CONSERVATIVE_DENY_TOTAL,
        )
        from app.services.rate_limit_service import (
            _CONSERVATIVE_WINDOW,
            _conservative_allow,
        )

        email_ok = _conservative_allow(email_key)
        ip_ok = _conservative_allow(ip_key)
        if email_ok and ip_ok:
            return RateLimitDecision(
                allowed=True, scope=None, retry_after_seconds=0
            )
        try:
            RATE_LIMIT_CONSERVATIVE_DENY_TOTAL.inc()
        except Exception:  # noqa: BLE001 — metrics must never block auth
            pass
        return RateLimitDecision(
            allowed=False,
            scope="email" if not email_ok else "ip",
            retry_after_seconds=_CONSERVATIVE_WINDOW,
        )

    # ------------------------------------------------------------------
    # Counter updates
    # ------------------------------------------------------------------

    def record_failure(self, *, email: str, ip: str) -> None:
        self._incr_with_ttl(f"{self._email_prefix}{email.lower()}", self._email_window)
        self._incr_with_ttl(f"{self._ip_prefix}{ip}", self._ip_window)

    def record_success(self, *, email: str, ip: str) -> None:
        """Clear per-email counter; keep the per-IP counter running."""
        try:
            self._redis.delete(f"{self._email_prefix}{email.lower()}")
        except Exception:
            pass
        # IP counter is NOT cleared — a single successful login mustn't
        # reset abuse-detection for that IP.
        self._incr_with_ttl(f"{self._ip_prefix}{ip}", self._ip_window)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _incr_with_ttl(self, key: str, window: int) -> None:
        try:
            new_val = self._redis.incr(key)
            if new_val == 1:
                self._redis.expire(key, window)
        except Exception:
            pass

    def _ttl(self, key: str, fallback: int) -> int:
        try:
            ttl = int(self._redis.ttl(key))
            return ttl if ttl > 0 else fallback
        except Exception:
            return fallback
