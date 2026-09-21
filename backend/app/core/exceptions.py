"""Domain exception hierarchy for the Secure AI Gateway.

These exceptions are pure application-level errors — no HTTP status codes,
no FastAPI imports.  The mapping from exception class to HTTP response lives
in middleware/error_handler.py.

Hierarchy
---------
GatewayError                       (base for all gateway domain errors)
├── ValidationError                (bad input / schema violation)
├── AuthenticationError            (identity unknown or credentials invalid)
├── AuthorizationError             (identity known but action not permitted)
├── NotFoundError                  (requested resource does not exist)
├── ConflictError                  (duplicate / state conflict)
├── PolicyViolationError           (gateway policy blocked the request)
│   └── DLPViolationError          (DLP/PII scan triggered a block)
├── RateLimitExceededError         (too many requests)
├── QuotaExceededError             (budget or token quota exhausted)
├── ProviderError                  (upstream LLM provider returned an error)
│   ├── ProviderTimeoutError       (upstream did not respond in time)
│   └── ProviderUnavailableError   (upstream is down or unreachable)
├── UpstreamBadResponseError       (upstream returned unparseable response)
├── ContextLengthExceededError     (request exceeds the model's context window)
├── NoCapableModelError            (auto routing: no model can serve a hard need)
├── NoAllowedProviderError         (the provider allow-list matches no candidate)
└── InternalServiceError           (unexpected internal failure)
"""

from __future__ import annotations

from typing import Any


class GatewayError(Exception):
    """Base exception for all gateway domain errors.

    Parameters
    ----------
    code : str
        Machine-readable error code (e.g. "authentication_error").
    message : str
        Human-readable description.
    details : dict | None
        Optional structured payload for the caller (field errors, etc.).
    """

    code: str = "gateway_error"
    message: str = "An unexpected gateway error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.code = code or self.__class__.code
        self.details = details
        self.headers = headers
        super().__init__(self.message)


# --- Authentication / Authorization ---

class AuthenticationError(GatewayError):
    code = "authentication_error"
    message = "Authentication required."


class AuthorizationError(GatewayError):
    code = "authorization_error"
    message = "You do not have permission to perform this action."


# --- Input / Resource ---

class ValidationError(GatewayError):
    code = "validation_error"
    message = "The request input is invalid."


class NotFoundError(GatewayError):
    code = "not_found"
    message = "The requested resource was not found."


class ConflictError(GatewayError):
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


# --- Policy / Security ---

class PolicyViolationError(GatewayError):
    code = "policy_violation"
    message = "The request was blocked by a gateway policy."


class DLPViolationError(PolicyViolationError):
    code = "dlp_violation"
    message = "The request was blocked due to sensitive data detection."


class GuardrailViolationError(PolicyViolationError):
    code = "guardrail_blocked"
    message = "The request was blocked by a content guardrail."


class ModerationViolationError(PolicyViolationError):
    code = "moderation_flagged"
    message = "The request was flagged by content moderation."


# --- Rate limit / Quota ---

class RateLimitExceededError(GatewayError):
    code = "rate_limit_exceeded"
    message = "Too many requests. Please retry later."


class QuotaExceededError(GatewayError):
    code = "quota_exceeded"
    message = "Usage quota or budget limit has been reached."


# --- Web search ---

class SearchDisabledError(GatewayError):
    code = "search_not_enabled"
    message = "Web search is not enabled for this tenant."


class SearchProvidersUnavailableError(GatewayError):
    code = "search_unavailable"
    message = "Web search is unavailable — no provider could answer."


class SearchQueryBlockedError(GatewayError):
    """The search query was withheld by DLP egress policy (Phase 9).

    Raised for the credential/secret class always, and for identity PII
    when the tenant's ``search_pii_egress`` policy is ``block``. The
    query is never masked (a masked query returns garbage results) —
    it is either sent verbatim or refused outright.
    """

    code = "search_query_blocked"
    message = "Search query blocked by DLP egress policy."


class RawEgressBlockedError(GatewayError):
    """Raw-egress content withheld by DLP egress policy (Phase 9 extension,
    FIX 2 — embeddings + voice channels).

    Raised for the credential/secret class ALWAYS (an API key inside a KB
    document or a spoken command must never egress), and for identity PII
    when the tenant's per-channel policy (``kb_pii_egress`` /
    ``voice_pii_egress``) is ``block``. The content is never masked — a
    masked embedding or masked TTS input is meaningless — so it is either
    sent verbatim (identity PII, default policy, audited) or refused
    outright. ``details['channel']`` carries the raw-egress channel.
    """

    code = "raw_egress_blocked"
    message = "Content blocked by DLP egress policy."


# --- Provider / Upstream ---

class ProviderError(GatewayError):
    code = "provider_error"
    message = "The upstream provider returned an error."


class ProviderAtCapacityError(ProviderError):
    """The provider's configured concurrency bulkhead is full.

    Raised by ``providers.concurrency.provider_slot`` when a slot can't
    be acquired within the timeout — load is shed instead of queued.
    Subclasses ProviderError so the dispatcher's fallback except-filter
    catches it (a full self-hosted box routes to a cloud candidate when
    one is configured); with no candidate it surfaces as 503 +
    Retry-After via the ``headers`` kwarg.
    """

    code = "provider_at_capacity"
    message = "Provider is at its configured concurrency limit. Retry shortly."


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"
    message = "The upstream provider did not respond in time."


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"
    message = "The upstream provider is currently unavailable."


class ProviderNotImplementedError(ProviderError):
    code = "provider_not_implemented"
    message = "Adapter implementation pending."


class ProviderKeyMissing(ProviderError):
    """No active provider key is available (UI / legacy / env all empty).

    Raised by an adapter when it needs a key but none resolves, so the
    caller fails closed BEFORE any provider HTTP call. Sprint 2B.5.
    """

    code = "provider_key_missing"
    message = "No active provider API key is configured."


class ProviderKeysStale(ProviderError):
    """The provider HAS keys, but every one of them needs re-testing.

    FIX57 Part 3. When a provider's endpoint changes, the stored verdict
    on each key ("valid", tested at some past moment) was reached against
    a different box, so the keys are marked ``stale`` and withheld from
    dispatch until an operator presses Test.

    Distinct from :class:`ProviderKeyMissing` because the two have
    identical symptoms and opposite fixes: one needs a key added, the
    other needs the keys that are already there re-checked. Telling an
    operator to configure a key they can see in the list is how a
    five-second fix becomes an afternoon.
    """

    code = "provider_keys_stale"
    message = (
        "This provider's endpoint changed; its keys must be tested again."
    )


class ModelNotServed(ProviderError):
    """The model was listed by a box that no usable key reaches.

    FIX85 Part C. Discovery records which key's endpoint reported each
    model. When every key at that endpoint is stale, invalid or gone,
    sending the request through another key of the same provider would
    reach a different box — the 401 / "no such model" the round exists
    to end — so dispatch names the situation instead.

    Not ``provider_keys_stale``: that one says "the endpoint moved", this
    one says "the box that has this model is unreachable through the
    keys you have". Not retryable; the fix is on the admin page.
    """

    code = "model_not_served"
    message = "No configured key currently reaches the endpoint that serves this model."


class UpstreamBadResponseError(GatewayError):
    code = "upstream_bad_response"
    message = "The upstream provider returned an unparseable response."


class ContextLengthExceededError(GatewayError):
    """The provider rejected the request because the prompt exceeds the
    model's context window.

    Deliberately NOT fallback-eligible (see ``FALLBACK_ELIGIBLE_ERRORS``):
    retrying a too-long prompt on another model wastes money — the client
    is responsible for trimming its conversation. ``details`` carries the
    provider name and the provider's original error message.
    """

    code = "context_length_exceeded"
    message = (
        "The request exceeds the model's context window. "
        "Shorten the conversation and try again."
    )


class NoCapableModelError(GatewayError):
    """Auto routing found no model that can serve a HARD need of the
    request (tools declared / images attached) among the models the
    caller may use.

    Raised instead of routing to a model that will fail: before FIX47 the
    capability filter "relaxed" an empty pool back to the whole pool, so a
    tools request with no tools-capable candidate landed on a tools-blind
    model and died with a generic "try again". Not fallback-eligible — no
    candidate can serve it. ``details.missing`` names the capabilities.
    """

    code = "no_capable_model"
    message = (
        "No available model supports what this request needs "
        "(tools or images)."
    )


class NoAllowedProviderError(GatewayError):
    """FIX126 O4 — the request's provider allow-list (``metadata.allowed_providers``,
    the project's, the region's, or the tenant's fallback-policy boundary)
    is non-empty and no provider in it can serve the request.

    Before this the router logged ``routing_no_compatible_provider`` and
    dispatched to the default provider (openai) anyway — the request body
    left for a provider the caller had excluded. Fail-closed: nothing is
    dispatched. Not retryable and not fallback-eligible — the same
    allow-list will match nothing again. ``details`` names the model and
    the allow-list.
    """

    code = "no_allowed_provider"
    message = (
        "No provider in the request's allowed providers can serve this "
        "model. Nothing was sent to any provider."
    )


# --- Internal ---

class InternalServiceError(GatewayError):
    code = "internal_error"
    message = "An internal service error occurred."
