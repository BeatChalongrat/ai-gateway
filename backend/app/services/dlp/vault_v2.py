"""Scoped Vault V2 — owner-bound cross-turn token store (Phase 4A.1).

The legacy :class:`ConversationVault` keys a Redis hash on the raw,
client-supplied ``conversation_id`` ONLY. That is unscoped: two callers
(different tenants / projects / actors) that happen to reuse the same
``conversation_id`` string share one vault, so any one of them can
resolve another's masked tokens. Vault V2 closes that by deriving the
Redis key from the *authenticated owner scope* (tenant + project + actor
+ conversation) that Phase 4A.0 already threads into
:class:`~app.services.dlp.policy_engine.DLPRequestContext`.

This module is **inert by default**. Nothing here runs in the live
pipeline unless ``DLP_VAULT_V2_ENABLED`` is flipped on in the process;
the legacy path stays byte-identical when the flag is off. There is NO
dual-read and NO migration of legacy contents — V2 reads/writes ONLY the
``dlp_vault:v2:{sha256}`` key space and ignores legacy keys entirely.

Security properties (enforced + tested):

* The Redis key contains only a SHA-256 of the canonical scope — never a
  raw tenant/project/actor/conversation id.
* A missing mandatory scope field makes the scope INVALID; an invalid
  scope performs NO persistent Redis operation (no shared/"default"
  scope, no wildcard delete).
* Resolution is authorization-checked: an entry is returned to the user
  only when ``restorable_to_user`` is true, ``never_restore`` is false,
  and the category is not in the never-restore set. Secrets / strong
  identifiers / OTP / phone / card / etc. never restore even within the
  correct scope.
* Wrong-scope, not-found, expired, and malformed-entry are
  indistinguishable to the caller (all → unresolved).

Limitation (documented, unchanged from legacy): raw values are still
stored as plaintext inside the Redis hash value in this phase. Phase
4A.1 does not add app-level encryption (out of scope). No raw value,
serialized envelope, or token mapping is ever logged.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

import structlog

from app.domain.dlp import EnforcementAction
from app.services._redis_pool import get_async_redis

try:  # redis-py is always present; guard keeps import errors non-fatal.
    from redis.exceptions import WatchError as _WatchError
except Exception:  # pragma: no cover - defensive
    class _WatchError(Exception):
        ...
from app.services.dlp.numeric_context import (
    CONFIDENTIAL_NUMERIC,
    SECRET_NUMERIC,
    STRONG_IDENTIFIER,
)

logger = structlog.get_logger(__name__)

# Match the legacy 2-hour inactivity window so V2 replay behaviour is
# comparable to the path it replaces.
VAULT_V2_TTL_SECONDS = 7200

_V2_KEY_PREFIX = "dlp_vault:v2"
_COUNTER_FIELD = "__counter__"

SCHEMA_VERSION = 1
SOURCE_MASKED_VALUE = "masked_value"

# Bounded per-scope ceiling. A safety backstop, not a product limit —
# far above any real conversation's token count. Overridable via config.
DEFAULT_MAX_ENTRIES = 5000

# ``<rN>`` (counter / deterministic suffix) and the pseudonym
# ``<<CATEGORY_N>>`` shapes — kept in lockstep with transformer's
# ``ANY_MASK_TOKEN_RE`` so a vault written by either emitter resolves.
_TOKEN_RE = re.compile(r"<r[A-Za-z0-9_\-]+>|<<[A-Z][A-Z0-9_]*>>")
_TOKEN_FULL_RE = re.compile(r"\A(?:<r[A-Za-z0-9_\-]+>|<<[A-Z][A-Z0-9_]*>>)\Z")

# Categories that must NEVER be returned to the user, mirroring
# ``orchestrator._NEVER_RESTORE_CATEGORIES``.
_NEVER_RESTORE_CATEGORIES = frozenset({"credential", "secret"})

# Strong-identifier / secret-like classes that must NEVER be restored to
# the user, even within the correct scope (mirrors + extends the legacy
# credential/secret hard-block). Matched against category, detector, and
# entity_type. NOTE: ``email`` is intentionally NOT here — an ordinary PII
# email remains user-restorable under the existing restore policy; it is
# only barred from CALCULATION eligibility (see below).
_NEVER_RESTORE_IDENTIFIER_CLASSES = frozenset(
    {
        "otp",
        "pin",
        "password",
        "passcode",
        "phone",
        "phone_number",
        "national_id",
        "thai_national_id",
        "credit_card",
        "credit_card_number",
        "bank_account",
        "bank_account_number",
        "promptpay",
        "passport",
        "passport_number",
        "tax_id",
        "taxpayer_id",
        "medical_record",
    }
)

# Secret / credential signal classes. Phase 4A.1.1 M2: never-restore is
# enforced SYMMETRICALLY across category / detector / entity_type, so a
# secret mislabeled (e.g. ``category=pii, detector=secret``) is still
# barred — not only when ``category`` happens to be "secret".
_SECRET_SIGNAL_CLASSES = frozenset(
    {
        "secret",
        "credential",
        "api_key",
        "apikey",
        "access_key",
        "secret_key",
        "private_key",
        "token",
        "bearer_token",
        "session_token",
    }
)

# Every class (identifier OR secret) that forces ``never_restore=true``
# when matched against ANY of category / detector / entity_type.
_NEVER_RESTORE_ALL_CLASSES = _NEVER_RESTORE_IDENTIFIER_CLASSES | _SECRET_SIGNAL_CLASSES

# Classes that can never be CALCULATION-eligible. Superset of all
# never-restore classes plus email (restorable, but not a number to
# compute on).
_FORBIDDEN_ELIGIBLE_CLASSES = _NEVER_RESTORE_ALL_CLASSES | frozenset(
    {"email", "email_address"}
)

# Numeric classes that can never be calculation-eligible.
_FORBIDDEN_ELIGIBLE_NUMERIC = frozenset({SECRET_NUMERIC, STRONG_IDENTIFIER})

# Known broad category buckets. An unknown category → non-restorable
# (conservative default per spec).
_KNOWN_RESTORABLE_CATEGORIES = frozenset(
    {"pii", "financial", "general", "name", "place", "org", "organization"}
)


# ---------------------------------------------------------------------------
# Exceptions — carry NO raw value, token mapping, or owner id.
# ---------------------------------------------------------------------------


class VaultScopeError(ValueError):
    """Raised when an operation is attempted with an invalid scope."""


class VaultCounterError(RuntimeError):
    """Raised when the stored reservation counter is malformed (fail closed)."""


class VaultCapExceededError(RuntimeError):
    """Raised when a reservation would exceed the per-scope entry cap."""


class VaultCorruptStateError(RuntimeError):
    """Raised when the scope hash is in an inconsistent state (fail closed)."""


class VaultTokenExhausted(RuntimeError):
    """Raised when the transformer requests more tokens than were reserved."""


async def _await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _norm_field(value: Any) -> str | None:
    """Normalize a mandatory scope field.

    NFC-normalizes (so canonically-equivalent Unicode collapses to one
    identity) and rejects ``None`` / empty / whitespace-only. Does NOT
    trim interior or surrounding content beyond NFC — two distinct
    authenticated ids must never silently collide into one key.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    nfc = unicodedata.normalize("NFC", value)
    if nfc.strip() == "":
        return None
    return nfc


# ---------------------------------------------------------------------------
# VaultScope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VaultScope:
    """Immutable authenticated owner scope for a vault.

    Ownership = (tenant_id, project_id, actor_id, conversation_id). These
    four are the ONLY components of the Redis key. ``channel`` /
    ``streaming`` / ``model`` / ``direction`` / ``message_role`` /
    ``conversation_id_source`` are deliberately excluded — a channel is
    not a security principal, and stream vs non-stream of the same owner
    must see the same vault.
    """

    tenant_id: str
    project_id: str
    actor_id: str
    conversation_id: str

    @classmethod
    def from_context(cls, context: Any) -> "VaultScope | None":
        """Build a scope from a ``DLPRequestContext`` (or ``None``).

        Returns ``None`` (INVALID) if any mandatory field is missing or
        blank. Reads ONLY the authenticated identity fields + the opaque
        conversation locator that 4A.0 threaded — never request body,
        tool args, channel, or direction.
        """
        if context is None:
            return None
        tenant = _norm_field(getattr(context, "tenant_id", None))
        project = _norm_field(getattr(context, "project_id", None))
        actor = _norm_field(getattr(context, "actor_id", None))
        conversation = _norm_field(getattr(context, "conversation_id", None))
        if not (tenant and project and actor and conversation):
            return None
        return cls(
            tenant_id=tenant,
            project_id=project,
            actor_id=actor,
            conversation_id=conversation,
        )

    @property
    def is_valid(self) -> bool:
        return bool(
            _norm_field(self.tenant_id)
            and _norm_field(self.project_id)
            and _norm_field(self.actor_id)
            and _norm_field(self.conversation_id)
        )

    def canonical_bytes(self) -> bytes:
        """Delimiter-unambiguous canonical serialization of the scope."""
        return json.dumps(
            {
                "tenant_id": self.tenant_id,
                "project_id": self.project_id,
                "actor_id": self.actor_id,
                "conversation_id": self.conversation_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def redis_key(self) -> str:
        return f"{_V2_KEY_PREFIX}:{self.digest()}"

    def fingerprint(self) -> str:
        """Short, non-reversible correlator safe for logs (no raw ids)."""
        return self.digest()[:12]


def build_vault_scope(context: Any) -> "VaultScope | None":
    """Module-level convenience for :meth:`VaultScope.from_context`."""
    return VaultScope.from_context(context)


# Counter-form ``<rN>`` index parser (deterministic-suffix / pseudonym
# tokens have no integer index and return None — they are never minted by
# the V2 reservation path).
_COUNTER_TOKEN_RE = re.compile(r"\A<r(\d+)>\Z")


def _token_index(token: str) -> int | None:
    m = _COUNTER_TOKEN_RE.match(token or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# ReservedTokenRange — the ONLY legitimate source of V2 mask tokens.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReservedTokenRange:
    """Immutable proof of a scoped atomic reservation.

    Constructed ONLY by :meth:`ConversationVaultV2.reserve_tokens_async`.
    The transformer consumes tokens via :meth:`allocator`; the store
    (:meth:`ConversationVaultV2.internal_store_reserved_entries_async`)
    accepts ONLY tokens contained here. ``scope_fingerprint`` is for
    internal sanity/logging — it is NOT owner authorization (that always
    requires the full :class:`VaultScope`).
    """

    scope_fingerprint: str
    tokens: tuple[str, ...]
    start_index: int
    end_index_exclusive: int

    def allocator(self) -> "_ReservedAllocator":
        return _ReservedAllocator(self.tokens)


class _ReservedAllocator:
    """Sequential consumer of a reserved token range (duck-typed `.next()`).

    The synchronous transformer holds this and calls ``next()`` per emitted
    token. Exhaustion is a hard error (fail closed) — never an in-request
    ``<rN>`` fallback.
    """

    __slots__ = ("_tokens", "_i")

    def __init__(self, tokens: tuple[str, ...]) -> None:
        self._tokens = tokens
        self._i = 0

    def next(self) -> str:
        if self._i >= len(self._tokens):
            raise VaultTokenExhausted("reserved token range exhausted")
        tok = self._tokens[self._i]
        self._i += 1
        return tok

    @property
    def consumed(self) -> int:
        return self._i


# ---------------------------------------------------------------------------
# VaultEntryV2
# ---------------------------------------------------------------------------


def _coerce_action(action: Any) -> str | None:
    """Return a canonical action NAME, or ``None`` if unknown."""
    if isinstance(action, EnforcementAction):
        return action.name
    if isinstance(action, str):
        name = action.strip().upper()
        if name in EnforcementAction.__members__:
            return name
    return None


def _norm_class(value: str) -> str:
    """Deterministic class normalization: lowercase, unify separators."""
    return re.sub(r"[\s\-]+", "_", value.strip().lower())


def _matches_class(parts: Iterable[Any], classes: frozenset) -> bool:
    for p in parts:
        if p and isinstance(p, str) and _norm_class(p) in classes:
            return True
    return False


@dataclass(frozen=True)
class VaultEntryV2:
    """Immutable per-token vault entry with restore/eligibility metadata.

    Metadata is derived from the actual ``DetectionMatch`` + policy
    decision (see :func:`build_vault_entry`) — NEVER from request body or
    tool arguments.
    """

    schema_version: int
    token: str
    raw_value: str
    category: str
    detector: str | None
    action: str
    numeric_class: str | None
    calculation_eligible: bool
    restorable_to_user: bool
    never_restore: bool
    source_kind: str
    message_index: int | None
    created_at: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "token": self.token,
                "raw_value": self.raw_value,
                "category": self.category,
                "detector": self.detector,
                "action": self.action,
                "numeric_class": self.numeric_class,
                "calculation_eligible": self.calculation_eligible,
                "restorable_to_user": self.restorable_to_user,
                "never_restore": self.never_restore,
                "source_kind": self.source_kind,
                "message_index": self.message_index,
                "created_at": self.created_at,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, blob: Any) -> "VaultEntryV2 | None":
        """Parse a stored envelope; ``None`` on any malformation.

        Never deserializes arbitrary Python objects — JSON only. A
        malformed envelope is treated as unresolved (the caller leaves
        the token opaque) rather than crashing.
        """
        if blob is None:
            return None
        if isinstance(blob, bytes):
            try:
                blob = blob.decode("utf-8")
            except Exception:
                return None
        if not isinstance(blob, str):
            return None
        try:
            d = json.loads(blob)
        except Exception:
            return None
        if not isinstance(d, dict):
            return None
        try:
            return cls(
                schema_version=int(d["schema_version"]),
                token=str(d["token"]),
                raw_value=str(d["raw_value"]),
                category=str(d["category"]),
                detector=(None if d.get("detector") is None else str(d["detector"])),
                action=str(d["action"]),
                numeric_class=(
                    None if d.get("numeric_class") is None else str(d["numeric_class"])
                ),
                calculation_eligible=bool(d["calculation_eligible"]),
                restorable_to_user=bool(d["restorable_to_user"]),
                never_restore=bool(d["never_restore"]),
                source_kind=str(d["source_kind"]),
                message_index=(
                    None if d.get("message_index") is None else int(d["message_index"])
                ),
                created_at=int(d["created_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


def build_vault_entry(
    *,
    token: str,
    raw_value: str,
    category: str,
    action: Any,
    created_at: int,
    detector: str | None = None,
    entity_type: str | None = None,
    numeric_class: str | None = None,
    message_index: int | None = None,
    source_kind: str = SOURCE_MASKED_VALUE,
) -> "VaultEntryV2 | None":
    """Derive a :class:`VaultEntryV2` from an actual finding + decision.

    Eligibility / restorability are computed deterministically and
    conservatively. Returns ``None`` for a malformed input (unknown
    action, blank token/category, non-``masked_value`` source) so the
    caller rejects the write rather than storing a raw-only entry.
    """
    if not token or not _TOKEN_FULL_RE.match(token):
        return None
    if source_kind != SOURCE_MASKED_VALUE:
        return None  # Phase 4A.1 stores only masked source values.
    cat = (category or "").strip()
    if not cat:
        return None
    action_name = _coerce_action(action)
    if action_name is None:
        # Unknown action → conservative: cannot build a trustworthy entry.
        return None

    # M2 — symmetric never-restore: ANY strong signal (category, detector,
    # entity_type, or numeric_class) saying secret/credential/identifier
    # forces never_restore. No single-field reliance.
    class_parts = (cat, detector, entity_type)
    never_restore = (
        _matches_class(class_parts, _NEVER_RESTORE_ALL_CLASSES)
        or numeric_class in _FORBIDDEN_ELIGIBLE_NUMERIC
    )
    forbidden_eligible = (
        never_restore
        or _matches_class(class_parts, _FORBIDDEN_ELIGIBLE_CLASSES)
    )

    restorable_to_user = (
        not never_restore
        and cat in _KNOWN_RESTORABLE_CATEGORIES
        and action_name in (EnforcementAction.RESTORE.name, EnforcementAction.MASK.name)
    )

    calculation_eligible = (
        numeric_class == CONFIDENTIAL_NUMERIC
        and action_name == EnforcementAction.MASK.name
        and not never_restore
        and not forbidden_eligible
    )

    return VaultEntryV2(
        schema_version=SCHEMA_VERSION,
        token=token,
        raw_value=raw_value,
        category=cat,
        detector=detector,
        action=action_name,
        numeric_class=numeric_class,
        calculation_eligible=bool(calculation_eligible),
        restorable_to_user=bool(restorable_to_user),
        never_restore=bool(never_restore),
        source_kind=source_kind,
        message_index=message_index,
        created_at=int(created_at),
    )


def derive_entries_from_span_map(
    span_map: dict[str, str],
    match_actions: Iterable[Any],
    *,
    created_at: int,
) -> list["VaultEntryV2"]:
    """Build V2 entries for a turn's ``span_map`` using real findings.

    Correlates each ``{token: raw_value}`` to the ``MatchAction`` whose
    ``match.matched_text`` equals the raw value, so category / action /
    numeric_class come from the ACTUAL detection + decision — never from
    request body or tool arguments. Tokens without a correlatable finding
    are stored conservatively (unknown category → non-restorable,
    ineligible). Malformed derivations are dropped.
    """
    by_value: dict[str, Any] = {}
    for ma in match_actions or []:
        match = getattr(ma, "match", None)
        if match is None:
            continue
        raw = getattr(match, "matched_text", None)
        if raw is not None and raw not in by_value:
            by_value[raw] = ma

    out: list[VaultEntryV2] = []
    for token, raw_value in (span_map or {}).items():
        ma = by_value.get(raw_value)
        category = "unknown"
        detector = None
        entity_type = None
        action: Any = EnforcementAction.MASK
        numeric_class = None
        message_index = None
        if ma is not None:
            match = ma.match
            category = getattr(match, "category", "unknown") or "unknown"
            detector = getattr(match, "detector_name", None)
            entity_type = getattr(match, "entity_type", None)
            action = getattr(ma, "action", EnforcementAction.MASK)
            meta = getattr(match, "metadata", None) or {}
            pv = meta.get("policy_v2") if isinstance(meta, dict) else None
            if isinstance(pv, dict):
                numeric_class = pv.get("numeric_class")
                message_index = pv.get("message_index")
        entry = build_vault_entry(
            token=token,
            raw_value=raw_value,
            category=category,
            action=action,
            created_at=created_at,
            detector=detector,
            entity_type=entity_type,
            numeric_class=numeric_class,
            message_index=message_index,
        )
        if entry is not None:
            out.append(entry)
    return out


def build_entries_from_records(
    records: Iterable[Any], *, created_at: int
) -> list["VaultEntryV2"]:
    """Build V2 entries from transformer ``AllocatedTokenRecord`` objects.

    Phase 4A.1.1 C2 fix: each record already pairs ONE emitted token with
    the EXACT applied finding/span the transformer masked — there is no
    raw-value reverse lookup and no first-match-wins. Metadata
    (category/detector/entity_type/numeric_class/message_index) comes
    straight from that record. Duck-typed (no transformer import).
    """
    out: list[VaultEntryV2] = []
    for r in records or []:
        pv = getattr(r, "policy_v2", None) or {}
        numeric_class = pv.get("numeric_class") if isinstance(pv, dict) else None
        entry = build_vault_entry(
            token=getattr(r, "token", ""),
            raw_value=getattr(r, "raw_value", ""),
            category=getattr(r, "category", "") or "",
            action=getattr(r, "action", None),
            created_at=created_at,
            detector=getattr(r, "detector", None),
            entity_type=getattr(r, "entity_type", None),
            numeric_class=numeric_class,
            message_index=getattr(r, "message_index", None),
        )
        if entry is not None:
            out.append(entry)
    return out


def select_stream_restore_candidates(
    *,
    output_tokens: set,
    authorized_inbound_tokens: frozenset,
    outbound_generated_tokens: frozenset,
) -> set:
    """Provenance-aware restore allowlist for the streaming path (4A.1.2).

    A token in the streamed output may be restored ONLY if it is present
    in the output, is an authorized inbound token, and is NOT in the
    gateway-minted outbound set. Outbound-generated provenance ALWAYS
    wins a conflict — even if a counter bug made an outbound token collide
    with an inbound index, it is excluded here. Pure set algebra; no I/O.
    """
    return (set(output_tokens) & set(authorized_inbound_tokens)) - set(
        outbound_generated_tokens
    )


def _entry_restorable(entry: VaultEntryV2) -> bool:
    """Final authorization gate applied at resolve time."""
    return (
        entry.restorable_to_user is True
        and entry.never_restore is False
        and entry.category not in _NEVER_RESTORE_CATEGORIES
    )


# ---------------------------------------------------------------------------
# Shadow readiness report (pure — no Redis I/O, no raw logging)
# ---------------------------------------------------------------------------


def vault_v2_shadow_report(
    scope: "VaultScope | None",
    entries: Iterable["VaultEntryV2 | None"],
) -> dict[str, int]:
    """Sanitized readiness counters for shadow mode. No Redis, no raw."""
    entries = list(entries)
    valid = [e for e in entries if isinstance(e, VaultEntryV2)]
    ineligible = [e for e in valid if not e.calculation_eligible]
    restore_candidates = [e for e in valid if _entry_restorable(e)]
    return {
        "scope_valid": 1 if (scope is not None and scope.is_valid) else 0,
        "scope_missing": 0 if (scope is not None and scope.is_valid) else 1,
        "entries_valid": len(valid),
        "entries_ineligible": len(ineligible),
        "restore_candidates": len(restore_candidates),
    }


# ---------------------------------------------------------------------------
# ConversationVaultV2
# ---------------------------------------------------------------------------


def _parse_counter(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except Exception as exc:  # pragma: no cover - defensive
            raise VaultCounterError("counter not decodable") from exc
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise VaultCounterError("counter malformed") from exc
    if value < 0:
        raise VaultCounterError("counter negative")
    return value


class ConversationVaultV2:
    """Scoped, owner-bound cross-turn token store.

    Every method takes a :class:`VaultScope`; there is intentionally no
    method that resolves by ``conversation_id`` alone. Reads use
    ``HMGET`` for only the requested tokens — the whole hash is never
    loaded to find a token, and Redis keys are never scanned.
    """

    def __init__(
        self, redis_client: Any = None, *, max_entries: int = DEFAULT_MAX_ENTRIES
    ) -> None:
        self._fake_client = redis_client
        self._async_client: Any | None = None
        self._max_entries = max_entries

    def _client(self) -> Any:
        if self._fake_client is not None:
            return self._fake_client
        if self._async_client is None:
            self._async_client = get_async_redis()
        return self._async_client

    @staticmethod
    def _valid_tokens(tokens: Iterable[str]) -> list[str]:
        return [t for t in tokens if t and _TOKEN_FULL_RE.match(t)]

    # -- reservation -----------------------------------------------------

    async def reserve_tokens_async(
        self, scope: "VaultScope | None", count: int
    ) -> "ReservedTokenRange":
        """Atomically reserve ``count`` token indices for ``scope``.

        Cap-check + counter-advance are ONE atomic step (WATCH/MULTI on the
        real async client; trivially atomic on the single-threaded fake) so
        concurrent callers never collide and a cap-exceeded request does NOT
        advance the counter. The counter stores the NEXT free index;
        reserving ``count`` from ``start`` yields ``<r{start}>..<r{start+
        count-1}>``. Gaps from a later store failure are acceptable; indices
        are never reused. Fail-closed on malformed/corrupt counter state.

        Returns a :class:`ReservedTokenRange` — the only legitimate source
        of V2 mask tokens.
        """
        if scope is None or not scope.is_valid:
            raise VaultScopeError("invalid scope: no token reservation")
        if not isinstance(count, int) or count <= 0:
            raise ValueError("count must be a positive integer")

        key = scope.redis_key()
        if self._fake_client is not None:
            start = self._reserve_fake(self._fake_client, key, count)
        else:
            start = await self._reserve_async(self._client(), key, count)
        return ReservedTokenRange(
            scope_fingerprint=scope.fingerprint(),
            tokens=tuple(f"<r{i}>" for i in range(start, start + count)),
            start_index=start,
            end_index_exclusive=start + count,
        )

    def _check_corrupt(self, current: int, hlen: int) -> None:
        """Fail closed on inconsistent hash state (no reset, no reuse)."""
        # Counter absent (==0) but token fields already exist → the hash was
        # written outside the reservation discipline (or legacy collision).
        if current == 0 and hlen > 0:
            raise VaultCorruptStateError("counter missing but entries present")

    def _reserve_fake(self, client: Any, key: str, count: int) -> int:
        # fakeredis is single-threaded: a WATCH/MULTI read-modify-write is
        # effectively atomic, and so is this guarded sequence.
        current = _parse_counter(client.hget(key, _COUNTER_FIELD))
        self._check_corrupt(current, int(client.hlen(key) or 0))
        if current + count > self._max_entries:
            raise VaultCapExceededError("per-scope entry cap exceeded")
        pipe = client.pipeline(transaction=True)
        pipe.hset(key, _COUNTER_FIELD, current + count)
        pipe.expire(key, VAULT_V2_TTL_SECONDS)
        pipe.execute()
        return current

    async def _reserve_async(self, client: Any, key: str, count: int) -> int:
        # Optimistic WATCH/MULTI: cap-check and advance commit together or
        # not at all; a concurrent writer triggers WatchError → retry.
        for _ in range(8):
            async with client.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(key)
                    current = _parse_counter(await pipe.hget(key, _COUNTER_FIELD))
                    self._check_corrupt(current, int(await pipe.hlen(key) or 0))
                    if current + count > self._max_entries:
                        await pipe.reset()
                        raise VaultCapExceededError("per-scope entry cap exceeded")
                    pipe.multi()
                    pipe.hset(key, _COUNTER_FIELD, current + count)
                    pipe.expire(key, VAULT_V2_TTL_SECONDS)
                    await pipe.execute()
                    return current
                except _WatchError:
                    continue
        # Could not commit without contention after retries → fail closed.
        raise VaultCorruptStateError("reservation contention not resolved")

    # -- write -----------------------------------------------------------

    def _serialize_entries(self, entries, allowed: set | None) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for e in entries or []:
            if not isinstance(e, VaultEntryV2):
                continue
            if not _TOKEN_FULL_RE.match(e.token):
                continue
            if e.source_kind != SOURCE_MASKED_VALUE:
                continue
            if allowed is not None and e.token not in allowed:
                # Token was not part of the reservation → reject (never
                # write an unreserved token; no raw log).
                logger.warning("dlp_vault_v2_unreserved_token_rejected")
                continue
            if e.token in mapping:
                continue  # dedup within batch
            try:
                mapping[e.token] = e.to_json()
            except Exception:
                continue  # serialization failure → skip; never log raw
        return mapping

    async def _hset_entries(self, scope: "VaultScope", mapping: dict[str, str]) -> int:
        if not mapping:
            return 0
        key = scope.redis_key()
        try:
            await _await(client_hset(self._client(), key, mapping))
            await _await(self._client().expire(key, VAULT_V2_TTL_SECONDS))
        except Exception as exc:
            logger.warning(
                "dlp_vault_v2_extend_failed",
                scope_fp=scope.fingerprint(),
                exception_type=type(exc).__name__,
            )
            return 0
        return len(mapping)

    async def internal_store_reserved_entries_async(
        self,
        scope: "VaultScope | None",
        reserved: "ReservedTokenRange",
        entries: list["VaultEntryV2 | None"],
    ) -> int:
        """LIVE V2 store. Accepts ONLY tokens from ``reserved``.

        This is the only store the live integration may call. It rejects
        any entry whose token was not part of the atomic reservation, so
        live code can never bypass the counter authority and write an
        arbitrary ``<r0>``. Owner authorization is still the full
        ``VaultScope`` (the reservation fingerprint is a sanity check, not
        an authorization token).
        """
        if scope is None or not scope.is_valid:
            raise VaultScopeError("invalid scope: no vault write")
        if reserved is None or reserved.scope_fingerprint != scope.fingerprint():
            raise VaultScopeError("reservation does not match scope")
        mapping = self._serialize_entries(entries, allowed=set(reserved.tokens))
        return await self._hset_entries(scope, mapping)

    async def extend_scoped_async(
        self, scope: "VaultScope | None", entries: list["VaultEntryV2 | None"]
    ) -> int:
        """TEST/HELPER ONLY — does NOT enforce reservation.

        Retained for unit tests and audit probes. The LIVE V2 path MUST
        use :meth:`internal_store_reserved_entries_async` (reservation-
        checked) so production cannot write an unreserved token. Malformed
        entries are skipped; a Redis outage writes nothing (masked, never
        raw); refreshes TTL on success.
        """
        if scope is None or not scope.is_valid:
            raise VaultScopeError("invalid scope: no vault write")
        mapping = self._serialize_entries(entries, allowed=None)
        return await self._hset_entries(scope, mapping)

    # -- read ------------------------------------------------------------

    async def get_entries_scoped_async(
        self, scope: "VaultScope | None", tokens: Iterable[str]
    ) -> dict[str, "VaultEntryV2"]:
        """Return validated entries for the requested tokens (HMGET only).

        Wrong scope / not found / expired / malformed are all → absent.
        Never reveals that a token exists in another scope.
        """
        if scope is None or not scope.is_valid:
            return {}
        token_list = self._valid_tokens(tokens)
        if not token_list:
            return {}
        key = scope.redis_key()
        try:
            values = await _await(self._client().hmget(key, token_list))
        except Exception as exc:
            logger.warning(
                "dlp_vault_v2_get_failed",
                scope_fp=scope.fingerprint(),
                exception_type=type(exc).__name__,
            )
            return {}
        out: dict[str, VaultEntryV2] = {}
        for token, blob in zip(token_list, values or []):
            entry = VaultEntryV2.from_json(blob)
            if entry is None:
                continue
            if entry.token != token:  # token-field must match the key
                continue
            out[token] = entry
        return out

    async def resolve_for_user_async(
        self, scope: "VaultScope | None", tokens: Iterable[str]
    ) -> dict[str, str]:
        """Authorized restore: token → raw value for AUTHORIZED entries only.

        Applies the full gate (restorable_to_user + not never_restore +
        category allowed). Secrets / identifiers / OTP / etc. are dropped
        even within the correct scope. Unauthorized / unresolved tokens
        are simply absent from the result (caller keeps them opaque).
        """
        entries = await self.get_entries_scoped_async(scope, tokens)
        out: dict[str, str] = {}
        for token, entry in entries.items():
            if _entry_restorable(entry):
                out[token] = entry.raw_value
        return out

    # -- lifecycle -------------------------------------------------------

    async def delete_scope_async(self, scope: "VaultScope | None") -> None:
        """Delete only the single hashed scoped key. Never a wildcard."""
        if scope is None or not scope.is_valid:
            return  # invalid scope → no delete, no pattern cleanup
        try:
            await _await(self._client().delete(scope.redis_key()))
        except Exception as exc:
            logger.warning(
                "dlp_vault_v2_delete_failed",
                scope_fp=scope.fingerprint(),
                exception_type=type(exc).__name__,
            )


def client_hset(client: Any, key: str, mapping: dict[str, str]) -> Any:
    """HSET a mapping, compatible with both redis-py and fakeredis."""
    return client.hset(key, mapping=mapping)


# Module-level singleton — same construction pattern as the legacy vault.
conversation_vault_v2 = ConversationVaultV2()
