"""What each gateway error code means to the person who sent the request.

The stream's ``error`` frame carries a machine-readable ``code``. Before
this table every frame also carried the same English sentence — "Model
unavailable, please try again" — whatever the code was, so a request the
model could never serve (tools on a tools-blind model, a prompt over the
context window) read as a transient outage and the retry button was
pressed three times for nothing.

Two things are decided here, per code:

* ``retryable`` — whether sending the same request again can succeed.
  A provider outage, a rate limit, a timeout: yes. A capability the
  model lacks, a parameter the provider rejected, a prompt that is too
  long, a policy block: no — the person has to change something.
* ``message`` — the English default the frame carries. The clients own
  localisation by ``code``; the message is what a client that does not
  know the code shows, so it must be correct for that code on its own.

A code missing from the table is treated as NOT retryable: the wrong
"try again" is the bug this file exists to end, and a new code that is
in fact transient costs one line here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    retryable: bool


_ENTRIES: tuple[ErrorInfo, ...] = (
    # ── transient: the same request may succeed a moment later ───────────
    ErrorInfo("provider_error", "The AI provider returned an error. Please try again.", True),
    ErrorInfo("provider_unavailable", "The AI provider is temporarily unavailable. Please try again.", True),
    ErrorInfo("provider_timeout", "The AI provider took too long to respond. Please try again.", True),
    ErrorInfo("provider_at_capacity", "The AI provider is at capacity right now. Please try again shortly.", True),
    ErrorInfo("provider_rate_limited", "The AI provider is rate-limiting requests. Please wait a moment and try again.", True),
    ErrorInfo("rate_limit_exceeded", "Too many requests. Please wait a moment and try again.", True),
    ErrorInfo("provider_model_not_found", "The provider did not serve the requested model. Please try again or pick another model.", True),
    ErrorInfo("all_models_failed", "No model could answer right now. Please try again or pick another model.", True),
    ErrorInfo("internal_error", "An internal error occurred. Please try again.", True),
    ErrorInfo("stream_interrupted", "The response was interrupted. Please try again.", True),
    # FIX49 Part A — the turn "completed" with no text and no tool call.
    # Whatever the upstream cause (an in-stream failure the parser
    # missed, a model that spent its whole budget reasoning), an empty
    # answer must never be reported as success: the user sees this
    # message and a working retry instead of a silent blank bubble.
    ErrorInfo("empty_completion", "The model returned an empty answer. Please try again or pick another model.", True),
    ErrorInfo("output_limit_reached", "The model reached its output limit before answering or calling a tool. Try again or choose another model for this task.", True),
    # per-attempt code in all_models_failed.attempts: the provider was skipped
    # because its health marker showed a recent failure
    ErrorInfo("health_marker_skip", "Skipped: this provider failed a moment ago. Please try again shortly.", True),
    # ── permanent for this request: retrying changes nothing ─────────────
    ErrorInfo("validation_error", "The request was rejected as invalid. Change the request before sending it again.", False),
    ErrorInfo("model_no_tool_support", "The selected model cannot use tools. Pick a model that supports tools, or turn the tools off.", False),
    ErrorInfo("model_no_vision_support", "The selected model cannot read images. Pick a model that supports images, or remove the image.", False),
    ErrorInfo("no_capable_model", "No available model can do what this request needs (tools or images) with your current access.", False),
    ErrorInfo("context_length_exceeded", "The conversation is too long for this model. Shorten it or start a new one.", False),
    ErrorInfo("upstream_bad_response", "The AI provider rejected the request. Change the request before sending it again.", False),
    ErrorInfo("quota_exceeded", "The usage quota has been reached. Contact your administrator.", False),
    ErrorInfo("insufficient_credits", "You're out of credits. Contact your administrator to top up.", False),
    # FIX51 Part 4 — a provider account that is out of credits (bare 402,
    # no Retry-After). Fallback-eligible (another ACCOUNT can answer) but
    # not user-retryable: the same request will keep failing until the
    # account is topped up.
    ErrorInfo("provider_no_credit", "The AI provider's account is out of credits. Contact your administrator.", False),
    ErrorInfo("provider_key_missing", "This provider has no API key configured. Contact your administrator.", False),
    ErrorInfo("provider_not_implemented", "This provider does not support the requested operation.", False),
    ErrorInfo("authentication_error", "The request was not authenticated.", False),
    ErrorInfo("authorization_error", "You are not allowed to do this.", False),
    ErrorInfo("policy_violation", "The request was blocked by policy.", False),
    ErrorInfo("dlp_violation", "The request was blocked because it contains data that must not leave the organisation.", False),
    ErrorInfo("guardrail_blocked", "The request was blocked by a safety guardrail.", False),
    ErrorInfo("moderation_flagged", "The request was flagged by content moderation.", False),
    ErrorInfo("content_policy_blocked", "The request was blocked by the provider's content policy.", False),
    # FIX57 Part 4 — a provider 401/403. Retrying is the one thing that
    # cannot help: the credential is wrong until someone changes it. This
    # used to arrive as the generic ``provider_error``, whose message ends
    # "Please try again" — advice that was not merely useless but pointed
    # away from the actual fix.
    ErrorInfo("provider_auth_error", "The provider rejected the gateway's credentials — this is a key problem, not a network one. Contact your administrator.", False),
    # FIX57 Part 3 — the provider's endpoint changed, so every stored key
    # verdict was reached against a different box and none has been
    # re-checked. Not "invalid": nobody knows yet.
    ErrorInfo("provider_keys_stale", "This provider's endpoint changed, so its API keys need to be tested again before it can be used. Contact your administrator.", False),
    ErrorInfo("raw_egress_blocked", "The request was blocked: sensitive data cannot be sent to this service.", False),
    ErrorInfo("search_not_enabled", "Web search is not enabled for your account.", False),
    ErrorInfo("search_unavailable", "Web search is unavailable right now.", False),
    ErrorInfo("search_query_blocked", "The search query was blocked because it contains sensitive data.", False),
    ErrorInfo("not_found", "The requested resource was not found.", False),
    ErrorInfo("conflict", "The request conflicts with the current state.", False),
    ErrorInfo("gateway_error", "The request could not be completed.", False),
)

CATALOG: dict[str, ErrorInfo] = {e.code: e for e in _ENTRIES}

_UNKNOWN_MESSAGE = "The request could not be completed."


def describe(code: str | None) -> ErrorInfo:
    """The catalog entry for ``code``; an unknown code is not retryable."""
    if code and code in CATALOG:
        return CATALOG[code]
    return ErrorInfo(code or "gateway_error", _UNKNOWN_MESSAGE, False)


def is_retryable(code: str | None) -> bool:
    return describe(code).retryable


def is_empty_completion(text: str | None, *, has_tool_calls: bool) -> bool:
    """Whether a finished turn qualifies as ``empty_completion``.

    The criterion is EMPTY, never SHORT: a one-word answer ("ใช่", "4")
    is a normal success, and a tool-call-only turn is a normal agentic
    turn. Both guards (streaming and non-streaming) decide through this
    one predicate so the criterion cannot drift into a length threshold
    on one side only.
    """
    return not (text or "").strip() and not has_tool_calls
