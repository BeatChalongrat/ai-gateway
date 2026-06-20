"""Dual canonical/display response artifacts (Phase 4A.2.1).

The gateway already restores DLP tokens to produce the user-visible
**display** response. This module captures the matching **canonical**
representation — the masked/tokenized, PRE-restore assistant content that
is safe to persist and to replay to the provider as history — and binds
it to the same server-generated generation id.

Phase 4A.2.1 produces + validates + (gated) transports the artifact. It
does NOT persist anything, does NOT change the display response, and is
inert unless ``DLP_CANONICAL_ARTIFACT_*`` flags are flipped in-process.
The artifact carries ONLY canonical content + integrity metadata — never
display text, raw restored values, raw owner ids, span maps, or vault
entries.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.services.dlp.policy_engine import (
    CHANNEL_WEBCHAT,
    CONV_SRC_TRUSTED_HEADER,
    SourceType,
)

ARTIFACT_SCHEMA_VERSION = 1

STATUS_COMPLETE = "complete"
STATUS_CANCELLED = "cancelled"
STATUS_ERROR = "error"
STATUS_UNAVAILABLE = "unavailable"
_ALLOWED_STATUS = frozenset(
    {STATUS_COMPLETE, STATUS_CANCELLED, STATUS_ERROR, STATUS_UNAVAILABLE}
)

# Internal transport identifiers. The non-stream extension is namespaced
# so public OpenAI clients ignore it; the SSE event names are distinct
# from any user-visible delta event.
EXTENSION_KEY = "x_sran_dlp"
CANONICAL_CAPABILITY_EVENT = "dlp.canonical_capability"
CANONICAL_FINAL_EVENT = "dlp.canonical_final"
CANONICAL_UNAVAILABLE_EVENT = "dlp.canonical_unavailable"


def capability_envelope(generation_id: str) -> dict[str, Any]:
    """B1 capability announcement — consumer distinguishes 'unsupported'."""
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "generation_id": generation_id,
        "supported": True,
    }


def unavailable_envelope(generation_id: str, error_code: str) -> dict[str, Any]:
    """B1 terminal 'artifact failed' envelope — never a raw exception/message."""
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "generation_id": generation_id,
        "status": STATUS_UNAVAILABLE,
        "error_code": error_code,
    }

# Placeholder shapes, for the ``canonical_contains_tokens`` shadow counter
# only (kept in lockstep with the transformer's token shapes).
_MASK_TOKEN_RE = re.compile(r"<r[A-Za-z0-9_\-]+>|<<[A-Z][A-Z0-9_]*>>")


class ArtifactError(ValueError):
    """Raised when an artifact fails integrity validation (fail closed)."""


def canonical_sha256(content: str) -> str:
    """Lowercase 64-char hex SHA-256 of the exact UTF-8 canonical bytes."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def count_tokens(content: str) -> int:
    return len(_MASK_TOKEN_RE.findall(content or ""))


@dataclass(frozen=True)
class CanonicalResponseArtifact:
    """Immutable canonical representation of one model generation.

    ``canonical_content`` is the post-outbound-DLP, PRE-restore assistant
    text (tokens intact). It contains NO display/raw restored value and
    NO raw owner id.
    """

    schema_version: int
    generation_id: str
    request_id: str
    scope_fingerprint: str | None
    channel: str
    streaming: bool
    status: str
    canonical_content: str
    canonical_sha256: str
    canonical_length: int
    created_at: int

    def validate(self) -> None:
        if self.schema_version != ARTIFACT_SCHEMA_VERSION:
            raise ArtifactError("schema_version")
        if not isinstance(self.canonical_content, str):
            raise ArtifactError("canonical_type")
        if self.canonical_length != len(self.canonical_content):
            raise ArtifactError("canonical_length")
        if self.canonical_sha256 != canonical_sha256(self.canonical_content):
            raise ArtifactError("canonical_digest")
        if self.status not in _ALLOWED_STATUS:
            raise ArtifactError("status")
        if not self.generation_id:
            raise ArtifactError("generation_id")

    def transport_dict(self) -> dict[str, Any]:
        """The wire form — canonical + integrity only, no raw ids/display."""
        return {
            "schema_version": self.schema_version,
            "generation_id": self.generation_id,
            "request_id": self.request_id,
            "canonical_content": self.canonical_content,
            "canonical_sha256": self.canonical_sha256,
            "canonical_length": self.canonical_length,
            "status": self.status,
        }


def build_canonical_artifact(
    *,
    generation_id: str,
    request_id: str,
    canonical_content: str,
    channel: str,
    streaming: bool,
    status: str,
    created_at: int,
    scope_fingerprint: str | None = None,
    forbidden_values: Iterable[str] | None = None,
) -> CanonicalResponseArtifact:
    """Build + validate a canonical artifact (fail closed on any issue).

    ``forbidden_values`` is the request-local set of restored raw values
    (display secrets). It is a DEFENSE-IN-DEPTH assertion that the
    canonical content never accidentally contains a restored value — a
    non-empty match raises :class:`ArtifactError` (caller marks the
    generation non-replayable). No raw value is ever logged here.
    """
    if not isinstance(canonical_content, str):
        raise ArtifactError("canonical_type")
    artifact = CanonicalResponseArtifact(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        generation_id=generation_id,
        request_id=request_id,
        scope_fingerprint=scope_fingerprint,
        channel=channel,
        streaming=streaming,
        status=status,
        canonical_content=canonical_content,
        canonical_sha256=canonical_sha256(canonical_content),
        canonical_length=len(canonical_content),
        created_at=int(created_at),
    )
    artifact.validate()
    if forbidden_values:
        for v in forbidden_values:
            # Only treat non-trivial values as leak signals (a 1-char value
            # legitimately appears in ordinary text — defense-in-depth, not
            # a detector). Never log the value.
            if v and len(v) >= 4 and v in canonical_content:
                raise ArtifactError("restored_value_in_canonical")
    return artifact


class CanonicalArtifactFinalizer:
    """Idempotent, per-generation canonical finalizer.

    The first ``finalize_once`` builds + caches the artifact; subsequent
    calls return the SAME artifact (never a different canonical content
    for one generation). Request-local; not shared across generations.
    """

    __slots__ = ("_artifact",)

    def __init__(self) -> None:
        self._artifact: CanonicalResponseArtifact | None = None

    @property
    def finalized(self) -> bool:
        return self._artifact is not None

    @property
    def artifact(self) -> CanonicalResponseArtifact | None:
        return self._artifact

    def finalize_once(self, **kwargs: Any) -> CanonicalResponseArtifact:
        if self._artifact is not None:
            return self._artifact
        self._artifact = build_canonical_artifact(**kwargs)
        return self._artifact


def canonical_artifact_transport_allowed(
    *,
    auth: Any,
    context: Any,
    enabled: bool,
) -> bool:
    """Gate for transporting the canonical artifact to the consumer.

    Requires ALL of: the feature flag enabled, a PROVEN internal-service
    trust signal (``auth.internal_trusted``), the managed WebChat channel,
    a conversation id from the TRUSTED internal header (not client body),
    and a complete authenticated scope (tenant/project/actor). Never
    relies on the channel string, body metadata, an unverified header,
    the model name, or the user-agent alone.
    """
    if not enabled:
        return False
    if auth is None or context is None:
        return False
    if not bool(getattr(auth, "internal_trusted", False)):
        return False
    if getattr(context, "channel", None) != CHANNEL_WEBCHAT:
        return False
    if getattr(context, "source_type", None) not in (
        SourceType.WEBCHAT,
        SourceType.DIRECT_API,
    ):
        # WEBCHAT managed traffic is labelled WEBCHAT by build_dlp_request_context;
        # DIRECT_API only passes here when internal_trusted already gated above.
        return False
    if getattr(context, "conversation_id_source", None) != CONV_SRC_TRUSTED_HEADER:
        return False
    if not getattr(context, "actor_id", None):
        return False
    if not getattr(context, "tenant_id", None) or not getattr(context, "project_id", None):
        return False
    # Phase 4A.2.7 — tenant-scoped: even a fully-trusted managed WebChat request
    # gets the canonical artifact ONLY when its tenant/project is in scope, so a
    # non-allowlisted tenant never emits canonical events (chat-edge → legacy).
    from app.services.dlp.feature_scope import is_in_scope

    if not is_in_scope(
        getattr(context, "tenant_id", None), getattr(context, "project_id", None)
    ):
        return False
    return True


def canonical_artifact_shadow_report(
    artifact: "CanonicalResponseArtifact | None",
    *,
    display_length: int,
    diverged: bool,
) -> dict[str, Any]:
    """Sanitized shadow counters — NO canonical/display text, NO raw ids."""
    if artifact is None:
        return {"artifact_created": False}
    return {
        "artifact_created": True,
        "status": artifact.status,
        "channel": artifact.channel,
        "streaming": artifact.streaming,
        "canonical_length": artifact.canonical_length,
        "display_length": int(display_length),
        "canonical_contains_tokens": count_tokens(artifact.canonical_content) > 0,
        "diverged": bool(diverged),
        "scope_valid": artifact.scope_fingerprint is not None,
    }


def safe_exception_fields(exc: BaseException, code: str) -> dict[str, str]:
    """Log-safe exception fields (F1) — NEVER the raw exception message."""
    return {"error_code": code, "exception_type": type(exc).__name__}
