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
the returned ``allowed`` flag and decide whether to 429. A Redis
outage degrades to "allow" because the password check is still the
backstop; failing closed on Redis would turn a Redis blip into an
admin lockout.
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
    ) -> None:
        self._redis = redis_client
        self._email_limit = per_email_limit
        self._email_window = per_email_window_seconds
        self._ip_limit = per_ip_limit
        self._ip_window = per_ip_window_seconds

    # ------------------------------------------------------------------
    # Pre-check — called before we run bcrypt, to short-circuit abuse.
    # ------------------------------------------------------------------

    def check(self, *, email: str, ip: str) -> RateLimitDecision:
        email_key = f"login_attempts:{email.lower()}"
        ip_key = f"admin_login_ip:{ip}"

        try:
            email_count = int(self._redis.get(email_key) or 0)
            ip_count = int(self._redis.get(ip_key) or 0)
        except Exception:
            return RateLimitDecision(allowed=True, scope=None, retry_after_seconds=0)

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

    # ------------------------------------------------------------------
    # Counter updates
    # ------------------------------------------------------------------

    def record_failure(self, *, email: str, ip: str) -> None:
        self._incr_with_ttl(f"login_attempts:{email.lower()}", self._email_window)
        self._incr_with_ttl(f"admin_login_ip:{ip}", self._ip_window)

    def record_success(self, *, email: str, ip: str) -> None:
        """Clear per-email counter; keep the per-IP counter running."""
        try:
            self._redis.delete(f"login_attempts:{email.lower()}")
        except Exception:
            pass
        # IP counter is NOT cleared — a single successful login mustn't
        # reset abuse-detection for that IP.
        self._incr_with_ttl(f"admin_login_ip:{ip}", self._ip_window)

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
