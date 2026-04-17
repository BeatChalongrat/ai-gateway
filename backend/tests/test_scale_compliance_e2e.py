"""P10-09: End-to-end scale/compliance integration tests."""

import pytest

from app.services.cache_service import CacheService
from app.services.background_tasks import BackgroundTaskRunner
from app.services.retention_service import RetentionService
from app.services.audit_integrity import AuditIntegrityService, GENESIS_HASH
from app.services.region_service import get_region_constraints
from app.domain.identity import IdentityInfo, get_identity_providers
import app.models  # noqa: F401


class TestCacheIntegration:
    def test_cache_round_trip(self):
        from tests.test_cache_service import _FakeRedis

        svc = CacheService(redis_client=_FakeRedis())
        messages = [{"role": "user", "content": "hello"}]
        svc.set("gpt-4o-mini", messages, "p1", {"data": True})
        assert svc.get("gpt-4o-mini", messages, "p1") == {"data": True}


class TestBackgroundTaskIntegration:
    @pytest.mark.asyncio
    async def test_process_mixed_tasks(self):
        runner = BackgroundTaskRunner()
        results = []

        async def append(v):
            results.append(v)

        async def fail():
            raise RuntimeError("test")

        runner.enqueue(append, "a")
        runner.enqueue(fail)
        runner.enqueue(append, "b")
        await runner.process_all()
        assert results == ["a", "b"]
        assert runner.stats["failed"] == 1


class TestRetentionIntegration:
    def test_cleanup_on_empty_db(self, db_session):
        svc = RetentionService(request_log_days=1, audit_log_days=1)
        result = svc.run_all(db_session)
        assert result["request_logs_deleted"] == 0
        assert result["audit_logs_deleted"] == 0


class TestAuditIntegrityIntegration:
    def test_empty_chain_valid(self, db_session):
        svc = AuditIntegrityService()
        result = svc.verify_chain(db_session)
        assert result["valid"] is True

    def test_genesis_hash_is_64_zeros(self):
        assert len(GENESIS_HASH) == 64
        assert GENESIS_HASH == "0" * 64


class TestRegionIntegration:
    def test_eu_constraints_applied(self):
        c = get_region_constraints("eu")
        assert c.blocked_providers is not None

    def test_global_unconstrained(self):
        c = get_region_constraints("global")
        assert c.blocked_providers is None


class TestIdentityIntegration:
    def test_identity_info_serializable(self):
        info = IdentityInfo(
            provider="api_key", subject_id="k1", tenant_id="t1",
            roles=("admin",),
        )
        assert info.provider == "api_key"
        assert info.roles == ("admin",)

    def test_providers_registered(self):
        providers = get_identity_providers()
        assert len(providers) >= 1
