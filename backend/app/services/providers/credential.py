"""FIX85 — a provider key that carries the endpoint it belongs to.

The incident this answers: ``Typhoon`` is a key for one box and ``admin``
is a key for another, and the product stored ONE endpoint per provider.
The key was resolved on one path (``provider_key_service``, 30 s cache)
and the endpoint on another (``base_urls``, its own 30 s cache), and
nothing guaranteed the two described the same machine.

:class:`ProviderCredential` is the pair. It is a ``str`` — its value IS
the API key — so every place that already treats a resolved key as a
string (``Authorization`` headers, the dispatcher's tried-set, the 401
auto-invalidate comparison, the discovery pool) keeps working
byte-for-byte; and it carries ``base_url``, the endpoint that belongs to
this key, for the places that build a client. A caller that wants the
endpoint reads it OFF THE KEY; there is no second lookup to get wrong.

``base_url`` is ``None`` for a key row without an endpoint of its own
(every row that predates FIX85, and any row an operator leaves on the
default) — the adapter then resolves the provider-level row exactly as it
did before, which is what "NULL means today's behaviour" means.

``repr`` never prints the key. ``str`` does, because a str must — the
same rule a plain key string lived under, and the reason no log line in
this codebase formats a key with ``%s``.
"""

from __future__ import annotations


class ProviderCredential(str):
    """An API key plus the endpoint it belongs to.

    ``ProviderCredential(raw)`` is equal to, hashes like, and formats as
    ``raw``. ``.base_url`` is the per-key endpoint (trailing slash
    stripped) or ``None``; ``.key_id`` / ``.label`` identify the
    ``provider_keys`` row the value came from (``None`` for the legacy
    ``providers.api_key_encrypted`` fallback, which has no row).
    """

    base_url: str | None
    key_id: str | None
    label: str | None

    def __new__(
        cls,
        api_key: str,
        *,
        base_url: str | None = None,
        key_id: str | None = None,
        label: str | None = None,
    ) -> "ProviderCredential":
        obj = super().__new__(cls, api_key)
        clean = (base_url or "").strip().rstrip("/")
        obj.base_url = clean or None
        obj.key_id = key_id
        obj.label = label
        return obj

    @property
    def api_key(self) -> str:
        """The key as a plain ``str`` (for callers that must not carry the pair)."""
        return str.__str__(self)

    def __repr__(self) -> str:  # never the key
        tail = self[-4:] if len(self) > 4 else ""
        return (
            f"ProviderCredential(key=••••{tail}, base_url={self.base_url!r}, "
            f"key_id={self.key_id!r}, label={self.label!r})"
        )

    # A str subclass would otherwise pickle/copy back to a plain str and
    # silently drop the endpoint; keep the pair together.
    def __reduce__(self):
        return (
            _rebuild,
            (str.__str__(self), self.base_url, self.key_id, self.label),
        )


def _rebuild(api_key: str, base_url: str | None, key_id: str | None, label: str | None):
    return ProviderCredential(api_key, base_url=base_url, key_id=key_id, label=label)


def endpoint_of(api_key: object) -> str | None:
    """The per-key endpoint carried by ``api_key``, or ``None``.

    Accepts anything an adapter may be handed (a credential, a plain
    string, ``None``) so call sites need no isinstance dance.
    """
    if isinstance(api_key, ProviderCredential):
        return api_key.base_url
    return None
