"""Raw-egress policy for third-party calls (Gateway Phase 9).

Standard completion text is DLP-masked before external dispatch. Five
registered raw-egress channels have explicit policy gates:

* ``web_search``  — a masked query ("prognosis for <PERSON_1>") returns
  garbage results; providers need the real query.
* ``media_gen``   — the prompt IS the creative intent; masking a name
  yields the wrong image/voice/video.
* ``embeddings`` — document/query text for vector generation.
* ``voice`` — transcription audio and speech synthesis text.
* ``agent_documents`` — trusted-caller decision in in_house.py; see below.

``RAW_EGRESS_ALLOWED`` is the machine-checked whitelist of those
channels. ``tests/test_dlp_egress_invariant.py`` enumerates every
outbound-to-third-party call site and fails if a site is neither masked
nor on this list — adding another raw-egress path therefore requires
consciously editing this constant (and the disclosure doc,
``docs/DLP_ARCHITECTURE.md`` § "Intentional raw egress").

Even the whitelisted channels are not a free-for-all:

1. The credential/secret class ALWAYS blocks (you never need a leaked
   API key to run a search) — enforced via the ``media_gen`` profile,
   whose explicit ``pii/identity/network → ALLOW`` beats the CRITICAL
   severity threshold while ``credential/secret/financial → BLOCK``.
2. Per-tenant ``search_pii_egress`` / ``media_pii_egress`` = ``block``
   makes the scan fail closed on ANY identity PII (compliance-strict
   tenants). Default ``allow`` preserves the pass-through behavior.
3. Every allowed raw egress that actually carried identity PII is
   audit-flagged (request_logs row with ``raw_egress: true`` + category
   COUNTS — never the values), so a tenant's compliance view can answer
   "which searches/prompts left unmasked."
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import RawEgressBlockedError, SearchQueryBlockedError

logger = structlog.get_logger(__name__)

# The ONLY channels allowed to send raw (unmasked) user content to a
# third party. Referenced by the egress scans below AND by the CI gate
# in tests/test_dlp_egress_invariant.py — edit both deliberately.
#
# FIX 2 added ``embeddings`` (KB indexing + RAG query text) and ``voice``
# (TTS input text + STT audio): the gateway now fronts these so chat-edge
# stops calling OpenAI directly, and they run behind the same
# credential-block + per-tenant-PII + audit gate as search/media. Like a
# masked query, a masked embedding of "<PERSON_1>" or masked audio is
# useless — so these channels send raw, but never the credential class.
#
# 12 Oct 2026 added ``agent_documents``: a chat completion of a caller the
# operator named (DLP_IN_HOUSE_PROJECTS) that says its data may leave
# and is not to be masked. The owner's decision:
# reading a document correctly matters more than masking it on the way,
# and masking is the admin's own switch per agent, off by default. Its
# gate is app/services/dlp/in_house.py ``decide`` (not the scans below):
# the same credential class is held back — a request with such a finding
# keeps its mask — and the request's own log row is the audit record
# (``dlp_input_action = 'sent_as_typed'``, findings counted by category).
RAW_EGRESS_ALLOWED: frozenset[str] = frozenset(
    {"web_search", "media_gen", "embeddings", "voice", "agent_documents"}
)

# Marker written into request_logs.policy_action / dlp_input_action for
# an allowed raw egress that carried identity PII.
RAW_EGRESS_POLICY_ACTION = "raw_egress_allow"


def assert_egress_allowed(channel: str) -> None:
    """Guard called by every raw-egress scan. A channel not on the
    whitelist is a programming error — fail loudly, never silently leak."""
    if channel not in RAW_EGRESS_ALLOWED:
        raise RuntimeError(
            f"Raw egress channel '{channel}' is not whitelisted. Either mask "
            "the content or consciously add the channel to "
            "app.services.dlp.egress_policy.RAW_EGRESS_ALLOWED (and update "
            "docs/DLP_ARCHITECTURE.md + tests/test_dlp_egress_invariant.py)."
        )


def _category_counts(detection: Any) -> dict[str, int]:
    """Per-category finding counts — categories only, NEVER values."""
    return dict(Counter(m.category for m in getattr(detection, "matches", [])))


async def _persist_raw_egress_audit(
    db: AsyncSession,
    *,
    channel: str,
    tenant_id: str,
    project_id: str,
    user_id: str | None,
    counts: dict[str, int],
) -> None:
    """Write one best-effort audit row for an ALLOWED raw egress.

    Reuses the request-log mechanism DLP block events already use (the
    image route persists BLOCKED RequestRecords the same way). The row
    carries the channel, category names + counts, and the
    ``raw_egress_allow`` marker — no query/prompt/transcript text, no
    matched values. Failure to audit never fails the request.
    """
    try:
        from app.domain.observability import RequestRecord, RequestStatus
        from app.services.orchestrator import GatewayOrchestrator

        record = RequestRecord(
            request_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            project_id=project_id,
            provider=channel,
            model="-",
            status=RequestStatus.SUCCESS,
            policy_action=RAW_EGRESS_POLICY_ACTION,
            dlp_input_findings=sum(counts.values()),
            dlp_input_action=RAW_EGRESS_POLICY_ACTION,
            dlp_categories=sorted(counts),
            dlp_findings_json=json.dumps(
                {"raw_egress": True, "channel": channel, "categories": counts},
                ensure_ascii=False,
            ),
            user_id=user_id,
        )
        await GatewayOrchestrator().request_log_service.persist_async(db, record)
        logger.info(
            "raw_egress_allowed",
            channel=channel,
            tenant_id=tenant_id,
            categories=counts,
        )
    except Exception as exc:  # noqa: BLE001 — audit must never fail the request
        logger.warning("raw_egress_audit_failed", channel=channel, error=str(exc))


async def audit_raw_egress(
    db: AsyncSession,
    *,
    channel: str,
    tenant_id: str,
    project_id: str,
    user_id: str | None,
    detection: Any,
) -> None:
    """Best-effort audit row for an ALLOWED raw egress carrying identity PII.

    Thin wrapper over :func:`_persist_raw_egress_audit` that derives the
    category counts from a DLP detection result. Used by every text-based
    raw-egress scan (search, embeddings, voice-TTS). Never fails the request.
    """
    await _persist_raw_egress_audit(
        db,
        channel=channel,
        tenant_id=tenant_id,
        project_id=project_id,
        user_id=user_id,
        counts=_category_counts(detection),
    )


async def _scan_raw_egress_text(
    text: str,
    db: AsyncSession,
    tenant: Any,
    *,
    channel: str,
    policy_attr: str,
    project_id: str = "",
    user_id: str | None = None,
) -> None:
    """Shared credential-block + per-tenant-PII + audit gate for a raw-egress
    TEXT channel. Channel-parameterised twin of :func:`scan_search_query`
    (which predates this and is kept verbatim for stability).

    The text is deliberately NOT masked — the feature needs the real content
    (a masked embedding / masked TTS input is meaningless). Instead:

    * credential/secret(/financial) detections → ``RawEgressBlockedError``
      (403), via the ``media_gen`` profile whose explicit ALLOW on the
      identity classes beats the CRITICAL severity threshold;
    * identity PII passes through RAW under the default tenant policy, with
      an audit row recording that it did;
    * tenant ``<policy_attr> = "block"`` fails closed on ANY identity PII.

    No-op when DLP is disabled or the text is empty.
    """
    if not settings.dlp_enabled:
        return
    assert_egress_allowed(channel)

    text = (text or "").strip()
    if not text:
        return

    from app.services.orchestrator import GatewayOrchestrator
    from app.services.policy.profile_service import get_runtime_profile_async

    orch = GatewayOrchestrator()
    profile = await get_runtime_profile_async(db, "media_gen")
    # A tenant on the "block" policy promises fail-closed on ANY identity
    # PII — names are NER-only findings, so the block path must scan with
    # NER enabled or a bare name produces zero detections and slips
    # through. The default "allow" path keeps the cheap regex-only scan
    # (only the credential class matters there). Mirrors scan_search_query.
    policy_blocks = (
        str(getattr(tenant, policy_attr, "allow") or "allow") == "block"
    )
    detection = await orch._scan_text_as_result_async(
        text, direction="input", ner_enabled=policy_blocks
    )
    enforcement = orch._scan_and_evaluate(detection, profile)

    if enforcement.is_blocked:
        raise RawEgressBlockedError(
            "Content contains credential/secret material and was not sent.",
            details={
                "channel": channel,
                "categories": sorted(
                    {ma.match.category for ma in enforcement.match_actions}
                ),
            },
        )

    if not enforcement.match_actions:
        return  # clean text — nothing to police or audit

    # Identity-class findings only from here on (blocked classes raised).
    tenant_id = str(getattr(tenant, "tenant_id", "") or "")
    if policy_blocks:
        raise RawEgressBlockedError(
            "Content contains personal data and this tenant's policy blocks "
            "unmasked egress on this channel.",
            details={"channel": channel, "policy": "block"},
        )

    await audit_raw_egress(
        db,
        channel=channel,
        tenant_id=tenant_id,
        project_id=project_id,
        user_id=user_id,
        detection=detection,
    )


async def scan_embedding_text(
    text: str,
    db: AsyncSession,
    tenant: Any,
    *,
    project_id: str = "",
    user_id: str | None = None,
) -> None:
    """DLP egress gate for the embeddings channel (FIX 2).

    Covers both raw-egress embedding paths: KB **document chunks** on index
    and the **RAG query** text on search. Credential/secret → 403; identity
    PII passes raw (audited) unless the tenant's ``kb_pii_egress`` is
    ``block``. No-op when DLP is disabled.
    """
    await _scan_raw_egress_text(
        text,
        db,
        tenant,
        channel="embeddings",
        policy_attr="kb_pii_egress",
        project_id=project_id,
        user_id=user_id,
    )


async def scan_voice_text(
    text: str,
    db: AsyncSession,
    tenant: Any,
    *,
    project_id: str = "",
    user_id: str | None = None,
) -> None:
    """DLP egress gate for voice TTS input text (FIX 2).

    The text-to-speak may carry PII/secrets. Credential/secret → 403;
    identity PII passes raw (audited) unless ``voice_pii_egress`` is
    ``block``. No-op when DLP is disabled.
    """
    await _scan_raw_egress_text(
        text,
        db,
        tenant,
        channel="voice",
        policy_attr="voice_pii_egress",
        project_id=project_id,
        user_id=user_id,
    )


async def assert_stt_egress_allowed(
    db: AsyncSession,
    tenant: Any,
    *,
    project_id: str = "",
    user_id: str | None = None,
) -> None:
    """DLP egress gate for voice speech-to-text — the RAW AUDIO path (FIX 2).

    Audio cannot be secret/PII-scanned before transcription (there is no
    text to scan yet — a documented limitation), so this gate is coarser
    than the text channels:

    * tenant ``voice_pii_egress = "block"`` → refuse STT outright (fail
      closed: a strict tenant cannot guarantee the audio is PII-free);
    * otherwise the raw-audio egress is audit-flagged (channel ``voice``,
      category marker ``audio`` — no content, no transcript) so a
      compliance view can see that unscannable audio left.

    The credential class cannot be enforced on opaque audio here; it IS
    enforced downstream — the transcript re-enters the MASKED chat
    completion pipeline. No-op when DLP is disabled.
    """
    if not settings.dlp_enabled:
        return
    assert_egress_allowed("voice")
    policy_blocks = (
        str(getattr(tenant, "voice_pii_egress", "allow") or "allow") == "block"
    )
    if policy_blocks:
        raise RawEgressBlockedError(
            "This tenant's policy blocks unmasked voice egress and speech "
            "audio cannot be scanned before transcription.",
            details={"channel": "voice", "policy": "block", "reason": "stt_audio"},
        )
    await _persist_raw_egress_audit(
        db,
        channel="voice",
        tenant_id=str(getattr(tenant, "tenant_id", "") or ""),
        project_id=project_id,
        user_id=user_id,
        counts={"audio": 1},
    )


async def scan_search_query(
    query: str,
    db: AsyncSession,
    tenant: Any,
    *,
    project_id: str = "",
    user_id: str | None = None,
) -> None:
    """DLP egress scan for a web-search query (mirrors the media template).

    The query is deliberately NOT masked — search needs the real query
    to return useful results; masking "prognosis for คุณสมชาย" into
    "prognosis for <PERSON_1>" yields garbage. Instead:

    * credential/secret(/financial) detections → 403
      ``search_query_blocked`` (via the ``media_gen`` profile, whose
      explicit ALLOW on identity classes beats the severity threshold);
    * identity PII passes through RAW under the default tenant policy,
      with an audit row recording that it did;
    * tenant ``search_pii_egress = "block"`` fails closed on ANY
      identity PII instead.

    No-op when DLP is disabled.
    """
    if not settings.dlp_enabled:
        return
    assert_egress_allowed("web_search")

    from app.services.orchestrator import GatewayOrchestrator
    from app.services.policy.profile_service import get_runtime_profile_async

    orch = GatewayOrchestrator()
    profile = await get_runtime_profile_async(db, "media_gen")
    # Phase 10 (A2): a tenant on the "block" policy promises fail-closed on
    # ANY identity PII — names are NER-only findings, so the block path
    # must scan with NER enabled or a bare name produces zero detections
    # and slips through. The default "allow" path keeps the cheap
    # regex-only scan (only the credential class matters there).
    policy_blocks = (
        str(getattr(tenant, "search_pii_egress", "allow") or "allow") == "block"
    )
    detection = await orch._scan_text_as_result_async(
        query, direction="input", ner_enabled=policy_blocks
    )
    enforcement = orch._scan_and_evaluate(detection, profile)

    if enforcement.is_blocked:
        raise SearchQueryBlockedError(
            "Search query contains credential/secret content and was not sent.",
            details={
                "channel": "web_search",
                "categories": sorted(
                    {ma.match.category for ma in enforcement.match_actions}
                ),
            },
        )

    if not enforcement.match_actions:
        return  # clean query — nothing to police or audit

    # Identity-class findings only from here on (blocked classes raised).
    tenant_id = str(getattr(tenant, "tenant_id", "") or "")
    if policy_blocks:
        raise SearchQueryBlockedError(
            "Search query contains personal data and this tenant's policy "
            "blocks unmasked search egress.",
            details={"channel": "web_search", "policy": "block"},
        )

    await audit_raw_egress(
        db,
        channel="web_search",
        tenant_id=tenant_id,
        project_id=project_id,
        user_id=user_id,
        detection=detection,
    )
