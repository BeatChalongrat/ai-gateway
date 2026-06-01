"""Shared test fixtures for the AI Gateway backend."""

import os
import tempfile

import httpx
import pytest
import pytest_asyncio
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
    config.addinivalue_line(
        "markers",
        "integration: tests that need a live environment the bare unit "
        "run doesn't provide — the configured DLP engine / Rust DLP "
        "sidecar, the NER sidecar, a real Postgres schema (vs the test "
        "sqlite), a gRPC rate-limiter, live provider keys, or the "
        "container's runtime .env. They are excluded from the unit lane: "
        "`pytest -m 'not integration'` is the gate that must stay green; "
        "`pytest -m integration` runs them against a full stack.",
    )


# C3 — modules that require a live environment (see the ``integration``
# marker doc above). Every test in these modules is auto-tagged
# ``integration`` by ``pytest_collection_modifyitems`` so the unit lane
# (`pytest -m "not integration"`) stays green. Kept as an explicit,
# reviewable all-list rather than a heuristic so adding a module to the
# integration lane is a deliberate, visible decision.
_INTEGRATION_MODULES = {
    # Live / end-to-end (need real keys or the full request pipeline)
    "test_api_e2e",
    "test_e2e_dlp_flow",
    "test_e2e_gateway_dlp",
    "test_error_contracts",
    "test_response_contracts",
    # DLP behavior — depends on the configured DLP engine (DLP_ENGINE=rust
    # in the container) / Rust sidecar, not the bare-python detector path.
    "test_admin_secure_trace",
    "test_block_path_persistence",
    "test_dlp_all_detectors",
    "test_dlp_config",
    "test_dlp_domain",
    "test_dlp_integration",
    "test_dlp_p0_fixes",
    "test_dlp_p0_live",
    "test_dlp_pipeline",
    "test_dlp_policy_runtime_privacy",
    "test_dlp_trace",
    "test_new_detectors",
    "test_privacy_rbac",
    # NER sidecar / Presidio
    "test_ner_detector",
    # Real Postgres schema vs the test sqlite
    "test_migrations",
    # gRPC rate-limiter sidecar
    "test_rate_limiter_client",
    # Container runtime .env overrides the asserted defaults
    "test_config",
    "test_secret_validation",
    # Streaming + model-registry integration (DB-backed registry / SSE)
    "test_streaming_moderation",
    "test_streaming_routing",
    "test_models_endpoint",
    "test_auth_dependency",
}


def pytest_collection_modifyitems(config, items):
    """Auto-tag environment-dependent modules + the ``tests/integration/``
    directory with the ``integration`` marker so the unit lane excludes
    them. See ``_INTEGRATION_MODULES`` for the rationale per module."""
    import pytest as _pytest

    for item in items:
        fspath = str(getattr(item, "fspath", "")).replace("\\", "/")
        module_name = item.module.__name__.rsplit(".", 1)[-1] if getattr(
            item, "module", None
        ) else ""
        if (
            "/tests/integration/" in fspath
            # The DLP pipeline suite exercises the configured DLP engine
            # (Rust sidecar in the container) end-to-end.
            or "/test_dlp_pipeline_suite/" in fspath
            or module_name in _INTEGRATION_MODULES
        ):
            item.add_marker(_pytest.mark.integration)


@pytest.fixture(autouse=True)
def _pseudonym_mode_off_by_default(monkeypatch):
    """Force ``DLP_PSEUDONYM_MODE=False`` for every test that doesn't
    explicitly opt in.

    Production default flipped to True in ``core/config.py`` so
    deployed gateways get readable ``<<CATEGORY_N>>`` pseudonyms out
    of the box. The pre-existing test suite, however, was written
    against the legacy ``<rN>`` shape — assertions pin exact tokens
    like ``<r0>``, counter sequences, span_map contents, and
    mask-restore round-trips that all assume the global counter scheme.

    Rather than rewrite ~50 tests (in ``test_dlp_counter_collision``,
    ``test_deterministic_tokenization``, ``test_dlp_detectors/``,
    etc.) each round the layer evolves, this autouse fixture pins the
    legacy behaviour for the suite. Tests that exercise the pseudonym
    path (``test_pseudonymization``, ``test_bare_name_masking``,
    ``test_thai_name_detection``) override via their own
    ``pseudonym_on`` fixture using the same monkeypatch mechanism.
    """
    try:
        from app.core.config import settings
    except Exception:
        return
    monkeypatch.setattr(settings, "dlp_pseudonym_mode", False, raising=False)
    yield


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

# Track A: the integration DB is now a per-run temp FILE (not :memory:) so
# the sync engine AND a parallel async engine (aiosqlite) can share the same
# data. After the AsyncSession flip, the chat hot path reads via the async
# engine — it must see rows written by sync test setup + the seed below.
# A file is the only reliable way to share one SQLite DB across the two
# drivers (sync pysqlite + async aiosqlite each open their own connection;
# committed writes are visible across connections). Per-run temp dir →
# fresh DB each pytest invocation (matches the old :memory: semantics).
_INTEGRATION_DB_FILE = os.path.join(
    tempfile.mkdtemp(prefix="ai_gw_test_"), "integration.db"
)
_INTEGRATION_DB_URL = f"sqlite:///{_INTEGRATION_DB_FILE}"

_integration_engine = create_engine(
    _INTEGRATION_DB_URL,
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
# Track A — async integration engine (aiosqlite) on the SAME sqlite file.
# After the AsyncSession flip the chat hot path depends on
# ``app.core.db.get_async_db``; route it (and any code that opens its own
# async session via ``get_async_session_factory`` — e.g. the streaming
# generator's post-stream persist) at the shared integration file so it
# sees the seed + any rows written by sync test setup. expire_on_commit=
# False mirrors prod so ORM attrs stay readable after commit (the streaming
# path reads them after the response starts).
# ---------------------------------------------------------------------------

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_async_integration_engine = create_async_engine(
    f"sqlite+aiosqlite:///{_INTEGRATION_DB_FILE}",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_AsyncIntegrationSession = async_sessionmaker(
    _async_integration_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def _integration_async_db_session():
    """Async mirror of ``_integration_db_session`` — yields an AsyncSession
    on the shared integration file, commit-on-success / rollback-on-error."""
    async with _AsyncIntegrationSession() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


from app.core.db import get_async_db  # noqa: E402
import app.core.db as _db_mod  # noqa: E402

main_app.dependency_overrides[get_async_db] = _integration_async_db_session
# Cover non-dependency async session opens (streaming generator post-stream
# persist) — they call get_async_session_factory() directly.
_db_mod.get_async_session_factory = lambda: _AsyncIntegrationSession


@pytest_asyncio.fixture
async def async_db():
    """Provide an AsyncSession on the shared integration DB for unit tests
    that exercise the new async service variants directly."""
    async with _AsyncIntegrationSession() as session:
        try:
            yield session
        finally:
            await session.rollback()


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


async def _stub_dispatch_with_fallback(
    self,
    route,
    payload,
    messages,
    *,
    api_key=None,
    key_resolver=None,
    invalidate_key=None,
    max_hops=2,
):
    """Stub for ``dispatch_with_fallback`` matching the production
    3-tuple return shape ``(response, used_provider, used_model)``.

    Delegates to ``self.dispatch`` (rather than calling
    ``_stub_dispatch`` directly) so that integration tests which
    monkeypatch ``ProviderDispatcher.dispatch`` on top of this autouse
    fixture still observe their spy / capture call. Fallback hops are
    not exercised by the autouse path — tests that want to verify
    fallback behaviour either drive the adapters directly or override
    this fixture (e.g. ``test_fallback_routing.py``).
    """
    resp = await self.dispatch(route, payload, messages, api_key=api_key)
    return resp, route.provider_name, route.model_name


@pytest.fixture(autouse=True)
def _mock_provider_dispatch(monkeypatch):
    """Auto-stub ProviderDispatcher.dispatch and dispatch_with_fallback
    for all tests.

    Adapter tests that use @respx.mock should also mark themselves with
    ``@pytest.mark.real_adapter`` or simply call the adapter directly
    (not through the dispatcher).
    """
    from app.services.provider_dispatcher import ProviderDispatcher
    monkeypatch.setattr(ProviderDispatcher, "dispatch", _stub_dispatch)
    monkeypatch.setattr(
        ProviderDispatcher,
        "dispatch_with_fallback",
        _stub_dispatch_with_fallback,
    )


@pytest.fixture(autouse=True)
def _mock_provider_key_service(request, monkeypatch):
    """Auto-stub ProviderKeyService.get_api_key so integration tests
    never fail with 'No API key configured'.

    Exempt the dedicated key-service unit tests — they exercise the REAL
    ``get_api_key`` / ``get_api_key_async`` (round-robin, cache,
    placeholder filtering), so the stub would shadow exactly what they
    assert. Same nodeid-exemption pattern the cache / rate-limiter
    autouse fixtures use."""
    if "test_async_provider_key_service" in request.node.nodeid or (
        "test_provider_key" in request.node.nodeid
    ):
        return
    from app.services.provider_key_service import ProviderKeyService
    monkeypatch.setattr(
        ProviderKeyService, "get_api_key", lambda self, session, provider: "test-stub-key"
    )

    # Track A: the chat hot path now calls the async variant. Stub it too,
    # or the flipped handler resolves None → ProviderUnavailableError (503).
    async def _get_api_key_async_stub(self, session, provider_name):
        return "test-stub-key"

    monkeypatch.setattr(
        ProviderKeyService, "get_api_key_async", _get_api_key_async_stub
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
def _reset_phase1_inmemory_caches():
    """Clear the Phase-1 per-process hot-path caches between tests.

    B5/B7/B8 added short-TTL in-memory caches (provider keys, keyword
    rules, last_used_at debounce). Production busts them via the admin
    endpoints, but tests insert rows directly through the ORM, so a stale
    cached result would otherwise leak across — clear them per test.
    """
    import app.services.api_key_service as _aks
    import app.services.keyword_rule_service as _krs
    import app.services.provider_key_service as _pks

    for mod, attr in (
        (_krs, "_rules_cache"),
        (_pks, "_keys_cache"),
        (_aks, "_last_used_writes"),
    ):
        getattr(mod, attr).clear()
    yield
    for mod, attr in (
        (_krs, "_rules_cache"),
        (_pks, "_keys_cache"),
        (_aks, "_last_used_writes"),
    ):
        getattr(mod, attr).clear()


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
