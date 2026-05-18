"""Shared test fixtures for the AI Gateway backend."""

import httpx
import pytest
import respx
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def pytest_configure(config):
    """Register custom markers so `pytest --strict-markers` works."""
    config.addinivalue_line(
        "markers",
        "ner: tests that exercise the Presidio NER detector. Use "
        "`pytest -m 'not ner'` to skip them, `pytest -m ner` to run "
        "only them. The tests mock the analyzer so they pass on any "
        "Python version — the marker is for CI selection, not a "
        "presidio install gate.",
    )

from fastapi import Header

from app.api.deps import (
    ResolvedApiKey,
    get_db_session,
    get_resolved_api_key,
    require_admin,
    require_admin_session,
)
from app.core.db import Base
from app.main import app as main_app
from app.models.project import ProjectORM
from app.models.tenant import TenantORM
import app.models  # noqa: F401 — ensure all ORM models register with Base.metadata


# ---------------------------------------------------------------------------
# Shared in-memory DB for integration tests using the main FastAPI app.
# ---------------------------------------------------------------------------

_integration_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_integration_engine)

# Seed the integration DB with a test tenant + project matching MOCK_AUTH.
_IntegrationSession = sessionmaker(bind=_integration_engine)
_seed = _IntegrationSession()
_seed.add(TenantORM(tenant_id="test-tenant", name="Test Tenant", slug="test-tenant"))
_seed.flush()
_seed.add(
    ProjectORM(
        project_id="test-project",
        tenant_id="test-tenant",
        name="Test Project",
        policy_profile_id="default",
    )
)
_seed.commit()
_seed.close()


# ---------------------------------------------------------------------------
# Mock auth for integration tests using the main FastAPI app.
# ---------------------------------------------------------------------------

MOCK_AUTH = ResolvedApiKey(
    key_id="test-key-id",
    tenant_id="test-tenant",
    project_id="test-project",
    key_prefix="gw_test_",
    role="admin",
)


async def mock_resolved_api_key(
    authorization: str | None = Header(default=None),
) -> ResolvedApiKey:
    """Mock auth: still requires a Bearer header, but skips DB validation."""
    from app.core.exceptions import AuthenticationError

    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid authorization header")
    return MOCK_AUTH


def _integration_db_session():
    """Yield a session from the shared integration engine."""
    session = _IntegrationSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


main_app.dependency_overrides[get_resolved_api_key] = mock_resolved_api_key
main_app.dependency_overrides[require_admin] = lambda: MOCK_AUTH
# Phase 2B Commit 9: admin routers now use require_admin_session
# (cookie-only, Bearer-rejecting). Tests that drive the admin router
# via MOCK_AUTH go through this override instead.
main_app.dependency_overrides[require_admin_session] = lambda: MOCK_AUTH
main_app.dependency_overrides[get_db_session] = _integration_db_session


# ---------------------------------------------------------------------------
# Phase 2B Commit 10 — CSRF middleware is enforced by default but
# existing tests drive admin routes via MOCK_AUTH without CSRF
# cookies. Flip the process-wide bypass flag so the main-app TestClient
# works. Dedicated CSRF tests in ``test_csrf_middleware.py`` build
# their own FastAPI app and don't rely on this flag — the middleware
# they add is enforced normally.
# ---------------------------------------------------------------------------

from app.middleware import csrf as _csrf_mod
_csrf_mod._BYPASS_FOR_TESTING = True


# ---------------------------------------------------------------------------
# Global provider stub for integration tests.
# Monkeypatches ProviderDispatcher.dispatch so tests never hit real APIs.
# Adapter-level tests use their own @respx.mock for HTTP-level mocking.
# ---------------------------------------------------------------------------

from app.domain.providers import ProviderResponse as _PR


async def _stub_dispatch(self, route, payload, messages, api_key=None):
    """Return a stub ProviderResponse for integration tests."""
    last_content = messages[-1].content if messages else ""
    return _PR(
        content=f"[{route.provider_name} stub] {last_content[:200]}",
        model=route.model_name,
    )


@pytest.fixture(autouse=True)
def _mock_provider_dispatch(monkeypatch):
    """Auto-stub ProviderDispatcher.dispatch for all tests.

    Adapter tests that use @respx.mock should also mark themselves with
    ``@pytest.mark.real_adapter`` or simply call the adapter directly
    (not through the dispatcher).
    """
    from app.services.provider_dispatcher import ProviderDispatcher
    monkeypatch.setattr(ProviderDispatcher, "dispatch", _stub_dispatch)


@pytest.fixture(autouse=True)
def _mock_provider_key_service(monkeypatch):
    """Auto-stub ProviderKeyService.get_api_key so integration tests
    never fail with 'No API key configured'."""
    from app.services.provider_key_service import ProviderKeyService
    monkeypatch.setattr(
        ProviderKeyService, "get_api_key", lambda self, session, provider: "test-stub-key"
    )


@pytest.fixture(autouse=True)
def _mock_adapter_stream_chat(monkeypatch):
    """Auto-stub ``adapter.stream_chat`` for OpenAI + Anthropic adapters.

    Before this fixture existed, streaming tests relied on individual
    monkeypatches of ``OpenAIAdapter.stream_chat``. ``AnthropicAdapter``
    had no stream_chat override at all, so the default ``raise
    NotImplementedError`` triggered a fallback to non-streaming
    (which was stubbed via ``_mock_provider_dispatch``). When
    Anthropic gained a real ``stream_chat`` method, any test that sent
    ``stream=true`` with a Claude model would reach the real adapter
    and call ``https://api.anthropic.com/v1/messages`` over the
    network — a production credential leak and flaky test.

    The safe default: both streaming adapters yield a single canned
    chunk. Tests that need specific output override via their own
    ``monkeypatch.setattr`` — that stacks on top of this one.
    """
    from app.domain.providers import ProviderStreamChunk
    from app.services.providers.anthropic_adapter import AnthropicAdapter
    from app.services.providers.openai_adapter import OpenAIAdapter

    async def _stub_stream_chat(
        self, payload, messages, *, resolved_model=None, api_key=None
    ):
        yield ProviderStreamChunk(
            delta=f"[{self.provider_name} stub stream]",
            finish_reason="stop",
            model=resolved_model or "stub",
        )

    monkeypatch.setattr(AnthropicAdapter, "stream_chat", _stub_stream_chat)
    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _stub_stream_chat)


@pytest.fixture(autouse=True)
def _isolate_session_local_to_integration_engine(monkeypatch):
    """Route every ``SessionLocal()`` call to the in-memory integration DB.

    The FastAPI dep override at line 109 covers the main request path
    (``Depends(get_db_session)``). It does NOT cover code that opens
    its own session via ``from app.core.db import SessionLocal`` —
    notably ``public_chat.py`` streaming ``event_generator()``, which
    opens a fresh session AFTER the dep cleanup runs (the SSE handler
    returns control to FastAPI as soon as it yields the
    ``StreamingResponse``, and the dep cleanup closes the dep session
    immediately — so any post-stream persist must use a fresh
    ``SessionLocal()``). Without this fixture those persist calls
    write to the live Postgres dev DB instead of the test DB,
    producing rows like ``tenant_id='test-tenant'`` in the operator's
    dashboard.

    Three pre-existing tests (``test_dlp_trace.py``,
    ``test_policy_override.py``, ``test_streaming_routing.py``) already
    monkeypatch this locally; this fixture hoists the same fix to the
    whole suite so future streaming tests don't have to remember it.
    Local monkeypatches in those tests still stack on top of this
    one — pytest's monkeypatch is LIFO per fixture.

    Kept as defence-in-depth after known-issues #6 was resolved
    (commit 216b54e).  The streaming path now captures the factory
    at route entry (``chat_completions`` binds ``log_session_factory
    = SessionLocal``) instead of importing it inside the generator,
    but the patched attribute is still what the route handler reads
    per request.  ``test_streaming_session_factory.py`` builds on
    this fixture by overriding the patched attribute mid-test to
    prove the per-request capture invariant.
    """
    integration_sessionmaker = sessionmaker(
        bind=_integration_engine, autoflush=False, autocommit=False
    )
    monkeypatch.setattr("app.core.db.SessionLocal", integration_sessionmaker)


@pytest.fixture(autouse=True)
def _isolate_stateful_redis_services(request, monkeypatch):
    """Neutralize Redis-backed gateway services for tests.

    - Rate limiter: always-allow (tests would otherwise exhaust the 60/min
      per-key budget after ~60 integration cases since Redis state persists
      across test runs).
    - Response cache: never hit (a response cached by one test would leak
      its policy_action / content into a later test with the same prompt).

    Tests that validate the services themselves (``test_rate_limiter.py``,
    ``test_cache_service.py``) are exempted — they inject fake Redis clients.
    """
    nodeid = request.node.nodeid

    if "test_rate_limiter" not in nodeid:
        from app.services.rate_limit_service import RateLimitService

        def _allow_all(self, *args, **kwargs):
            return {
                "allowed": True,
                "scope": None,
                "remaining": 999,
                "reset_at": 0,
                "limit": 999,
            }

        async def _allow_all_async(self, *args, **kwargs):
            return _allow_all(self, *args, **kwargs)

        monkeypatch.setattr(RateLimitService, "check_all", _allow_all)
        # May 2026 Redis async refactor — the orchestrator's hot path
        # now calls ``check_all_async``. Override both names so the
        # rate limiter is neutralised regardless of which caller a
        # test exercises.
        monkeypatch.setattr(
            RateLimitService, "check_all_async", _allow_all_async
        )

    # Exempt dedicated cache tests and the scale integration round-trip,
    # both of which inject their own fake Redis and need the real methods.
    if "test_cache_service" not in nodeid and "TestCacheIntegration" not in nodeid:
        from app.services.cache_service import CacheService

        async def _async_none(self, *args, **kwargs):
            return None

        async def _async_noop(self, *args, **kwargs):
            return None

        monkeypatch.setattr(CacheService, "get", lambda self, *args, **kwargs: None)
        monkeypatch.setattr(CacheService, "set", lambda self, *args, **kwargs: None)
        # Mirror the async API patched by the May 2026 refactor.
        monkeypatch.setattr(CacheService, "get_async", _async_none)
        monkeypatch.setattr(CacheService, "set_async", _async_noop)


@pytest.fixture()
def sqlite_engine():
    """Create an in-memory SQLite engine with all tables from ORM metadata.

    Uses StaticPool so all connections (including cross-thread from
    TestClient) share the same in-memory database.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db_session(sqlite_engine):
    """Provide a transactional DB session that rolls back after each test."""
    Session = sessionmaker(bind=sqlite_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def db_inspector(sqlite_engine):
    """SQLAlchemy Inspector bound to the test engine."""
    return inspect(sqlite_engine)
