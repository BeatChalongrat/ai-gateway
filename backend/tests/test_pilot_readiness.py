"""PR-06: Pilot-readiness protection integration tests.

Validates all 5 pilot blockers are addressed end-to-end.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import ResolvedApiKey, check_tenant_access
from app.core.config import Settings
from app.core.exceptions import AuthorizationError
from app.main import app
from app.services.rate_limit_service import RateLimitService

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


class TestPR01TenantIsolation:
    def test_cross_tenant_access_denied(self):
        auth = ResolvedApiKey(
            key_id="k", tenant_id="t1", project_id="p",
            key_prefix="gw_", role="tenant_admin",
        )
        with pytest.raises(AuthorizationError):
            check_tenant_access(auth, "other-tenant")

    def test_same_tenant_allowed(self):
        auth = ResolvedApiKey(
            key_id="k", tenant_id="t1", project_id="p",
            key_prefix="gw_", role="tenant_admin",
        )
        check_tenant_access(auth, "t1")


class TestPR02RateLimiting:
    def test_rate_limit_enforced(self):
        # Use the in-memory fake redis from test_rate_limiter so this
        # pilot-readiness check stays hermetic.
        from tests.test_rate_limiter import _FakeRedis

        svc = RateLimitService(redis_client=_FakeRedis())
        assert svc.check_key("test-key", limit=2)["allowed"] is True
        assert svc.check_key("test-key", limit=2)["allowed"] is True
        assert svc.check_key("test-key", limit=2)["allowed"] is False


class TestPR03SecretValidation:
    def test_insecure_defaults_rejected_in_production(self):
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="production", jwt_secret="change-me",
        )
        with pytest.raises(RuntimeError, match="STARTUP BLOCKED"):
            s.validate_secrets()

    def test_dev_env_allows_defaults(self):
        s = Settings(
            _env_file=None, postgres_password="x",
            app_env="development", jwt_secret="change-me",
        )
        s.validate_secrets()  # passes


class TestPR04RegionEnforcement:
    def test_eu_blocks_openrouter_in_routing(self):
        from app.domain.entities import RequestContext
        from app.schemas.common import Message
        from app.schemas.public import ChatCompletionsRequest
        from app.services.routing_service import RoutingService

        ctx = RequestContext(
            tenant_id="t", project_id="p", project_name="P", region="eu"
        )
        payload = ChatCompletionsRequest(
            model="auto", messages=[Message(role="user", content="hi")]
        )
        route = RoutingService().select_route(ctx, payload, None)
        assert route.provider_name != "openrouter"


class TestPR05BodySizeLimit:
    def test_oversized_request_rejected(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={**_HEADERS, "Content-Length": "2000000"},
            content="x" * 100,
        )
        assert resp.status_code == 413

    def test_normal_request_passes(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
