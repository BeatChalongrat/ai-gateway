"""Masking follows where the data goes.

Masking exists to stop data leaving the organisation. A caller the operator trusts to know where its data
may go can say, per request, that what it sends may not leave: ``X-Sran-Egress: closed``. When the model it
names runs in-house, the request reaches that model as it was typed — nothing leaves, so nothing is masked —
and the answer comes back as the model wrote it. Every other request is masked exactly as before.

A request is sent unmasked only when ALL of these hold (``decide``):

  * the caller sent ``X-Sran-Egress: closed``;
  * the caller's project is one the operator named in ``DLP_IN_HOUSE_PROJECTS`` (empty by default: the
    header then means nothing, whoever sends it);
  * the provider that will answer is in-house (``SELF_HOSTED_PROVIDERS``);
  * the request has no tools, is not a stream, does not use the secure calculator, and responses are
    scanned (so the log's copy of the answer is a masked one).

What still holds for a request sent unmasked:

  * a BLOCK still blocks — only the mask is not applied;
  * the request log, moderation and the guardrail are fed the MASKED copy, as before; the response cache is
    not read or written;
  * no provider outside the organisation is tried with it: the dispatcher refuses (``in_house_only``), and
    if the turn is rescued by another provider after all, that provider gets the masked copy and the answer
    is restored the usual way;
  * the caller is told what was done, not what it asked for: ``X-Gateway-Masking: off`` or ``on``.

The request log says so too: ``dlp_input_action = 'in_house_unmasked'`` on a row whose findings were sent
as typed (``'mask'`` keeps meaning the provider saw placeholders).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

HEADER = "X-Sran-Egress"
CLOSED = "closed"
# what the caller is told was done with its request
RESPONSE_HEADER = "X-Gateway-Masking"
MASKING_OFF = "off"
MASKING_ON = "on"
# request_logs.dlp_input_action for a row whose findings reached an in-house model as typed
LOG_ACTION = "in_house_unmasked"
# the reason added to the response's policy_reasons
REASON = "in_house_unmasked"

_asked: ContextVar[bool] = ContextVar("dlp_egress_closed_asked", default=False)


def header_says_closed(value: str | None) -> bool:
    """True when an ``X-Sran-Egress`` header value says the request's data may not leave."""
    return value is not None and value.strip().lower() == CLOSED


def mark_request() -> Token:
    """The current request (and the tasks it starts) said its data may not leave."""
    return _asked.set(True)


def asked() -> bool:
    return _asked.get()


def trusted_projects() -> frozenset[str]:
    """The projects whose ``X-Sran-Egress: closed`` is believed — the operator's list, empty by default."""
    from app.core.config import settings

    raw = getattr(settings, "dlp_in_house_projects", "") or ""
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def is_in_house(provider: str | None) -> bool:
    from app.services.providers.base_urls import SELF_HOSTED_PROVIDERS

    return bool(provider) and provider in SELF_HOSTED_PROVIDERS


@dataclass(frozen=True)
class Decision:
    unmasked: bool
    reason: str


def decide(
    *,
    asked: bool,
    project_id: str | None,
    provider: str | None,
    has_tools: bool = False,
    stream: bool = False,
    calc_active: bool = False,
    responses_scanned: bool = True,
) -> Decision:
    """Whether this request goes to its model as typed. The reason names the first condition that failed."""
    if not asked:
        return Decision(False, "not_asked")
    if not project_id or str(project_id) not in trusted_projects():
        return Decision(False, "caller_not_named")
    if not is_in_house(provider):
        return Decision(False, "outside_model")
    if stream:
        return Decision(False, "stream")
    if has_tools:
        return Decision(False, "tools")
    if calc_active:
        return Decision(False, "calculator")
    if not responses_scanned:
        return Decision(False, "responses_not_scanned")
    return Decision(True, "in_house")
