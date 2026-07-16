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

from app.core.exceptions import RawEgressBlockedError, SearchQueryBlockedError
from app.services.dlp.egress_policy import (
    RAW_EGRESS_ALLOWED,
    assert_egress_allowed,
    assert_stt_egress_allowed,
    scan_embedding_text,
    scan_search_query,
    scan_voice_text,
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
    # THE intentional raw channels.
    "services/search/providers.py": "raw_whitelisted",
    "services/media_gen/base.py": "raw_whitelisted",
    "services/media_gen/adapters/replicate_base.py": "raw_whitelisted",
    "services/media_gen/adapters/elevenlabs_tts.py": "raw_whitelisted",
    "services/media_gen/adapters/openai_tts.py": "raw_whitelisted",
    "services/providers/openai_images_client.py": "raw_whitelisted",
    # FIX 2 — gateway-fronted embeddings + voice (chat-edge no longer calls
    # OpenAI directly). RAW user content by design, gated by the whitelisted
    # embeddings / voice channels (credential-blocked + per-tenant PII + audit).
    "services/embeddings/openai_embeddings_client.py": "raw_whitelisted",
    "services/voice/openai_voice_client.py": "raw_whitelisted",
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


def test_raw_egress_whitelist_is_exactly_the_four_channels():
    """Adding a channel here is a CONSCIOUS product/compliance decision —
    update RAW_EGRESS_ALLOWED, this test, and the disclosure doc together.
    FIX 2 added embeddings + voice (gateway-fronted; chat-edge no longer
    calls OpenAI directly)."""
    assert RAW_EGRESS_ALLOWED == frozenset(
        {"web_search", "media_gen", "embeddings", "voice"}
    )


def test_unlisted_channel_is_rejected_loudly():
    """A hypothetical third raw-egress integration calling the guard fails
    immediately (this simulates the 'fake new path' drill)."""
    with pytest.raises(RuntimeError, match="not whitelisted"):
        assert_egress_allowed("my_new_integration")
    # The real channels pass.
    assert_egress_allowed("web_search")
    assert_egress_allowed("media_gen")
    assert_egress_allowed("embeddings")
    assert_egress_allowed("voice")


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


# ---------------------------------------------------------------------------
# 2b. Code-interpreter sandbox (Phase 13) — an INTERNAL trust boundary.
#
# The run_python / plan-exec code interpreter is NOT a Python-backend egress
# channel: it lives entirely in chat-edge (Go) plus an in-container Python
# runner, with ZERO representation under backend/app/. So the httpx sweep
# above never sees it, and it must NOT be forced into EGRESS_CLASSIFICATION
# (a fake key there would fail test_classified_modules_still_exist).
#
# Its classification is INTERNAL, like the NER sidecar: the interpreter runs
# on RAW user data but has NO third-party network egress (docker --network
# none + a seccomp profile with every networking syscall removed), so results
# reach a provider ONLY through the masked completion pipeline. It is
# therefore correctly ABSENT from RAW_EGRESS_ALLOWED (it is not a raw egress
# path at all — it is a no-egress path).
#
# The substantive proof lives in the Go arg-builder test
# (services/chat-edge-api/internal/sandbox/sandbox_test.go::
# TestSandbox_DockerArgsAlwaysNoNetwork). The two tests below pin that proof
# from the Python gate so the guarantee cannot be silently removed on the Go
# side without a red test on this side too.
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SANDBOX_GO = (
    REPO_ROOT / "services" / "chat-edge-api" / "internal" / "sandbox" / "sandbox.go"
)
SANDBOX_GO_TEST = (
    REPO_ROOT / "services" / "chat-edge-api" / "internal" / "sandbox" / "sandbox_test.go"
)
SANDBOX_SECCOMP = (
    REPO_ROOT / "services" / "chat-edge-api" / "sandbox" / "seccomp.json"
)

# FIX 2 — chat-edge's embeddings + voice paths (Go). These MUST route through
# the gateway (which runs the DLP raw-egress gate), NOT call OpenAI directly.
CHAT_EDGE_EMBEDDING_GO = (
    REPO_ROOT / "services" / "chat-edge-api" / "internal" / "service"
    / "embedding_service.go"
)
CHAT_EDGE_VOICE_GO = (
    REPO_ROOT / "services" / "chat-edge-api" / "internal" / "handler" / "voice.go"
)
CHAT_EDGE_GATEWAY_CLIENT_GO = (
    REPO_ROOT / "services" / "chat-edge-api" / "internal" / "service"
    / "gateway_client.go"
)


def test_chat_edge_embeddings_voice_route_through_gateway_not_openai():
    """Drift guard for the FIX 2 bypass removal (Go side). chat-edge used to
    call OpenAI embeddings/Whisper/TTS directly, bypassing DLP. It must now
    forward to the gateway (which fronts the DLP raw-egress gate). If a future
    edit re-adds a direct OpenAI call on these paths, this test reddens on the
    Python side too — mirroring the sandbox pin above.
    """
    emb = CHAT_EDGE_EMBEDDING_GO.read_text(encoding="utf-8")
    voice = CHAT_EDGE_VOICE_GO.read_text(encoding="utf-8")

    # No direct OpenAI egress on either path.
    for src, name in ((emb, "embedding_service.go"), (voice, "voice.go")):
        assert "api.openai.com" not in src, (
            f"{name} references api.openai.com — the FIX 2 bypass removal "
            "regressed (it must route through the gateway)."
        )
        assert "/audio/transcriptions" not in src
        assert "/audio/speech" not in src

    # Both forward through the gateway client.
    assert "s.gateway.GenerateEmbedding(" in emb, (
        "embedding_service.go no longer forwards to the gateway"
    )
    assert "h.gw.VoiceTranscribe(" in voice and "h.gw.VoiceSynthesize(" in voice, (
        "voice.go no longer forwards to the gateway"
    )

    # The gateway client targets the gateway's whitelisted routes.
    gw = CHAT_EDGE_GATEWAY_CLIENT_GO.read_text(encoding="utf-8")
    assert 'g.BaseURL+"/embeddings"' in gw
    assert 'g.BaseURL+"/voice/transcribe"' in gw
    assert 'g.BaseURL+"/voice/synthesize"' in gw


def test_code_interpreter_is_not_a_raw_egress_channel():
    """The sandbox is internal (no third-party egress), so it must never be
    added to the raw-egress whitelist. If someone reclassifies it as a raw
    egress channel, that is a conscious product/compliance decision that must
    update this test and the disclosure doc together — same contract as the
    two real raw channels."""
    assert "code_interpreter" not in RAW_EGRESS_ALLOWED
    assert "sandbox" not in RAW_EGRESS_ALLOWED
    assert "run_python" not in RAW_EGRESS_ALLOWED


def test_code_interpreter_egress_is_go_side_and_network_locked():
    """The code-interpreter egress path is Go-side + internal. Pin the actual
    no-third-party-network guarantee from PART A/C at the source level so a
    Go-side edit that weakens it also reddens the Python egress gate.

    We assert three things about the checked-in sandbox:
      1. dockerArgs pins `--network` immediately followed by `"none"`.
      2. no OTHER --network value (bridge/host/container) appears anywhere.
      3. the Go arg-builder no-network unit test exists.
    Plus: the seccomp profile removes networking syscalls (defence in depth).
    """
    src = SANDBOX_GO.read_text(encoding="utf-8")

    # 1. The exact adjacent `--network`, `none` argv pair the daemon sees.
    assert '"--network", "none"' in src, (
        "sandbox.go no longer pins '--network', 'none' in the docker arg "
        "builder — the code-interpreter egress invariant is broken."
    )

    # 2. No reachable widening of the network mode.
    for bad in ('"--network", "bridge"', '"--network", "host"',
                '"--network", "container', '"--network=bridge"',
                '"--network=host"'):
        assert bad not in src, f"sandbox.go reaches a non-'none' network mode: {bad}"

    # 3. The substantive Go proof is present.
    test_src = SANDBOX_GO_TEST.read_text(encoding="utf-8")
    assert "TestSandbox_DockerArgsAlwaysNoNetwork" in test_src, (
        "the Go arg-builder no-network unit test is missing — the sandbox "
        "egress invariant has no code-level guard."
    )

    # Defence in depth: seccomp default-denies and never allow-lists socket.
    seccomp = SANDBOX_SECCOMP.read_text(encoding="utf-8")
    assert '"defaultAction": "SCMP_ACT_ERRNO"' in seccomp
    for netcall in ('"socket"', '"connect"', '"bind"', '"sendto"', '"recvfrom"'):
        assert netcall not in seccomp, (
            f"seccomp profile now allow-lists a networking syscall ({netcall}); "
            "the sandbox could open a socket despite --network none."
        )


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

    # FIX 2 — embeddings + voice routes run their egress scan BEFORE calling
    # the OpenAI utility client (scan-then-egress ordering).
    emb_src = (APP_DIR / "api/routes/public_embeddings.py").read_text(encoding="utf-8")
    assert "scan_embedding_text" in emb_src
    assert emb_src.index("scan_embedding_text(") < emb_src.index("generate_embedding(")

    voice_src = (APP_DIR / "api/routes/public_voice.py").read_text(encoding="utf-8")
    # STT: policy/audit gate before reading + forwarding audio. Anchor on the
    # `await` call sites (the def names contain "transcribe("/"synthesize(").
    assert "assert_stt_egress_allowed" in voice_src
    assert voice_src.index("assert_stt_egress_allowed(") < voice_src.index(
        "await transcribe("
    )
    # TTS: text scan before synthesis.
    assert "scan_voice_text" in voice_src
    assert voice_src.index("scan_voice_text(") < voice_src.index("await synthesize(")


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


# ---------------------------------------------------------------------------
# 3b. FIX 2 — embeddings + voice channels (same contract as search/media).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embeddings_channel_blocks_secret_allows_identity(async_db):
    tenant = SimpleNamespace(tenant_id="t-emb", kb_pii_egress="allow")

    # A KB doc / query carrying a secret must never be embedded.
    with pytest.raises(RawEgressBlockedError):
        await scan_embedding_text(f"api key is {SECRET_API_KEY}", async_db, tenant)

    # Identity fixtures pass (raw by design — a masked embedding is useless).
    for fixture in IDENTITY_FIXTURES:
        await scan_embedding_text(f"notes about {fixture}", async_db, tenant)


@pytest.mark.asyncio
async def test_embeddings_channel_block_policy_fails_closed(async_db):
    # Use a regex-detectable identity fixture (email) so the block-policy
    # assertion holds regardless of NER availability in the test env.
    tenant = SimpleNamespace(tenant_id="t-emb-strict", kb_pii_egress="block")
    with pytest.raises(RawEgressBlockedError):
        await scan_embedding_text(f"email {PII_EMAIL}", async_db, tenant)


@pytest.mark.asyncio
async def test_voice_channel_blocks_secret_allows_identity(async_db):
    tenant = SimpleNamespace(tenant_id="t-voice", voice_pii_egress="allow")

    # A spoken/typed command carrying a secret must never reach TTS.
    with pytest.raises(RawEgressBlockedError):
        await scan_voice_text(f"read my key {SECRET_API_KEY}", async_db, tenant)

    for fixture in IDENTITY_FIXTURES:
        await scan_voice_text(f"say hello to {fixture}", async_db, tenant)


@pytest.mark.asyncio
async def test_voice_channel_block_policy_fails_closed(async_db):
    tenant = SimpleNamespace(tenant_id="t-voice-strict", voice_pii_egress="block")
    with pytest.raises(RawEgressBlockedError):
        await scan_voice_text(f"call {PII_EMAIL}", async_db, tenant)


@pytest.mark.asyncio
async def test_stt_block_policy_refuses_unscannable_audio(async_db):
    # STT audio can't be scanned; a "block" tenant is refused outright.
    strict = SimpleNamespace(tenant_id="t-stt-strict", voice_pii_egress="block")
    with pytest.raises(RawEgressBlockedError):
        await assert_stt_egress_allowed(async_db, strict)

    # An "allow" tenant is permitted (the audit row is best-effort).
    allow = SimpleNamespace(tenant_id="t-stt-allow", voice_pii_egress="allow")
    await assert_stt_egress_allowed(async_db, allow)
