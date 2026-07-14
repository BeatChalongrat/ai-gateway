"""DLP egress CI gate (Gateway Phase 9, A3).

Machine-checks the gateway's core invariant: **no user content reaches a
third party without DLP masking**, with exactly two intentional
exceptions (web search, media generation) that must still block the
credential/secret class.

Three layers of enforcement:

1. **Whitelist pin** — ``RAW_EGRESS_ALLOWED`` is exactly
   ``{"web_search", "media_gen"}``. Adding a third raw path requires
   editing that constant, this test, AND docs/DLP_ARCHITECTURE.md.
2. **Classification sweep** — every module under ``app/`` that talks
   HTTP outbound (imports httpx) must be classified below as
   ``masked`` / ``raw_whitelisted`` / ``no_user_content``. A new
   provider/integration file fails the sweep until a human classifies
   it — that conscious step is the drift guard.
3. **Behavioral seed-PII checks** — a fixture set (name, phone, email,
   Thai national ID, API-key secret) is pushed through the masking
   pipeline (must all disappear) and through the two whitelisted scans
   (identity passes raw, the secret blocks).
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from app.core.exceptions import SearchQueryBlockedError
from app.services.dlp.egress_policy import (
    RAW_EGRESS_ALLOWED,
    assert_egress_allowed,
    scan_search_query,
)

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

# ---------------------------------------------------------------------------
# The outbound-HTTP classification (R0.5 inventory, Phase 9).
#
# Keys are module paths relative to app/. Contracts:
#   masked          — carries user content that is POST-mask (or masked-at-
#                     rest snapshots) when it egresses.
#   raw_whitelisted — carries RAW user content by design; MUST sit behind a
#                     credential-blocking scan and the RAW_EGRESS_ALLOWED
#                     whitelist (web_search / media_gen channels only).
#   no_user_content — egresses no user-authored content (rates, catalogs,
#                     key-validation probes, auth codes, alert metadata) or
#                     talks only to OUR OWN infrastructure (NER sidecar).
# ---------------------------------------------------------------------------
EGRESS_CLASSIFICATION: dict[str, str] = {
    # Chat completions — messages are masked by the orchestrator before
    # any adapter sees them (mask → guardrail → moderation → dispatch).
    "services/providers/http_client.py": "masked",
    "services/providers/anthropic_adapter.py": "masked",
    "services/providers/google_adapter.py": "masked",
    "services/providers/openai_adapter.py": "masked",
    "services/providers/openai_compatible_adapter.py": "masked",
    "services/providers/openrouter_adapter.py": "masked",
    "services/providers/error_handler.py": "masked",
    "services/providers/base_urls.py": "masked",
    # OpenAI moderation — receives the post-mask dict_messages text.
    "services/moderation_service.py": "masked",
    # AI review — reviews masked-at-rest request-log snapshots and has its
    # own egress host gate (review_egress_allowed).
    "services/ai_review_provider.py": "masked",
    # THE two intentional raw channels.
    "services/search/providers.py": "raw_whitelisted",
    "services/media_gen/base.py": "raw_whitelisted",
    "services/media_gen/adapters/replicate_base.py": "raw_whitelisted",
    "services/media_gen/adapters/elevenlabs_tts.py": "raw_whitelisted",
    "services/media_gen/adapters/openai_tts.py": "raw_whitelisted",
    "services/providers/openai_images_client.py": "raw_whitelisted",
    # No user-authored content egresses here.
    "services/currency_service.py": "no_user_content",       # FX rates
    "services/discovery/pricing_fetcher.py": "no_user_content",
    "services/discovery/provider_discovery.py": "no_user_content",
    "services/webhook_service.py": "no_user_content",        # alert metadata
    "services/provider_key_service.py": "no_user_content",   # key probes
    "services/portal/oauth_flow_service.py": "no_user_content",
    "services/portal/connector_oauth_service.py": "no_user_content",
    "services/ner_service_client.py": "no_user_content",     # OWN sidecar
    "api/routes/admin.py": "no_user_content",                # provider health ping
}

# Seed-PII fixtures (A3.3): all five must vanish under masking; the
# identity four pass the whitelisted scans raw; the secret blocks.
PII_NAME = "สมชาย ใจดี"
PII_PHONE = "081-234-5678"
PII_EMAIL = "alice@example.com"
PII_THAI_ID = "1-2345-67890-12-3"
SECRET_API_KEY = "sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"
IDENTITY_FIXTURES = [PII_NAME, PII_PHONE, PII_EMAIL, PII_THAI_ID]


# ---------------------------------------------------------------------------
# 1. Whitelist pin
# ---------------------------------------------------------------------------


def test_raw_egress_whitelist_is_exactly_search_and_media():
    """Adding a channel here is a CONSCIOUS product/compliance decision —
    update RAW_EGRESS_ALLOWED, this test, and the disclosure doc together."""
    assert RAW_EGRESS_ALLOWED == frozenset({"web_search", "media_gen"})


def test_unlisted_channel_is_rejected_loudly():
    """A hypothetical third raw-egress integration calling the guard fails
    immediately (this simulates the 'fake new path' drill)."""
    with pytest.raises(RuntimeError, match="not whitelisted"):
        assert_egress_allowed("my_new_integration")
    # The two real channels pass.
    assert_egress_allowed("web_search")
    assert_egress_allowed("media_gen")


# ---------------------------------------------------------------------------
# 2. Classification sweep — the drift guard
# ---------------------------------------------------------------------------


def _outbound_http_modules() -> list[str]:
    """Every app/ module that imports httpx (the outbound HTTP layer)."""
    hits: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        rel = path.relative_to(APP_DIR).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "import httpx" in text or "from httpx" in text:
            hits.append(rel)
    return sorted(hits)


def test_every_outbound_http_module_is_classified():
    unclassified = [
        rel for rel in _outbound_http_modules()
        if rel not in EGRESS_CLASSIFICATION
    ]
    assert not unclassified, (
        "New outbound-HTTP module(s) found that are not classified in the "
        f"DLP egress gate: {unclassified}. Every module that egresses user "
        "content must be either MASKED (route it through the DLP masking "
        "transformer) or consciously added to RAW_EGRESS_ALLOWED in "
        "app/services/dlp/egress_policy.py (+ this classification, + "
        "docs/DLP_ARCHITECTURE.md). If it egresses no user content, "
        "classify it as 'no_user_content'."
    )


def test_classified_modules_still_exist():
    """The inventory can't rot: a renamed/deleted module must be
    re-classified, not silently forgotten."""
    missing = [
        rel for rel in EGRESS_CLASSIFICATION
        if not (APP_DIR / rel).exists()
    ]
    assert not missing, f"Classified modules no longer exist: {missing}"


def test_raw_call_sites_are_guarded():
    """Structural pin: the routes feeding the raw channels run the
    credential-blocking scan BEFORE the egress."""
    search_src = (APP_DIR / "api/routes/public_search.py").read_text(encoding="utf-8")
    assert "scan_search_query" in search_src
    # Scan happens before the provider call.
    assert search_src.index("scan_search_query(") < search_src.index(
        "search_service.search("
    )

    media_src = (APP_DIR / "services/media_gen/submit_helpers.py").read_text(
        encoding="utf-8"
    )
    assert 'assert_egress_allowed("media_gen")' in media_src
    assert '"media_gen"' in media_src  # media_gen profile scan

    images_src = (APP_DIR / "api/routes/public_images.py").read_text(encoding="utf-8")
    assert '"media_gen"' in images_src  # media_gen profile scan


# ---------------------------------------------------------------------------
# 3. Behavioral seed-PII checks
# ---------------------------------------------------------------------------


def _seed_text() -> str:
    return (
        f"contact {PII_NAME} at {PII_EMAIL} or {PII_PHONE}, "
        f"id {PII_THAI_ID}, key {SECRET_API_KEY}"
    )


@pytest.mark.asyncio
async def test_masked_path_emits_no_raw_fixture(async_db):
    """The shared masking pipeline every chat path routes through: with
    the DEFAULT profile, all five fixtures are transformed before any
    provider adapter could see them."""
    from app.services.orchestrator import GatewayOrchestrator
    from app.services.policy.profile_service import get_runtime_profile_async

    orch = GatewayOrchestrator()
    profile = await get_runtime_profile_async(async_db, "default")
    detection = await orch._scan_text_as_result_async(
        _seed_text(), direction="input", ner_enabled=False
    )
    enforcement = orch._scan_and_evaluate(detection, profile)
    masked, _span_map, _ = orch.transformer.mask_text(
        _seed_text(), enforcement.match_actions
    )

    for fixture in [PII_PHONE, PII_EMAIL, PII_THAI_ID, SECRET_API_KEY]:
        assert fixture not in masked, f"raw fixture leaked past masking: {fixture!r}"


@pytest.mark.asyncio
async def test_search_channel_blocks_secret_allows_identity(async_db):
    tenant = SimpleNamespace(tenant_id="t-gate", search_pii_egress="allow")

    with pytest.raises(SearchQueryBlockedError):
        await scan_search_query(f"debug {SECRET_API_KEY}", async_db, tenant)

    # Identity fixtures pass (raw by design) without raising.
    for fixture in IDENTITY_FIXTURES:
        await scan_search_query(f"search about {fixture}", async_db, tenant)


@pytest.mark.asyncio
async def test_media_channel_blocks_secret_allows_identity(async_db):
    from fastapi import HTTPException

    from app.services.media_gen.submit_helpers import (
        scan_media_prompt_with_profile,
    )

    with pytest.raises(HTTPException) as exc:
        await scan_media_prompt_with_profile(
            f"an image of my key {SECRET_API_KEY}", async_db
        )
    assert exc.value.status_code == 403

    for fixture in IDENTITY_FIXTURES:
        await scan_media_prompt_with_profile(
            f"a portrait of {fixture}", async_db
        )
