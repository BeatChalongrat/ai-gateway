"""Per-end-user rate-limit keying for SHARED API keys + image throttling.

Covers the rate-limit redesign for web-chat shared-key traffic:

  * a SHARED api_key carrying a TRUSTED ``X-User-ID`` is bucketed
    per end-user (``<key_id>:user:<uid>``), so 12 web-chat users no
    longer collide on one 60/min counter;
  * an external client SPOOFING ``X-User-ID`` (no internal secret) is
    ignored and stays on the shared ``key_id`` bucket;
  * JWT identity is untouched (already per-user);
  * the Growth-tier project/tenant ceilings are env-configurable;
  * image generation is throttled per-user (hour) + per-tenant (day),
    and spoofing cannot mint fresh image quota.

These exercise the PURE helpers + the limiter directly (no Rust sidecar,
no live Redis) so they're deterministic.
"""

from __future__ import annotations

import asyncio

import pytest

from app.api.deps import (
    ResolvedApiKey,
    _internal_signal_trusted,
    resolve_rate_limit_key_id,
)
from app.api.routes.public_images import _enforce_image_rate_limits
from app.core.config import settings
from app.core.exceptions import RateLimitExceededError
from app.services import rate_limit_service as rl
from app.services.rate_limit_service import RateLimitService


# ---------------------------------------------------------------------------
# Fake Redis (sliding-window ZSET ops only) — mirrors test_rate_limiter.py
# ---------------------------------------------------------------------------


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._ops: list = []

    def zremrangebyscore(self, key, minv, maxv):
        self._ops.append(("zrem", key, minv, maxv))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self):
        out = []
        for op in self._ops:
            if op[0] == "zrem":
                _, key, _mn, mx = op
                zset = self._redis.store.get(key, {})
                drop = [m for m, s in zset.items() if s <= mx]
                for m in drop:
                    del zset[m]
                out.append(len(drop))
            elif op[0] == "zcard":
                out.append(len(self._redis.store.get(op[1], {})))
            elif op[0] == "zadd":
                _, key, mapping = op
                zset = self._redis.store.setdefault(key, {})
                zset.update(mapping)
                out.append(len(mapping))
            elif op[0] == "expire":
                out.append(1)
        self._ops = []
        return out


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, float]] = {}

    def pipeline(self, transaction: bool = True):  # noqa: ARG002
        return _FakePipeline(self)


class _Orch:
    """Minimal stand-in for GatewayOrchestrator carrying the limiter."""

    def __init__(self, svc: RateLimitService) -> None:
        self.rate_limit_service = svc


def _auth(key_id: str, *, rate_limit_key_id: str | None, tenant_id: str = "t1"):
    return ResolvedApiKey(
        key_id=key_id,
        tenant_id=tenant_id,
        project_id="p1",
        key_prefix="gw_x",
        rate_limit_key_id=rate_limit_key_id,
    )


# ---------------------------------------------------------------------------
# 1. Shared web-chat API key → two users get separate buckets
# ---------------------------------------------------------------------------


class TestSharedKeyPerUser:
    def test_trusted_user_ids_get_distinct_keys(self, monkeypatch):
        monkeypatch.setattr(settings, "internal_service_secret", "s3cr3t")
        a = resolve_rate_limit_key_id(
            is_api_key=True,
            base_key_id="gw_shared",
            x_user_id="userA",
            x_internal_service_secret="s3cr3t",
        )
        b = resolve_rate_limit_key_id(
            is_api_key=True,
            base_key_id="gw_shared",
            x_user_id="userB",
            x_internal_service_secret="s3cr3t",
        )
        assert a == "gw_shared:user:userA"
        assert b == "gw_shared:user:userB"
        assert a != b

    def test_distinct_keys_yield_independent_counters(self):
        svc = RateLimitService(redis_client=_FakeRedis())
        # user A exhausts a 60/min bucket
        for _ in range(60):
            assert svc.check_key("gw_shared:user:A", limit=60)["allowed"] is True
        assert svc.check_key("gw_shared:user:A", limit=60)["allowed"] is False
        # user B, same shared key, is untouched
        assert svc.check_key("gw_shared:user:B", limit=60)["allowed"] is True


# ---------------------------------------------------------------------------
# 2. External spoofing — X-User-ID ignored without the internal secret
# ---------------------------------------------------------------------------


class TestSpoofingIgnored:
    def test_missing_secret_falls_back_to_key_id(self, monkeypatch):
        monkeypatch.setattr(settings, "internal_service_secret", "s3cr3t")
        k = resolve_rate_limit_key_id(
            is_api_key=True,
            base_key_id="gw_shared",
            x_user_id="attacker",
            x_internal_service_secret=None,
        )
        assert k == "gw_shared"

    def test_wrong_secret_falls_back_to_key_id(self, monkeypatch):
        monkeypatch.setattr(settings, "internal_service_secret", "s3cr3t")
        k = resolve_rate_limit_key_id(
            is_api_key=True,
            base_key_id="gw_shared",
            x_user_id="attacker",
            x_internal_service_secret="wrong-secret",
        )
        assert k == "gw_shared"

    def test_server_secret_unset_never_trusts(self, monkeypatch):
        monkeypatch.setattr(settings, "internal_service_secret", "")
        assert _internal_signal_trusted("anything") is False
        k = resolve_rate_limit_key_id(
            is_api_key=True,
            base_key_id="gw_shared",
            x_user_id="userA",
            x_internal_service_secret="anything",
        )
        assert k == "gw_shared"

    def test_normal_client_without_user_id_unchanged(self, monkeypatch):
        monkeypatch.setattr(settings, "internal_service_secret", "s3cr3t")
        k = resolve_rate_limit_key_id(
            is_api_key=True,
            base_key_id="gw_shared",
            x_user_id=None,
            x_internal_service_secret="s3cr3t",
        )
        assert k == "gw_shared"


# ---------------------------------------------------------------------------
# 3. JWT auth — identity stays jwt:<id>, X-User-ID never widens/narrows it
# ---------------------------------------------------------------------------


class TestJwtUnchanged:
    def test_jwt_key_unchanged_even_with_trusted_header(self, monkeypatch):
        monkeypatch.setattr(settings, "internal_service_secret", "s3cr3t")
        k = resolve_rate_limit_key_id(
            is_api_key=False,
            base_key_id="jwt:abc-123",
            x_user_id="someone-else",
            x_internal_service_secret="s3cr3t",
        )
        assert k == "jwt:abc-123"


# ---------------------------------------------------------------------------
# 4. Growth-tier limits are env-configurable with safe fallbacks
# ---------------------------------------------------------------------------


class TestConfigurableGrowthLimits:
    def test_env_values_applied(self, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_key_per_minute", 99)
        monkeypatch.setattr(settings, "rate_limit_project_per_minute", 5555)
        monkeypatch.setattr(settings, "rate_limit_tenant_per_minute", 7777)
        assert rl.key_limit() == 99
        assert rl.project_limit_default() == 5555
        assert rl.tenant_limit_default() == 7777

    def test_defaults_used_when_unset_or_invalid(self, monkeypatch):
        # A non-positive / unusable value falls back to the module default.
        monkeypatch.setattr(settings, "rate_limit_project_per_minute", 0)
        monkeypatch.setattr(settings, "rate_limit_tenant_per_minute", -1)
        assert rl.project_limit_default() == rl.DEFAULT_PROJECT_LIMIT
        assert rl.tenant_limit_default() == rl.DEFAULT_TENANT_LIMIT

    def test_check_all_enforces_configured_project_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_key_per_minute", 10_000)
        monkeypatch.setattr(settings, "rate_limit_project_per_minute", 3)
        monkeypatch.setattr(settings, "rate_limit_tenant_per_minute", 10_000)
        svc = RateLimitService(redis_client=_FakeRedis())
        for _ in range(3):
            assert svc.check_all("k", "proj", "ten")["allowed"] is True
        denied = svc.check_all("k", "proj", "ten")
        assert denied["allowed"] is False
        assert denied["scope"] == "project"
        assert denied["limit"] == 3


# ---------------------------------------------------------------------------
# 5. Image generation throttling (per-user hour + per-tenant day)
# ---------------------------------------------------------------------------


class TestImageRateLimit:
    def test_per_user_hourly_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "image_rate_limit_user_per_hour", 3)
        monkeypatch.setattr(settings, "image_rate_limit_tenant_per_day", 10_000)
        orch = _Orch(RateLimitService(redis_client=_FakeRedis()))
        auth = _auth("gw_shared", rate_limit_key_id="gw_shared:user:A")

        for _ in range(3):
            asyncio.run(_enforce_image_rate_limits(orch, auth))  # allowed
        with pytest.raises(RateLimitExceededError) as exc:
            asyncio.run(_enforce_image_rate_limits(orch, auth))
        assert exc.value.details["scope"] == "image_user"

    def test_per_tenant_daily_limit(self, monkeypatch):
        monkeypatch.setattr(settings, "image_rate_limit_user_per_hour", 10_000)
        monkeypatch.setattr(settings, "image_rate_limit_tenant_per_day", 2)
        orch = _Orch(RateLimitService(redis_client=_FakeRedis()))
        # Two DIFFERENT users, same tenant — the tenant/day cap binds.
        a = _auth("gw_shared", rate_limit_key_id="gw_shared:user:A", tenant_id="t9")
        b = _auth("gw_shared", rate_limit_key_id="gw_shared:user:B", tenant_id="t9")

        asyncio.run(_enforce_image_rate_limits(orch, a))
        asyncio.run(_enforce_image_rate_limits(orch, b))
        with pytest.raises(RateLimitExceededError) as exc:
            asyncio.run(_enforce_image_rate_limits(orch, a))
        assert exc.value.details["scope"] == "image_tenant"

    def test_spoofing_cannot_bypass_image_limit(self, monkeypatch):
        """Untrusted callers share one bucket — fake X-User-ID grants no quota.

        For untrusted requests ``rate_limit_key_id`` is None, so the helper
        falls back to ``key_id``: every spoofed user collapses onto the same
        per-user image counter and cannot mint fresh quota.
        """
        monkeypatch.setattr(settings, "image_rate_limit_user_per_hour", 2)
        monkeypatch.setattr(settings, "image_rate_limit_tenant_per_day", 10_000)
        orch = _Orch(RateLimitService(redis_client=_FakeRedis()))
        # Both requests are UNTRUSTED (rate_limit_key_id=None) → same key_id.
        spoof1 = _auth("gw_shared", rate_limit_key_id=None)
        spoof2 = _auth("gw_shared", rate_limit_key_id=None)

        asyncio.run(_enforce_image_rate_limits(orch, spoof1))
        asyncio.run(_enforce_image_rate_limits(orch, spoof2))
        # Third call (regardless of which spoofed identity) is throttled.
        with pytest.raises(RateLimitExceededError) as exc:
            asyncio.run(_enforce_image_rate_limits(orch, spoof1))
        assert exc.value.details["scope"] == "image_user"
