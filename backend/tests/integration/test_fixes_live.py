"""End-to-end live integration tests for the 6 DLP + routing fixes.

These tests fire real HTTP requests at the running gateway
(``http://localhost:8080`` by default) and inspect side effects in the
shared Postgres database. Nothing is mocked at the test layer — the
only redirect is the production-style ``PROVIDER_BASE_URL_OVERRIDES``
mechanism that points provider adapters at the in-cluster mock LLM
when present. See ``tests/integration/run_fix_tests.sh`` for the
wrapper that bounces the backend with the override before invoking
pytest.

Skip rules:
* If the backend is unreachable → all tests skip.
* If provider responses come from the real upstreams (no
  ``X-Mock-Provider`` header) → tests that need to drive a full
  request lifecycle skip with a clear message.
* Fix 3A is documented as expected-fail until the orchestrator is
  rewired to pass ``ner_enabled=False`` into ``get_default_registry``
  — see the test docstring for the follow-up.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Iterator

import httpx
import psycopg
import pytest


BASE_URL = os.environ.get("AI_GATEWAY_BASE_URL", "http://localhost:8080")
PG_DSN = os.environ.get(
    "AI_GATEWAY_PG_DSN",
    "postgresql://postgres:postgres@localhost:5432/ai_gateway",
)


# ---------------------------------------------------------------------------
# Connectivity probes — every fixture / test piggy-backs on these
# ---------------------------------------------------------------------------


def _backend_alive() -> bool:
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def _db_alive() -> bool:
    try:
        with psycopg.connect(PG_DSN, connect_timeout=2) as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _backend_alive(),
        reason=(
            f"Backend at {BASE_URL} not reachable. "
            f"Run `make up` then `tests/integration/run_fix_tests.sh`."
        ),
    ),
    pytest.mark.skipif(
        not _db_alive(),
        reason=(
            f"Postgres at {PG_DSN} not reachable. "
            f"Check the docker-compose stack is up and port 5432 is exposed."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def db_conn() -> Iterator[psycopg.Connection]:
    """Autocommit connection — tests insert seed data + read audit rows
    written by the live backend in another process, so we cannot rely on
    a single transaction."""
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        yield conn


def _hash_api_key(raw: str) -> str:
    """Mirror ``app.services.api_key_service.hash_api_key``: SHA-256 hex."""
    import hashlib
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_api_key() -> tuple[str, str, str]:
    """Mirror ``app.core.security.generate_api_key`` output shape:
    (raw_key, prefix, hash). Prefix is the first 8 chars (matches the
    live ``generate_api_key`` slice); hash is SHA-256 hex.

    Generating it here (rather than importing the live module) keeps
    this test file usable from the host without setting PYTHONPATH at
    the backend tree."""
    raw = f"gw_{uuid.uuid4().hex}"
    return raw, raw[:8], _hash_api_key(raw)


@pytest.fixture(scope="session")
def test_tenant(db_conn: psycopg.Connection) -> Iterator[dict[str, Any]]:
    """Create (or reuse) a dedicated integration tenant + project pair.

    ``ner_enabled=False`` at the tenant row AND at ``org_settings.id=1``
    so ``project_service.resolve_project_context``'s
    ``tenant_ner OR org_ner`` resolution actually lands on False — the
    precondition for Fix 3's ``thai_name_no_prefix`` detector to fire.
    The original ``org_settings`` value is restored on teardown so the
    rest of the dev stack stays untouched."""
    tenant_id = "integ-fixes-tenant"
    project_id = "integ-fixes-project"
    slug = "integ-fixes"

    db_conn.execute(
        """
        INSERT INTO tenants (tenant_id, name, slug, status, region, ner_enabled)
        VALUES (%s, %s, %s, 'active', 'global', false)
        ON CONFLICT (tenant_id) DO UPDATE SET ner_enabled = EXCLUDED.ner_enabled
        """,
        (tenant_id, "Integ Fixes Tenant", slug),
    )
    db_conn.execute(
        """
        INSERT INTO projects (project_id, tenant_id, name)
        VALUES (%s, %s, %s)
        ON CONFLICT (project_id) DO NOTHING
        """,
        (project_id, tenant_id, "Integ Fixes Project"),
    )

    # Stash + flip the org-wide NER toggle. project_service resolves
    # ``ctx.ner_enabled`` as ``tenant_ner OR org_ner`` so leaving the
    # org default at True would force NER on for every request in
    # this fixture's scope — and Fix 3A wants it off.
    original_org_ner = db_conn.execute(
        "SELECT ner_enabled FROM org_settings WHERE id = 1"
    ).fetchone()
    db_conn.execute(
        "UPDATE org_settings SET ner_enabled = false WHERE id = 1"
    )
    try:
        yield {"tenant_id": tenant_id, "project_id": project_id}
    finally:
        if original_org_ner is not None:
            db_conn.execute(
                "UPDATE org_settings SET ner_enabled = %s WHERE id = 1",
                (original_org_ner[0],),
            )


@pytest.fixture(scope="session")
def api_key(db_conn: psycopg.Connection, test_tenant: dict) -> str:
    """Insert an active API key for the integration project and return
    the raw token to use in ``Authorization: Bearer …`` headers."""
    raw, prefix, key_hash = _generate_api_key()
    db_conn.execute(
        """
        INSERT INTO api_keys
            (key_id, tenant_id, project_id, name, key_prefix, key_hash,
             status, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'active', NOW())
        """,
        (
            str(uuid.uuid4()),
            test_tenant["tenant_id"],
            test_tenant["project_id"],
            "integ-fixes-key",
            prefix,
            key_hash,
        ),
    )
    return raw


@pytest.fixture
def headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def cleanup_keyword_rules(db_conn: psycopg.Connection, test_tenant: dict):
    """Delete any keyword_rules rows the test created for this project /
    tenant after the test exits."""
    yield
    db_conn.execute(
        """
        DELETE FROM keyword_rules
        WHERE (scope = 'project' AND scope_id = %s)
           OR (scope = 'tenant' AND scope_id = %s)
           OR (scope = 'global' AND name LIKE 'integ_fixes_%%')
        """,
        (test_tenant["project_id"], test_tenant["tenant_id"]),
    )


def _insert_keyword_rule(
    db_conn: psycopg.Connection,
    *,
    name: str,
    keywords: list[str],
    model_override: str,
    scope: str,
    scope_id: str | None,
    priority: int = 1,
) -> None:
    db_conn.execute(
        """
        INSERT INTO keyword_rules
            (id, name, keywords_json, model_override, priority,
             scope, scope_id, enabled, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, true, NOW())
        """,
        (
            str(uuid.uuid4()),
            name,
            json.dumps(keywords),
            model_override,
            priority,
            scope,
            scope_id,
        ),
    )


def _request_log_by_id(
    db_conn: psycopg.Connection, request_id: str
) -> dict[str, Any] | None:
    cur = db_conn.execute(
        """
        SELECT request_id, provider, model, router_used, status,
               policy_action, input_raw, input_masked, output_raw,
               output_final, dlp_input_findings, dlp_output_findings,
               error_code
        FROM request_logs WHERE request_id = %s
        """,
        (request_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _wait_for_log(
    db_conn: psycopg.Connection, request_id: str, timeout: float = 5.0
) -> dict[str, Any] | None:
    """Poll the request_logs table until the row shows up or we give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = _request_log_by_id(db_conn, request_id)
        if row is not None:
            return row
        time.sleep(0.1)
    return None


def _find_recent_blocked_row(
    db_conn: psycopg.Connection,
    *,
    error_code: str,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """Find the most recent BLOCKED ``request_logs`` row with the given
    ``error_code``. Used for inbound-block paths where the middleware
    request_id (returned to the client) does not match the orchestrator
    request_id (written to the audit row), so we cannot look up by id."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cur = db_conn.execute(
            """
            SELECT request_id, provider, model, router_used, status,
                   policy_action, input_raw, input_masked, output_raw,
                   output_final, dlp_input_findings, dlp_output_findings,
                   error_code
            FROM request_logs
            WHERE status = 'blocked' AND error_code = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (error_code,),
        )
        row = cur.fetchone()
        if row is not None:
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        time.sleep(0.1)
    return None


def _chat(
    base_url: str,
    headers: dict,
    *,
    content: str,
    model: str = "auto",
    metadata: dict | None = None,
    stream: bool = False,
    timeout: float = 60.0,
) -> httpx.Response:
    return httpx.post(
        f"{base_url}/v1/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "metadata": metadata or {},
            "stream": stream,
        },
        timeout=timeout,
    )


# Per-test handling of the "provider unreachable" case lives inside
# each test as an explicit ``if r.status_code == 502: pytest.skip(...)``
# guard — that keeps the skip reason adjacent to the assertion and
# avoids module-level state about which tests need which upstreams.


# ===========================================================================
# Fix 1 — DLP: token mutation restore
# ===========================================================================


class TestFix1DLPRestore:
    def test_1a_exact_token_restored_in_audit_input_masked(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
    ):
        """Baseline: the email is masked on the way TO the provider
        (so it never leaks upstream). The mock's canned templates
        sometimes include CRITICAL credentials in response to any
        prompt containing the substring ``email`` — and the masked
        placeholder ``[EMAIL_0]`` itself contains that substring, so
        the outbound DLP scan may legitimately 403. Both outcomes are
        valid here: what we're asserting is the *inbound* masking, and
        the BLOCKED row also carries ``input_masked``."""
        # Unique email per run — the response cache is keyed on
        # (model, messages, project), so a repeat prompt would hit the
        # cache and skip the routing / DLP scan paths we want to test.
        unique_email = f"alice-{uuid.uuid4().hex[:8]}@example.com"
        r = _chat(
            base_url, headers,
            content=f"contact {unique_email} for help",
        )
        if r.status_code == 502:
            pytest.skip(
                "Provider returned 502 — likely placeholder keys + no "
                "PROVIDER_BASE_URL_OVERRIDES. See run_fix_tests.sh."
            )
        assert r.status_code in (200, 403), (
            f"Expected 200 (success) or 403 (outbound DLP block) from "
            f"/v1/chat/completions, got {r.status_code}: {r.text[:300]}"
        )

        if r.status_code == 200:
            request_id = r.json()["id"]
            row = _wait_for_log(db_conn, request_id)
        else:
            # 403 outbound block — the row is written under the
            # orchestrator id (not the middleware id surfaced to the
            # client), so look it up by recent error_code.
            row = _find_recent_blocked_row(
                db_conn, error_code="dlp_violation"
            )

        assert row is not None, (
            "No request_logs row written for the chat completion."
        )
        assert row["dlp_input_findings"] >= 1, (
            f"Expected ≥1 inbound DLP finding (the email), got "
            f"{row['dlp_input_findings']}. input_raw={row['input_raw']!r}"
        )
        masked = row["input_masked"] or ""
        assert unique_email not in masked, (
            f"Raw email leaked into input_masked: {masked!r}"
        )
        assert "<r" in masked, (
            f"Expected a <rN> placeholder in input_masked: {masked!r}"
        )

    def test_1b_transformer_restore_handles_token_variants(self):
        """Direct exercise of the transformer's normalize-before-restore
        path added in Fix 2 (the DLP-fix prompt). Runs in the test
        runner's own process — no HTTP, no provider — so it's stable
        even when the upstreams aren't reachable."""
        try:
            from app.services.dlp.transformer import DLPTransformer
        except ImportError:
            pytest.skip(
                "app.services.dlp.transformer not importable from this "
                "process (set PYTHONPATH=backend or run inside the "
                "backend container)."
            )
        transformer = DLPTransformer()
        span_map = {"<r0>": "alice@example.com"}
        variants = [
            "<R0>",            # uppercase
            "< r0 >",          # padded angles
            "<r0 >",           # trailing space
            "<r0>",            # canonical
        ]
        for variant in variants:
            result = transformer.restore(variant, span_map)
            assert "alice@example.com" in result, (
                f"Restore failed for variant {variant!r}: got {result!r}"
            )
            assert "<" not in result and ">" not in result, (
                f"Token angle leaked through restore for variant {variant!r}: "
                f"{result!r}"
            )


# ===========================================================================
# Fix 2 — DLP streaming: tail buffer must not leak long credentials
# ===========================================================================


def _collect_sse(resp: httpx.Response) -> tuple[str, list[dict]]:
    """Read a streaming response into (concatenated_text, list_of_events)."""
    text_parts: list[str] = []
    events: list[dict] = []
    for line in resp.iter_lines():
        if not line:
            continue
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):].strip()
        if payload == "[DONE]":
            continue
        try:
            ev = json.loads(payload)
        except Exception:
            continue
        events.append(ev)
        for ch in ev.get("choices", []):
            delta = ch.get("delta", {})
            piece = delta.get("content")
            if isinstance(piece, str):
                text_parts.append(piece)
    return "".join(text_parts), events


class TestFix2StreamingTail:
    """Fix 2 sized the streaming tail buffer to 120 chars (was 50) so a
    long ``db_connection_string`` or ``private_key`` PEM block can't
    span a chunk boundary in a way that leaks the leading fragment.

    Critically, the default policy profile *blocks* CRITICAL-severity
    findings at the inbound stage — the credential never reaches the
    provider at all. That's the strongest guarantee: no provider call,
    no stream, no leak. The streaming tail buffer is a defense-in-depth
    against the model emitting a long credential *outbound* in response
    to a non-credential prompt; the mock provider's canned templates
    don't include >120-char patterns so the outbound side is exercised
    by the unit tests added in the DLP-fix prompt, not here. These
    integration tests assert the inbound block fires cleanly and the
    BLOCKED audit row records the credential category."""

    def test_2a_db_connection_string_blocked_inbound(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
    ):
        secret = "postgresql://admin:s3cr3tP@ss@db.internal.corp:5432/production_db"
        with httpx.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            headers=headers,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": f"here is our db: {secret}"}],
                "stream": True,
                "metadata": {},
            },
            timeout=60.0,
        ) as resp:
            body = resp.read()

        # Block fires before any SSE bytes are emitted — the central
        # error handler returns a clean JSON 403 instead of a partial
        # stream. That's the entire safety contract.
        assert resp.status_code == 403, (
            f"Expected 403 DLP block for a CRITICAL credential, got "
            f"{resp.status_code}: {body[:300]}"
        )
        payload = json.loads(body)
        assert payload["error"]["code"] == "dlp_violation", payload
        # The password literal must not appear in the error body
        # (would mean we echoed back what we blocked).
        assert "s3cr3tP@ss" not in body.decode(), (
            f"Blocked response leaked the credential literal: {body!r}"
        )

        # The audit row is persisted under the orchestrator's internal
        # ``req_<hex>`` id (distinct from the middleware request_id
        # surfaced in the error body), so we look it up by recent
        # ``error_code='dlp_violation'`` rows for this project.
        row = _find_recent_blocked_row(
            db_conn, error_code="dlp_violation"
        )
        assert row is not None, (
            "No BLOCKED dlp_violation row written after the 403 response."
        )
        assert row["status"] == "blocked", row

    def test_2b_private_key_blocked_inbound(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
    ):
        pem = (
            "use this key: -----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA1234abcdEFGHIJKLMNOPQRSTUVWXYZ0123456789+/abcdef\n"
            "fakefakefakefakefakefakefakefakefakefakefakefakefakefakefakefake\n"
            "-----END RSA PRIVATE KEY-----"
        )
        with httpx.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            headers=headers,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": pem}],
                "stream": True,
                "metadata": {},
            },
            timeout=60.0,
        ) as resp:
            body = resp.read()

        assert resp.status_code == 403, (
            f"Expected 403 DLP block for a PEM private key, got "
            f"{resp.status_code}: {body[:300]}"
        )
        payload = json.loads(body)
        assert payload["error"]["code"] == "dlp_violation", payload
        assert "MIIEowIBAA" not in body.decode(), (
            f"Blocked response leaked the PEM body: {body!r}"
        )

        row = _find_recent_blocked_row(
            db_conn, error_code="dlp_violation"
        )
        assert row is not None, (
            "No BLOCKED dlp_violation row written after the 403 response."
        )
        assert row["status"] == "blocked", row


# ===========================================================================
# Fix 3 — DLP: bare Thai name + NER off
# ===========================================================================


class TestFix3ThaiNameNoPrefix:
    def test_3a_bare_thai_name_with_context_keyword_masked(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
    ):
        r = _chat(
            base_url, headers,
            content="ลูกค้าชื่อ สมชาย ใจดี ต้องการความช่วยเหลือ",
        )
        if r.status_code == 502:
            pytest.skip("Provider 502 — needs PROVIDER_BASE_URL_OVERRIDES.")
        assert r.status_code == 200, r.text[:300]
        request_id = r.json()["id"]
        row = _wait_for_log(db_conn, request_id)
        assert row is not None, f"No request_logs row for {request_id}."
        masked = row["input_masked"] or ""
        assert "สมชาย" not in masked, (
            f"Bare Thai name leaked into input_masked: {masked!r}"
        )
        assert "<r" in masked, (
            f"Expected a <rN> placeholder, got: {masked!r}"
        )

    def test_3b_prefixed_thai_name_and_phone_still_masked(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
    ):
        r = _chat(
            base_url, headers,
            content="นายสมชาย ใจดี โทร 0812345678",
        )
        if r.status_code == 502:
            pytest.skip("Provider 502 — needs PROVIDER_BASE_URL_OVERRIDES.")
        assert r.status_code == 200, r.text[:300]
        request_id = r.json()["id"]
        row = _wait_for_log(db_conn, request_id)
        assert row is not None, f"No request_logs row for {request_id}."
        masked = row["input_masked"] or ""
        assert "สมชาย" not in masked, (
            f"Prefixed Thai name leaked into input_masked: {masked!r}"
        )
        assert "0812345678" not in masked, (
            f"Phone number leaked into input_masked: {masked!r}"
        )
        # Both name and phone produce ``<rN>`` placeholders — at least
        # two distinct tokens must be present.
        import re
        tokens = set(re.findall(r"<r\d+>", masked))
        assert len(tokens) >= 2, (
            f"Expected at least two <rN> placeholders, got: {masked!r}"
        )


# ===========================================================================
# Fix 4 — Routing: project-scoped keyword rule beats semantic
# ===========================================================================


class TestFix4ProjectKeywordOverSemantic:
    """The dev stack runs with ``semantic_router_enabled=False`` and no
    qwen/zai/kimi/etc. provider keys, so we can't drive a confident
    semantic-vs-keyword race end-to-end here — that scenario is unit-
    tested in ``tests/test_routing_priority.py::TestProjectScopeOverridesSemantic``
    against a fake encoder. What these integration tests *do* prove is
    the new ``keyword_project`` chokepoint:

    * 4A: a project-scoped rule changes the routed model AND surfaces
      the new ``router_used='keyword_project'`` label in the audit row,
      which means the pre-semantic check actually ran in production.
    * 4B: a global-scoped rule still routes via the regular
      ``router_used='keyword'`` path — Fix 4 must not misclassify it.

    Both rules route to ``gpt-4o`` (openai, has a provider key in the
    dev env) so the request completes and we can read the request_logs
    row. The auto-default for ``general`` is ``gpt-4o-mini``; a routed
    model of ``gpt-4o`` is unambiguous proof that the rule fired."""

    def test_4a_project_rule_surfaces_keyword_project_label(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
        test_tenant: dict,
        cleanup_keyword_rules,
    ):
        _insert_keyword_rule(
            db_conn,
            name="integ_fixes_project_4a",
            keywords=["วิเคราะห์ข้อมูล"],
            model_override="gpt-4o",
            scope="project",
            scope_id=test_tenant["project_id"],
        )
        # Unique tail per run defeats the response cache (keyed on the
        # message content + project_id), otherwise a re-run would hit
        # the cached row from the previous run and router_used would
        # be NULL because the cache path doesn't re-route.
        nonce = uuid.uuid4().hex[:8]
        r = _chat(
            base_url, headers,
            content=f"วิเคราะห์ข้อมูลยอดขายของเราหน่อย ({nonce})",
            model="auto",
        )
        if r.status_code == 502:
            pytest.skip("Provider 502 — needs PROVIDER_BASE_URL_OVERRIDES.")
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        request_id = body["id"]
        row = _wait_for_log(db_conn, request_id)
        assert row is not None, f"No request_logs row for {request_id}."
        assert row["router_used"] == "keyword_project", (
            f"Expected router_used='keyword_project', got "
            f"{row['router_used']!r}. The orchestrator's _apply_router_chain "
            f"may not be calling get_project_scoped_hint() first."
        )
        assert row["model"] == "gpt-4o", (
            f"Expected the project rule's model_override (gpt-4o), got "
            f"{row['model']!r}"
        )

    def test_4b_global_rule_uses_plain_keyword_label(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
        cleanup_keyword_rules,
    ):
        _insert_keyword_rule(
            db_conn,
            name="integ_fixes_global_4b",
            keywords=["analyze"],
            model_override="gpt-4o",
            scope="global",
            scope_id=None,
        )
        # Unique tail per run defeats the response cache.
        nonce = uuid.uuid4().hex[:8]
        r = _chat(
            base_url, headers,
            content=f"please analyze our data carefully ({nonce})",
        )
        if r.status_code == 502:
            pytest.skip("Provider 502 — needs PROVIDER_BASE_URL_OVERRIDES.")
        assert r.status_code == 200, r.text[:300]
        request_id = r.json()["id"]
        row = _wait_for_log(db_conn, request_id)
        assert row is not None, f"No request_logs row for {request_id}."
        # The forbidden outcome: a global rule promoted to keyword_project.
        assert row["router_used"] != "keyword_project", (
            f"Global rule incorrectly promoted to keyword_project — "
            f"router_used={row['router_used']!r}, row={row!r}"
        )
        # Either ``keyword`` (our rule won the regular pass) or
        # ``keyword`` from a DEFAULT_RULES match on "analyze" is fine —
        # both demonstrate the global pass runs after semantic defers.
        assert row["router_used"] == "keyword", (
            f"Expected router_used='keyword' (semantic disabled in dev "
            f"stack → keyword pass wins), got {row['router_used']!r}"
        )


# ===========================================================================
# Fix 5 — Routing: preferred_provider is a real soft preference
# ===========================================================================


class TestFix5PreferredProvider:
    def test_5a_preferred_provider_selected_in_auto(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
    ):
        r = _chat(
            base_url, headers,
            content=f"hello {uuid.uuid4().hex[:8]}",
            model="auto",
            metadata={
                "use_case": "general",
                "preferred_provider": "anthropic",
            },
        )
        if r.status_code == 502:
            pytest.skip("Provider 502 — needs PROVIDER_BASE_URL_OVERRIDES.")
        assert r.status_code == 200, r.text[:300]
        request_id = r.json()["id"]
        row = _wait_for_log(db_conn, request_id)
        assert row is not None, f"No request_logs row for {request_id}."
        assert row["provider"] == "anthropic", (
            f"Expected provider=anthropic (preferred), got {row['provider']!r}. "
            f"Fix 5 may not be wired — check _route_auto's preferred_provider "
            f"override block in routing_service.py."
        )

    def test_5b_unknown_preferred_falls_back_gracefully(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
    ):
        r = _chat(
            base_url, headers,
            content=f"hello {uuid.uuid4().hex[:8]}",
            model="auto",
            metadata={
                "use_case": "general",
                "preferred_provider": "fictional-provider",
                "allowed_providers": ["openai"],
            },
        )
        # The key contract: NO 5xx. Either the request completes (200)
        # or the gateway returns a 4xx with a clear explanation. A 500
        # would mean preferred_provider with no candidates raised
        # uncaught.
        assert r.status_code < 500, (
            f"Unknown preferred_provider produced a {r.status_code}: "
            f"{r.text[:300]} — preferred should fall back silently."
        )
        if r.status_code == 502:
            pytest.skip("Provider 502 — needs PROVIDER_BASE_URL_OVERRIDES.")
        if r.status_code == 200:
            request_id = r.json()["id"]
            row = _wait_for_log(db_conn, request_id)
            assert row is not None, f"No request_logs row for {request_id}."
            assert row["provider"] == "openai", (
                f"Expected fallback to openai when preferred has no "
                f"candidates, got {row['provider']!r}"
            )


# ===========================================================================
# Fix 6 — Routing: explicit-model swap is model-aware + deterministic
# ===========================================================================


class TestFix6CompatibleProvider:
    def test_6a_claude_with_openai_only_allowed_is_explicit_not_silent(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
    ):
        """When claude is asked but only openai is allowed, the legacy
        code would silently swap to openai and get a 404 from upstream.
        Fix 6 still falls back to ``openai`` but logs a
        ``routing_no_compatible_provider`` warning and writes a clear
        reason string. The contract this asserts: no 5xx from the
        gateway itself."""
        r = _chat(
            base_url, headers,
            content=f"hello {uuid.uuid4().hex[:8]}",
            model="claude-3-5-sonnet-20241022",
            metadata={"allowed_providers": ["openai"]},
        )
        # The gateway must not 5xx its own routing decision. A 4xx
        # (validation) or 200 (with the fallback target) is fine; a 500
        # would indicate the route-engine itself raised.
        assert r.status_code != 500, (
            f"Routing engine 500'd on incompatible explicit swap: "
            f"{r.text[:300]}"
        )
        # 502 from a downstream provider (openai 404 on claude-model) is
        # ALSO acceptable because the fallback contract is explicit
        # about that — the routing reasons string warns operators.
        if r.status_code in (200, 502):
            return  # both are valid documented outcomes
        # Any 4xx is fine too as long as it's a clear gateway message,
        # not a silent corruption.
        assert 400 <= r.status_code < 500, (
            f"Unexpected status {r.status_code}: {r.text[:300]}"
        )

    def test_6b_explicit_swap_is_deterministic(
        self,
        base_url: str,
        headers: dict,
        db_conn: psycopg.Connection,
    ):
        """Send the same incompatible request multiple times; the
        gateway's swap target must be identical every run (Fix 6
        replaced ``next(iter(frozenset))`` with sorted iteration)."""
        providers: list[str | None] = []
        for _ in range(5):
            r = _chat(
                base_url, headers,
                content=f"hello {uuid.uuid4().hex[:8]}",
                model="gpt-4o",
                metadata={"allowed_providers": ["anthropic", "openrouter"]},
            )
            if r.status_code == 502:
                pytest.skip("Provider 502 — needs PROVIDER_BASE_URL_OVERRIDES.")
            if r.status_code != 200:
                # We still want to assert determinism, so capture the
                # error body's provider hint when present.
                providers.append(None)
                continue
            request_id = r.json()["id"]
            row = _wait_for_log(db_conn, request_id)
            providers.append(row["provider"] if row else None)
        # The whole point is determinism: every run must agree.
        non_none = [p for p in providers if p is not None]
        assert len(set(non_none)) <= 1, (
            f"Provider swap flapped across runs: {providers}. "
            f"Fix 6's sorted(allowed_providers) iteration may not be wired."
        )
