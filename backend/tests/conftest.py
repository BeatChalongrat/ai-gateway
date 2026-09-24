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

# FIX124 — before anything imports the app: every Redis client a test can
# build lands on an in-process fake, and a real connection refuses to open.
# The lane's settings resolve to the RUNNING stack's Redis; a test once left
# a 15-minute health marker there that the live gateway honoured.
from tests import _redis_isolation

_redis_isolation.install()

# FIX128 B — the same for Postgres: a unit test that opens a real Postgres
# connection (the live DB, from the container's settings) fails instead of
# writing. The sqlite fixtures are untouched.
from tests import _pg_isolation

_pg_isolation.install()


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
    config.addinivalue_line(
        "markers",
        "quarantine(reason, ticket): FIX126 O8 — a test that fails for a "
        "known, documented cause (see tests/QUARANTINE.md). It still RUNS in "
        "every lane, as xfail(strict=False): a failure reads 'xfailed', and "
        "a quarantined test that starts passing reads 'XPASS' so it can be "
        "released. reason= and ticket= are required; condition= (optional, "
        "default True) limits the quarantine to where its cause holds. "
        "`pytest -m quarantine` runs only them; `-m 'not quarantine'` leaves "
        "them out.",
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

        # FIX126 O8 — quarantined tests run as non-strict xfail with their
        # ticket in the reason, so the default lane (`-m "not integration"`,
        # unchanged) is green on a documented baseline and still reports
        # them. A quarantine without a reason or a ticket is refused.
        q = item.get_closest_marker("quarantine")
        if q is not None:
            reason = q.kwargs.get("reason") or (q.args[0] if q.args else "")
            ticket = q.kwargs.get("ticket") or (q.args[1] if len(q.args) > 1 else "")
            if not reason or not ticket:
                raise _pytest.UsageError(
                    f"{item.nodeid}: @pytest.mark.quarantine needs reason= and ticket="
                )
            # ``condition=`` (optional): quarantine only where the cause
            # holds — e.g. a cross-tree gate is quarantined only when the
            # sibling tree is not mounted, and stays a hard gate where it is.
            if q.kwargs.get("condition", True):
                item.add_marker(
                    _pytest.mark.xfail(strict=False, reason=f"quarantined [{ticket}]: {reason}")
                )


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


@pytest.fixture(autouse=True)
def _force_python_log_sink(monkeypatch):
    """FIX36 Part 0.1 — request logs NEVER leave the process during tests.

    The deployed container runs ``log_worker_engine="go"``: request logs are
    dispatched over gRPC to the Go log-worker sidecar, which writes to the
    REAL Postgres — completely outside the SQLAlchemy-level isolation the
    rest of this conftest builds. That is how pytest polluted
    ``request_logs`` with 214 test-tenant rows between May and Aug 2026.
    Pin the in-process python sink for every test; the loud tripwire in
    ``request_log_queue._dispatch_to_grpc`` (and the engine write guard in
    ``app.core.db``) stays armed as the backstop for anything this fixture
    cannot reach. Tests that exercise the gRPC client itself re-enable the
    path explicitly (SRAN_ALLOW_LOG_SINK_IN_TESTS=1).
    """
    try:
        from app.core.config import settings
    except Exception:
        return
    monkeypatch.setattr(settings, "log_worker_engine", "python", raising=False)
    yield


@pytest.fixture(autouse=True)
def _email_domain_allowlist_open_by_default(monkeypatch):
    """Open the sign-in domain allowlist for every test that doesn't
    explicitly exercise it.

    Production default is ``ALLOWED_EMAIL_DOMAINS=gbtech.co.th`` (Phase 0
    domain lock), but the pre-existing portal suite creates accounts on
    ``example.com``-style addresses throughout. Rather than rewrite those
    fixtures, the unit lane runs with the allowlist empty (= allow all).
    The gate itself is covered by ``test_oauth_domain_lock.py``, whose
    tests re-pin ``allowed_email_domains`` via the same monkeypatch
    mechanism (explicit fixtures/setattr run after this autouse one).
    """
    try:
        from app.core.config import settings
    except Exception:
        return
    monkeypatch.setattr(settings, "allowed_email_domains", "", raising=False)
    yield


@pytest.fixture(autouse=True)
def _alert_channels_off_by_default(monkeypatch):
    """FIX130 — no test can page a person.

    The lane's env is copied from the live backend container, which carries
    the owner's ALERT_NTFY_URL / ALERT_EMAIL_TO: every test that fired an
    alert rule POSTed it to the real ntfy topic (17 fake "test-rule: …"
    notifications on 2026-09-23), and the rule tests that expect
    ``delivery_status == "log_only"`` failed because a channel was
    configured. The global channels are blanked for every test; tests that
    exercise delivery set their own (fake) endpoint via the same monkeypatch
    mechanism (explicit setattr runs after this autouse one).
    """
    try:
        from app.core.config import settings
    except Exception:
        return
    # FIX131: the test topic and the environment label too — a test that
    # wants either sets it itself.
    for name in ("alert_ntfy_url", "alert_ntfy_token", "alert_email_to", "alert_webhook_url",
                 "alert_test_ntfy_url", "alert_environment"):
        monkeypatch.setattr(settings, name, "", raising=False)
    # FIX131: the pager's shared store is Redis in a real process; every test
    # gets its own empty in-memory store (no Redis, no state between tests).
    try:
        from app.services import alert_pager

        monkeypatch.setattr(alert_pager, "_store", alert_pager.MemoryStore(), raising=False)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _password_login_enabled_by_default(monkeypatch):
    """Keep password signup/login usable for every test that doesn't
    explicitly exercise the kill-switch.

    The Phase 3 cutover ships ``PASSWORD_LOGIN_ENABLED=false`` in the
    deployment env, but hundreds of pre-SSO fixtures create their users
    via ``POST /portal/auth/signup`` and would all 403 if the suite
    inherited that env. Same pattern as the domain-allowlist fixture
    above: the unit lane forces the flag on; the kill-switch behavior
    itself is covered by ``test_oauth_domain_lock.py``, which re-pins
    the flag to ``False`` via monkeypatch (explicit setattr runs after
    this autouse one).
    """
    try:
        from app.core.config import settings
    except Exception:
        return
    monkeypatch.setattr(settings, "password_login_enabled", True, raising=False)
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
# Phase 13 D — portal credential rate limiters (login / signup).
# Every TestClient request shares ip="testclient", so the real Redis (or
# outage-conservative) counters would throttle the suite after a handful of
# portal signups/logins. Neutralise by handing each request a limiter backed
# by a FRESH fakeredis — counters can never accumulate. The dedicated tests
# (test_portal_auth_rate_limit.py) override these deps again with a
# persistent limiter to exercise the real behaviour.
# ---------------------------------------------------------------------------

import fakeredis as _fakeredis  # noqa: E402

from app.api.routes import portal_auth as _portal_auth_routes  # noqa: E402
from app.services.admin.login_service import (  # noqa: E402
    LoginRateLimiter as _PortalLoginRateLimiter,
)


def _fresh_portal_login_limiter():
    return _PortalLoginRateLimiter(
        redis_client=_fakeredis.FakeRedis(decode_responses=True),
        per_email_limit=5,
        per_email_window_seconds=15 * 60,
        per_ip_limit=30,
        per_ip_window_seconds=15 * 60,
        email_key_prefix="portal_login_attempts:",
        ip_key_prefix="portal_login_ip:",
    )


def _fresh_portal_signup_limiter():
    return _PortalLoginRateLimiter(
        redis_client=_fakeredis.FakeRedis(decode_responses=True),
        per_email_limit=5,
        per_email_window_seconds=60 * 60,
        per_ip_limit=10,
        per_ip_window_seconds=60 * 60,
        email_key_prefix="portal_signup_attempts:",
        ip_key_prefix="portal_signup_ip:",
    )


main_app.dependency_overrides[
    _portal_auth_routes.get_portal_login_rate_limiter
] = _fresh_portal_login_limiter
main_app.dependency_overrides[
    _portal_auth_routes.get_portal_signup_rate_limiter
] = _fresh_portal_signup_limiter


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
        ProviderKeyService,
        "get_api_key",
        lambda self, session, provider, model=None: "test-stub-key",
    )

    # Model discovery reads the whole candidate pool rather than one
    # round-robin draw (a catalog read needs one key that WORKS, not a
    # fair share). Stub it alongside, or discovery would fall through to
    # a real DB read and hand its adapters ``None``.
    monkeypatch.setattr(
        ProviderKeyService,
        "get_all_active_keys",
        lambda self, session, provider: ["test-stub-key"],
    )

    # Track A: the chat hot path now calls the async variant. Stub it too,
    # or the flipped handler resolves None → ProviderUnavailableError (503).
    async def _get_api_key_async_stub(self, session, provider_name, model=None):
        return "test-stub-key"

    monkeypatch.setattr(
        ProviderKeyService, "get_api_key_async", _get_api_key_async_stub
    )


@pytest.fixture(autouse=True)
def _mock_adapter_stream_chat(request, monkeypatch):
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

    if request.node.get_closest_marker("real_adapter"):
        # FIX119 — a test that exercises the REAL stream path (fake transport) opts out.
        return
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


@pytest.fixture(autouse=True)
def _no_inherited_health_markers_anywhere():
    """FIX124 — a health marker never outlives the test that set it.

    Tests fail a primary on purpose, which marks real pairs
    (openai/gpt-4o-mini above all). The markers used to sit in a shared Redis
    until their TTL ran out, so a later test's route depended on how long the
    tests before it had taken (FIX57), and the escalating cooldown would have
    stretched that to 15 minutes. They now live in the run's own fake server
    and are dropped around every test.
    """
    _redis_isolation.clear_health_keys()
    yield
    _redis_isolation.clear_health_keys()


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


@pytest.fixture(autouse=True)
def _restore_model_registry():
    """Put the process-global model registry back after every test.

    FIX57 — ``model_registry._registry`` is a module singleton seeded at
    import, and ``ModelRegistry.reload`` REPLACES its contents with
    whatever active rows the database holds. A test that triggers a
    discovery reload against a database with no Claude rows therefore
    empties the seeded catalogue for every test that runs after it, in
    the same process.

    That is how four tests came to fail in the full lane and pass on
    their own: ``get_candidate("claude-3-5-sonnet-20241022")`` returned
    None, and the admin discovery endpoint listed nothing, because
    somebody upstream had reloaded. Nothing was wrong with either test.

    The snapshot is two shallow containers, so this costs a couple of
    microseconds per test and buys the one thing the release gate needs:
    a test's result not depending on which tests preceded it.
    """
    from app.services.providers import model_registry as _mr

    registry = _mr._registry
    candidates = list(registry._candidates)
    model_map = dict(registry._model_map)
    try:
        yield
    finally:
        registry._candidates = candidates
        registry._model_map = model_map
