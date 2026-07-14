"""Raw-egress policy for third-party calls (Gateway Phase 9).

The gateway's core invariant is "no user content reaches a third party
without DLP masking." Exactly TWO channels are exempt, because masking
would break the feature itself:

* ``web_search``  — a masked query ("prognosis for <PERSON_1>") returns
  garbage results; providers need the real query.
* ``media_gen``   — the prompt IS the creative intent; masking a name
  yields the wrong image/voice/video.

``RAW_EGRESS_ALLOWED`` is the machine-checked whitelist of those
channels. ``tests/test_dlp_egress_invariant.py`` enumerates every
outbound-to-third-party call site and fails if a site is neither masked
nor on this list — adding a third raw-egress path therefore requires
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
from app.core.exceptions import SearchQueryBlockedError

logger = structlog.get_logger(__name__)

# The ONLY channels allowed to send raw (unmasked) user content to a
# third party. Referenced by the egress scans below AND by the CI gate
# in tests/test_dlp_egress_invariant.py — edit both deliberately.
RAW_EGRESS_ALLOWED: frozenset[str] = frozenset({"web_search", "media_gen"})

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

    Reuses the request-log mechanism DLP block events already use (the
    image route persists BLOCKED RequestRecords the same way). The row
    carries the channel, category names + counts, and the
    ``raw_egress_allow`` marker — no query/prompt text, no matched
    values. Failure to audit never fails the request.
    """
    try:
        from app.domain.observability import RequestRecord, RequestStatus
        from app.services.orchestrator import GatewayOrchestrator

        counts = _category_counts(detection)
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
