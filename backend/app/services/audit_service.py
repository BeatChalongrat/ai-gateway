"""Audit service for the Secure AI Gateway.

Handles DLP audit event emission.  General request logging and DB
persistence are deferred to Phase 7.

Privacy guarantee: DLPAuditEntry records contain NO matched_text.
The conversion from MatchAction → DLPAuditEntry intentionally strips
the raw sensitive value.
"""

from __future__ import annotations

import structlog

from app.domain.dlp import DLPAuditEntry, EnforcementDecision, MatchAction
from app.domain.entities import RequestContext

logger = structlog.get_logger(__name__)


class AuditService:

    def emit_dlp_findings(
        self,
        match_actions: list[MatchAction],
        direction: str,
        field_path_prefix: str = "messages",
    ) -> list[DLPAuditEntry]:
        """Convert MatchActions to privacy-safe DLPAuditEntries and log them.

        Parameters
        ----------
        match_actions : list[MatchAction]
            The enforcement decisions containing detection matches.
        direction : str
            "input" or "output".
        field_path_prefix : str
            Base path for field_path (e.g. "messages" or "response_content").

        Returns
        -------
        list[DLPAuditEntry]
            The emitted entries (for testability).
        """
        entries: list[DLPAuditEntry] = []

        for ma in match_actions:
            entry = DLPAuditEntry(
                detector_name=ma.match.detector_name,
                entity_type=ma.match.entity_type,
                category=ma.match.category,
                severity=ma.match.severity.name.lower(),
                action=ma.action.name.lower(),
                field_path=f"{field_path_prefix}[{ma.match.start}:{ma.match.end}]",
                direction=direction,
            )
            entries.append(entry)

            logger.info(
                "dlp_finding",
                detector_name=entry.detector_name,
                entity_type=entry.entity_type,
                category=entry.category,
                severity=entry.severity,
                action=entry.action,
                field_path=entry.field_path,
                direction=entry.direction,
            )

        return entries

    def record_blocked_request(
        self,
        ctx: RequestContext,
        enforcement: EnforcementDecision,
        direction: str = "input",
    ) -> list[DLPAuditEntry]:
        """Record a blocked request and emit DLP audit entries.

        Returns the emitted entries for testability.
        """
        entries = self.emit_dlp_findings(
            enforcement.match_actions,
            direction=direction,
            field_path_prefix="messages" if direction == "input" else "response_content",
        )

        logger.warning(
            "request_blocked",
            policy_profile=enforcement.policy_profile_id,
            overall_action=enforcement.overall_action.name.lower(),
            direction=direction,
            finding_count=len(enforcement.match_actions),
            tenant_id=ctx.tenant_id,
            project_id=ctx.project_id,
            request_id=ctx.request_id,
        )

        return entries

    def record_success(
        self,
        ctx: RequestContext,
        route,
        enforcement: EnforcementDecision,
        usage,
        input_enforcement: EnforcementDecision | None = None,
        output_enforcement: EnforcementDecision | None = None,
    ) -> None:
        """Record a successful request with DLP enforcement details.

        Emits DLP findings for both input and output if provided.
        General request logging is deferred to Phase 7.
        """
        # Emit DLP findings for input side
        if input_enforcement and input_enforcement.match_actions:
            self.emit_dlp_findings(
                input_enforcement.match_actions,
                direction="input",
                field_path_prefix="messages",
            )

        # Emit DLP findings for output side
        if output_enforcement and output_enforcement.match_actions:
            self.emit_dlp_findings(
                output_enforcement.match_actions,
                direction="output",
                field_path_prefix="response_content",
            )

        # Structured audit event for the complete request
        logger.info(
            "request_audit",
            request_id=ctx.request_id,
            tenant_id=ctx.tenant_id,
            project_id=ctx.project_id,
            provider=getattr(route, "provider_name", ""),
            model=getattr(route, "model_name", ""),
            policy_action=enforcement.overall_action.name.lower(),
            input_findings=len(input_enforcement.match_actions) if input_enforcement else 0,
            output_findings=len(output_enforcement.match_actions) if output_enforcement else 0,
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            total_tokens=getattr(usage, "total_tokens", 0),
        )
