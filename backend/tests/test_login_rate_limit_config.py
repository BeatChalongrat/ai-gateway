"""Phase 2C — Commit 16: login rate-limit configuration verification.

Pins the production defaults so a careless future edit doesn't loosen
the brute-force gate without anyone noticing. Per Beat's P1 locked
spec: 5 attempts per 15 min per email AND 5 attempts per 15 min
per IP.
"""

from __future__ import annotations

from app.services.admin import session_service


def test_production_login_rate_limits_are_tight():
    # Reset any existing cached singleton so we see the current defaults.
    session_service._rate_limiter = None
    rl = session_service.get_login_rate_limiter()
    try:
        assert rl._email_limit == 5
        assert rl._email_window == 15 * 60
        assert rl._ip_limit == 5, "per-IP must be 5 per 15 min (Beat P1 spec)"
        assert rl._ip_window == 15 * 60
    finally:
        session_service._rate_limiter = None
