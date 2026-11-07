"""What a trusted caller may say about its own data: where it may go, and whether it is masked.

Masking exists to protect data on its way out of the organisation. A caller the operator trusts to know
its own data (``DLP_IN_HOUSE_PROJECTS`` — empty by default) can say two things per request:

  * ``X-Sran-Egress: closed`` — this request's data may not leave the organisation;
  * ``X-Sran-Masking: off`` or ``on`` — whether personal data in it is to be masked before a model
    reads it. This is the caller's own setting (an admin's switch on the caller's side, off by
    default), and since 12 Oct 2026 it no longer follows the first: a request whose data MAY leave
    and that says ``off`` is sent to a model outside the organisation as it was typed.

A caller that sends only ``X-Sran-Egress: closed`` (an older one) is read as before: as typed for an
in-house model, masked for any other. A caller that sends neither is masked exactly as it always was.

A request is sent as typed only when ALL of these hold (``decide``):

  * it did not say ``X-Sran-Masking: on``, and it said ``off`` (or, the older way, ``closed`` alone);
  * the caller's project is one the operator named — otherwise the headers mean nothing, whoever
    sends them;
  * the provider that will answer is in-house (``SELF_HOSTED_PROVIDERS``), OR the request said
    ``off`` and did not say ``closed``: then it may go to a provider outside, as typed;
  * for a provider outside: the text holds nothing of the credential class (a key, a password, a
    secret). Such a request keeps its mask whatever was asked — this is the one thing every raw
    channel of the gateway still holds back (``egress_policy.RAW_EGRESS_ALLOWED``);
  * the request has no tools, is not a stream, does not use the secure calculator, and responses are
    scanned (so the log's copy of the answer is a masked one).

What still holds for a request sent as typed:

  * a BLOCK still blocks — only the mask is not applied;
  * the request log, moderation and the guardrail are fed the MASKED copy, as before; the response cache is
    not read or written;
  * a request that may not leave is tried with no provider outside the organisation: the dispatcher
    refuses (``in_house_only``); and no request sent as typed is rescued by another model — if a turn is
    rescued after all, that provider gets the masked copy and the answer is restored the usual way;
  * the caller is told what was done, not what it asked for: ``X-Gateway-Masking: off`` or ``on``.

What the gateway masks is TEXT. The image of a page is sent to the model named as it is, on every path,
and always was: nothing here changes that, and nothing here may be read as saying an image was masked.

The request log says what happened: ``dlp_input_action = 'in_house_unmasked'`` on a row whose findings
reached an in-house model as typed, ``'sent_as_typed'`` on a row whose findings reached a provider
outside as typed (``'mask'`` keeps meaning the provider saw placeholders).
"""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

HEADER = "X-Sran-Egress"
CLOSED = "closed"
# the caller's own word on masking: "off" (send the text as typed) or "on" (mask it)
MASKING_HEADER = "X-Sran-Masking"
# what the caller is told was done with its request
RESPONSE_HEADER = "X-Gateway-Masking"
MASKING_OFF = "off"
MASKING_ON = "on"
# request_logs.dlp_input_action for a row whose findings reached an in-house model as typed
LOG_ACTION = "in_house_unmasked"
# the reason added to the response's policy_reasons
REASON = "in_house_unmasked"
# both of those for a row whose findings reached a provider OUTSIDE the organisation as typed
SENT_AS_TYPED = "sent_as_typed"
# the raw-egress channel this is (egress_policy.RAW_EGRESS_ALLOWED)
CHANNEL = "agent_documents"
# findings of these classes keep the mask on the way out, whatever the caller asked
CREDENTIAL_CLASSES = frozenset({"credential", "secret"})

_asked: ContextVar[bool] = ContextVar("dlp_egress_closed_asked", default=False)
_masking: ContextVar[str | None] = ContextVar("dlp_masking_asked", default=None)


def header_says_closed(value: str | None) -> bool:
    """True when an ``X-Sran-Egress`` header value says the request's data may not leave."""
    return value is not None and value.strip().lower() == CLOSED


def header_masking(value: str | None) -> str | None:
    """``off`` or ``on`` when an ``X-Sran-Masking`` header value says one of the two; None for anything else."""
    word = (value or "").strip().lower()
    return word if word in (MASKING_OFF, MASKING_ON) else None


def mark_request() -> Token:
    """The current request (and the tasks it starts) said its data may not leave."""
    return _asked.set(True)


def mark_masking(word: str) -> Token:
    """The current request (and the tasks it starts) said ``off`` or ``on`` about masking."""
    return _masking.set(word)


def asked() -> bool:
    return _asked.get()


def masking_asked() -> str | None:
    return _masking.get()


def trusted_projects() -> frozenset[str]:
    """The projects whose word on their own data is believed — the operator's list, empty by default."""
    from app.core.config import settings

    raw = getattr(settings, "dlp_in_house_projects", "") or ""
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def is_in_house(provider: str | None) -> bool:
    from app.services.providers.base_urls import SELF_HOSTED_PROVIDERS

    return bool(provider) and provider in SELF_HOSTED_PROVIDERS


def has_credential_class(match_actions: Iterable[Any]) -> bool:
    """True when a finding of the request is of the credential class (a key, a password, a secret)."""
    for ma in match_actions or ():
        category = getattr(getattr(ma, "match", None), "category", "") or ""
        if str(category).lower() in CREDENTIAL_CLASSES:
            return True
    return False


@dataclass(frozen=True)
class Decision:
    unmasked: bool
    reason: str
    # the request goes, as typed, to a provider outside the organisation
    outside: bool = False


def decide(
    *,
    asked: bool,
    project_id: str | None,
    provider: str | None,
    has_tools: bool = False,
    stream: bool = False,
    calc_active: bool = False,
    responses_scanned: bool = True,
    masking: str | None = None,
    credential_found: bool = False,
) -> Decision:
    """Whether this request goes to its model as typed. The reason names the first condition that failed."""
    if masking == MASKING_ON:
        return Decision(False, "masking_on")
    if masking != MASKING_OFF and not asked:
        return Decision(False, "not_asked")
    if not project_id or str(project_id) not in trusted_projects():
        return Decision(False, "caller_not_named")
    outside = not is_in_house(provider)
    if outside and (asked or masking != MASKING_OFF):
        # the data may not leave, or the caller never said it may go out as typed
        return Decision(False, "outside_model")
    if stream:
        return Decision(False, "stream")
    if has_tools:
        return Decision(False, "tools")
    if calc_active:
        return Decision(False, "calculator")
    if not responses_scanned:
        return Decision(False, "responses_not_scanned")
    if outside and credential_found:
        return Decision(False, "credential_found")
    if outside:
        return Decision(True, SENT_AS_TYPED, outside=True)
    return Decision(True, "in_house")
