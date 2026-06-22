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


# --- Provider / Upstream ---

class ProviderError(GatewayError):
    code = "provider_error"
    message = "The upstream provider returned an error."


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


class UpstreamBadResponseError(GatewayError):
    code = "upstream_bad_response"
    message = "The upstream provider returned an unparseable response."


# --- Internal ---

class InternalServiceError(GatewayError):
    code = "internal_error"
    message = "An internal service error occurred."
