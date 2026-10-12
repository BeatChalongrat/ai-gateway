"""Who receives a request — the route the gateway calls, and the company whose machines run the model.

A provider row is a route. For a direct row the route and the company are one: what is sent to
``deepseek`` is seen by DeepSeek. A router is a route only: behind OpenRouter a model is run by its maker
or by a hosting company, and unless it is told otherwise OpenRouter picks one per request. Measured on
13 Oct 2026 with this deployment's own key (scripts/probes/AM16/openrouter_pin.py): a request for
``anthropic/claude-sonnet-4.5`` was answered by Amazon Bedrock, ``openai/gpt-4o`` by Azure,
``deepseek/deepseek-chat`` by DeepInfra and a Llama model by Parasail — none of them the maker, and
nothing in the model's name said so. Only Google's models were run by Google.

So the company in a recipient is the one that RUNS the model, not the one that made it. A caller that
must reach named recipients only says so with ``metadata.allowed_recipients``: a list of recipient keys,
``<route>`` for a direct row and ``<route>:<company>`` behind a router. Then, and only then:

* an in-house provider is not a recipient — nothing leaves — and is always allowed;
* any other call is refused before anything is sent unless a company that can run it is on the list
  (``no_allowed_provider``). The check is made where a provider's adapter is called, so the first
  attempt, a second route and a rescue by another model are held by the same line;
* behind a router the request is pinned (``router_pin``) to the endpoints of the companies on the
  list — only those, no falling back to another host, and no endpoint that keeps what it is sent. With
  none of them up, or none allowed by the router account's own data policy, the request fails at the
  router; it is not re-routed;
* a model no company in the table can be said to run — a router's own "auto", an open model served by
  whoever hosts it that day — has no key, so it can be on no list: with a list present it is refused;
* the list present and empty means nobody outside. Absent, the caller did not ask and nothing changes.

The table holds what was seen to answer, and the names of the endpoints are OpenRouter's own
(GET https://openrouter.ai/api/v1/providers). A maker or a host that is not in it has no key here until
someone adds it — after seeing it answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.core.exceptions import NoAllowedProviderError
from app.services.providers.base_urls import SELF_HOSTED_PROVIDERS

METADATA_KEY = "allowed_recipients"

# A route that hands the request on to another company.
ROUTERS = frozenset({"openrouter"})

# Behind OpenRouter: the maker named at the front of the model id -> the companies that can run its
# models there, each with OpenRouter's names for that company's endpoints. The maker itself is one of
# them where it serves its own models; a company hosting another's model is a recipient in its own right.
OPENROUTER_HOSTS: dict[str, dict[str, tuple[str, ...]]] = {
    "google": {"google": ("google-ai-studio", "google-vertex")},
    "anthropic": {"anthropic": ("anthropic",), "amazon": ("amazon-bedrock",), "google": ("google-vertex",)},
    "openai": {"openai": ("openai",), "microsoft": ("azure",)},
}


@dataclass(frozen=True)
class Recipient:
    """Where a call may go. ``key`` is what a list holds."""

    route: str
    company: str
    in_house: bool = False

    @property
    def key(self) -> str:
        if self.in_house:
            return ""
        return self.route if self.company == self.route else f"{self.route}:{self.company}"


def _maker(model: str) -> str:
    return (model or "").split("/", 1)[0] if "/" in (model or "") else ""


def recipients_of(provider: str, model: str) -> tuple[Recipient, ...]:
    """Every company that may run this model on this route. Empty: no company can be named."""
    provider = (provider or "").strip()
    if provider in SELF_HOSTED_PROVIDERS:
        return (Recipient(route=provider, company=provider, in_house=True),)
    if provider == "openrouter":
        return tuple(Recipient(route=provider, company=c) for c in OPENROUTER_HOSTS.get(_maker(model), {}))
    if provider in ROUTERS:  # a router nobody has written the table for: no company can be named
        return ()
    return (Recipient(route=provider, company=provider),)


def keys_of(provider: str, model: str) -> list[str]:
    """The recipient keys a list may hold for this model — none for an in-house one."""
    return [r.key for r in recipients_of(provider, model) if not r.in_house]


def asked(metadata: Any) -> frozenset[str] | None:
    """The caller's list, or ``None`` when it sent none. An empty list is a list: nobody outside.

    The key being there is what counts. ``null`` is what an empty list becomes in more than one
    language's JSON, so it is read as the empty list — never as "did not ask"."""
    if not isinstance(metadata, dict) or METADATA_KEY not in metadata:
        return None
    value = metadata.get(METADATA_KEY)
    if value is None or isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return frozenset()  # (a list that is empty, null or cannot be read allows nobody)
    return frozenset(str(v).strip() for v in value if str(v).strip())


def require(provider: str, model: str, metadata: Any) -> None:
    """Refuse the call — before a byte is sent — when the caller named its recipients and none of the
    companies that can run this model is one of them."""
    allowed = asked(metadata)
    if allowed is None:
        return
    possible = recipients_of(provider, model)
    if any(r.in_house for r in possible):
        return
    if any(r.key in allowed for r in possible):
        return
    raise NoAllowedProviderError(
        f"'{model}' on '{provider}' is not one of the recipients this request may reach. "
        "Nothing was sent to any provider.",
        details={
            "model": model,
            "model_provider": provider,
            "recipients": [r.key for r in possible],
            "allowed_recipients": sorted(allowed),
            "reason": "recipient_not_allowed" if possible else "recipient_cannot_be_named",
        },
    )


def allowed(provider: str, model: str, metadata: Any) -> bool:
    """Whether ``require`` would let this call through."""
    try:
        require(provider, model, metadata)
    except NoAllowedProviderError:
        return False
    return True


def router_pin(provider: str, model: str, metadata: Any) -> dict | None:
    """OpenRouter's ``provider`` object for a request that named its recipients, else ``None``.

    Only the endpoints of the companies on the list that can run this model, no fallback to another
    host, and only endpoints that do not keep what they are sent. With none of them available the
    request fails at OpenRouter; it is not re-routed."""
    named = asked(metadata)
    if provider != "openrouter" or named is None:
        return None
    endpoints: list[str] = []
    for company, slugs in OPENROUTER_HOSTS.get(_maker(model), {}).items():
        if f"{provider}:{company}" in named:
            endpoints.extend(s for s in slugs if s not in endpoints)
    if not endpoints:
        return None  # (no company of the list runs it: ``require`` has refused this call already)
    return {"only": endpoints, "allow_fallbacks": False, "data_collection": "deny"}
