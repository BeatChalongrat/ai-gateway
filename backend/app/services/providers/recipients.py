"""Who receives a request — the route the gateway calls, and the company whose machines run the model.

A provider row is a route. For a direct row the route and the company are one: what is sent to
``deepseek`` is seen by DeepSeek. A router is a route only: behind OpenRouter a model is run by its maker
or by a hosting company, and unless it is told otherwise OpenRouter picks one per request — a page sent
to ``google/gemini-2.5-flash`` through it is seen by OpenRouter and by whoever ran it.

A caller that must reach named recipients only says so with ``metadata.allowed_recipients``: a list of
recipient keys, ``<route>`` for a direct row and ``<route>:<company>`` behind a router. Then, and only
then:

* an in-house provider is not a recipient — nothing leaves — and is always allowed;
* any other call is refused before anything is sent unless its recipient is on the list
  (``no_allowed_provider``). The check is made where a provider's adapter is called, so the first
  attempt, a second route and a rescue by another model are held by the same line;
* a model whose company cannot be named — a router's own "auto", a model with no maker's endpoint on
  that router — has no key, so it can be on no list: with a list present it is refused;
* the list present and empty means nobody outside. Absent, the caller did not ask and nothing changes;
* behind a router the request is pinned (``router_pin``): the company's own endpoints only, no
  falling back to another host, and no endpoint that keeps what it is sent.

The names of OpenRouter's endpoints are its own (GET https://openrouter.ai/api/v1/providers, read
12 Oct 2026). A maker that is not in the table has no key here until someone adds it with its endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.core.exceptions import NoAllowedProviderError
from app.services.providers.base_urls import SELF_HOSTED_PROVIDERS

METADATA_KEY = "allowed_recipients"

# A route that hands the request on to another company.
ROUTERS = frozenset({"openrouter"})

# Behind OpenRouter: the maker named at the front of the model id -> (the company, OpenRouter's names for
# that company's OWN endpoints). Another company hosting the same model (Azure for OpenAI's, Amazon or
# Google for Anthropic's) is another recipient and is not listed.
OPENROUTER_MAKERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "openai": ("openai", ("openai",)),
    "anthropic": ("anthropic", ("anthropic",)),
    "google": ("google", ("google-ai-studio", "google-vertex")),
    "deepseek": ("deepseek", ("deepseek",)),
    "x-ai": ("xai", ("xai",)),
    "mistralai": ("mistral", ("mistral",)),
    "meta-llama": ("meta", ("meta",)),
}


@dataclass(frozen=True)
class Recipient:
    """Where a call goes. ``key`` is what a list holds; "" when the company cannot be named."""

    route: str
    company: str  # "" = cannot be named
    in_house: bool = False

    @property
    def key(self) -> str:
        if self.in_house or not self.company:
            return ""
        return self.route if self.company == self.route else f"{self.route}:{self.company}"


def recipient_of(provider: str, model: str) -> Recipient:
    provider = (provider or "").strip()
    if provider in SELF_HOSTED_PROVIDERS:
        return Recipient(route=provider, company=provider, in_house=True)
    if provider == "openrouter":
        maker = (model or "").split("/", 1)[0] if "/" in (model or "") else ""
        company = OPENROUTER_MAKERS.get(maker, ("", ()))[0]
        return Recipient(route=provider, company=company)
    if provider in ROUTERS:  # a router nobody has written the table for: its company cannot be named
        return Recipient(route=provider, company="")
    return Recipient(route=provider, company=provider)


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
    """Refuse the call — before a byte is sent — when the caller named its recipients and this is not one."""
    allowed = asked(metadata)
    if allowed is None:
        return
    r = recipient_of(provider, model)
    if r.in_house:
        return
    if r.key and r.key in allowed:
        return
    raise NoAllowedProviderError(
        f"'{model}' on '{provider}' is not one of the recipients this request may reach. "
        "Nothing was sent to any provider.",
        details={
            "model": model,
            "model_provider": provider,
            "recipient": r.key or None,
            "allowed_recipients": sorted(allowed),
            "reason": "recipient_not_allowed" if r.key else "recipient_cannot_be_named",
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

    Only the company's own endpoints, no fallback to another host, and only endpoints that do not keep
    what they are sent. With none of them up the request fails at OpenRouter; it is not re-routed."""
    if provider != "openrouter" or asked(metadata) is None:
        return None
    maker = (model or "").split("/", 1)[0] if "/" in (model or "") else ""
    endpoints = OPENROUTER_MAKERS.get(maker, ("", ()))[1]
    if not endpoints:
        return None  # (no key either: ``require`` has refused this call already)
    return {"only": list(endpoints), "allow_fallbacks": False, "data_collection": "deny"}
