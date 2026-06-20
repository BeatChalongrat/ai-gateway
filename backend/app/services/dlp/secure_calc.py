"""Secure Calculator — Phase 4A.2.6.

Computes over Vault V2 ``calculation_eligible`` confidential-numeric tokens and
mints a result token ``<CALC_n>``. The raw operands and the raw result NEVER
leave trusted server memory + the scoped result vault: the provider, canonical
history, ``request_logs.output_final``, and search/export/admin all see only
``<CALC_n>``. The result restores to its raw value ONLY on the authorized owner
display path (same Vault V2 scope authority).

Hard security rules (enforced here):
  * Deterministic ``decimal.Decimal`` arithmetic ONLY — no ``eval``, no
    expression parser, no Python/JS/SQL execution, no LLM-decided math.
  * Operands MUST be Vault V2 entries with ``numeric_class==CONFIDENTIAL_NUMERIC``
    AND ``calculation_eligible==True`` (which already implies action==MASK,
    not never_restore, not a secret/identifier). Anything else → fail closed.
  * Result tokens are ``calculation_eligible=False`` → no chained ``<CALC_n>``
    arithmetic in v1.
  * The result store keys on the SAME owner-scope digest as Vault V2 (a
    separate ``dlp_calc:v1`` namespace); wrong scope = not found.
  * No raw operand/result value is ever logged.
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, getcontext, ROUND_HALF_UP
from enum import Enum
from typing import Any, Iterable

import structlog

from app.services._redis_pool import get_async_redis
from app.services.dlp.numeric_context import CONFIDENTIAL_NUMERIC
from app.services.dlp.vault_v2 import VaultScope, VAULT_V2_TTL_SECONDS

logger = structlog.get_logger(__name__)

# Bounded precision — generous but finite, so a pathological operand can't
# blow up memory. Fixed-point decimal; never float.
getcontext().prec = 50

_CALC_KEY_PREFIX = "dlp_calc:v1"
_CALC_COUNTER = "__calc_counter__"
CALC_TOKEN_TYPE = "calculation_result"
CALC_SCHEMA_VERSION = 1

# ``<CALC_n>`` namespace — DISJOINT from ``<rN>`` and ``<<CATEGORY_N>>``.
CALC_TOKEN_RE = re.compile(r"\A<CALC_(\d+)>\Z")
# A result-token value cap (defense against a runaway operand list).
_MAX_OPERANDS = 100


def is_calc_token(token: str) -> bool:
    return bool(CALC_TOKEN_RE.match(token or ""))


class CalcError(ValueError):
    """Fail-closed calculator error. Carries a STABLE code, never raw values."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CalcOperation(str, Enum):
    SUM = "sum"
    AVERAGE = "average"
    DIFFERENCE = "difference"
    PERCENTAGE_CHANGE = "percentage_change"
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"


_BINARY_EXACT2 = {
    CalcOperation.DIFFERENCE,
    CalcOperation.SUBTRACT,
    CalcOperation.DIVIDE,
    CalcOperation.PERCENTAGE_CHANGE,
}


def coerce_operation(value: Any) -> "CalcOperation | None":
    if isinstance(value, CalcOperation):
        return value
    if isinstance(value, str):
        try:
            return CalcOperation(value.strip().lower())
        except ValueError:
            return None
    return None


def parse_operand(raw: str) -> Decimal:
    """Parse a stored raw numeric value to a Decimal. Fail closed on garbage.

    Accepts plain integers/decimals with optional thousands separators; rejects
    anything that isn't a finite number (no currency symbols, no expressions).
    """
    if not isinstance(raw, str):
        raise CalcError("operand_not_numeric")
    s = raw.strip().replace(",", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        raise CalcError("operand_not_numeric")
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        raise CalcError("operand_not_numeric")
    if not d.is_finite():
        raise CalcError("operand_not_finite")
    return d


def compute(operation: CalcOperation, operands: list[Decimal]) -> Decimal:
    """Deterministic fixed-point arithmetic. Fail-closed; no eval."""
    n = len(operands)
    if n == 0:
        raise CalcError("no_operands")
    if n > _MAX_OPERANDS:
        raise CalcError("too_many_operands")
    if operation in _BINARY_EXACT2 and n != 2:
        raise CalcError("operation_requires_two_operands")

    try:
        if operation in (CalcOperation.SUM, CalcOperation.ADD):
            result = sum(operands, Decimal(0))
        elif operation == CalcOperation.AVERAGE:
            result = sum(operands, Decimal(0)) / Decimal(n)
        elif operation in (CalcOperation.DIFFERENCE, CalcOperation.SUBTRACT):
            result = operands[0] - operands[1]
        elif operation == CalcOperation.MULTIPLY:
            result = Decimal(1)
            for o in operands:
                result *= o
        elif operation == CalcOperation.DIVIDE:
            if operands[1] == 0:
                raise CalcError("division_by_zero")
            result = operands[0] / operands[1]
        elif operation == CalcOperation.PERCENTAGE_CHANGE:
            if operands[0] == 0:
                raise CalcError("percentage_change_base_zero")
            result = (operands[1] - operands[0]) / operands[0] * Decimal(100)
        else:  # pragma: no cover - enum exhaustive
            raise CalcError("unsupported_operation")
    except CalcError:
        raise
    except (InvalidOperation, ArithmeticError):
        raise CalcError("arithmetic_error")

    if not result.is_finite():
        raise CalcError("result_not_finite")
    return result


def format_result(result: Decimal, *, currency: bool = False) -> str:
    """Canonical string form of a result. 2dp for currency; else normalized."""
    if currency:
        return str(result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    # Normalize: drop a trailing .0 (80000.0 → 80000) but keep real fractions.
    normalized = result.normalize()
    # ``normalize`` can yield scientific notation for large ints (8E+4); expand.
    return f"{normalized:f}"


# ---------------------------------------------------------------------------
# Result entry + scoped result vault
# ---------------------------------------------------------------------------


async def _await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass(frozen=True)
class CalcResultEntry:
    """A minted ``<CALC_n>`` result. Operands are TOKEN REFS only (never raw)."""

    schema_version: int
    token: str
    token_type: str
    raw_value: str
    operation: str
    operand_tokens: tuple[str, ...]
    restorable_to_user: bool
    never_restore_to_provider: bool
    calculation_eligible: bool
    created_at: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "token": self.token,
                "token_type": self.token_type,
                "raw_value": self.raw_value,
                "operation": self.operation,
                "operand_tokens": list(self.operand_tokens),
                "restorable_to_user": self.restorable_to_user,
                "never_restore_to_provider": self.never_restore_to_provider,
                "calculation_eligible": self.calculation_eligible,
                "created_at": self.created_at,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, blob: Any) -> "CalcResultEntry | None":
        if isinstance(blob, bytes):
            try:
                blob = blob.decode("utf-8")
            except Exception:
                return None
        if not isinstance(blob, str):
            return None
        try:
            d = json.loads(blob)
            return cls(
                schema_version=int(d["schema_version"]),
                token=str(d["token"]),
                token_type=str(d["token_type"]),
                raw_value=str(d["raw_value"]),
                operation=str(d["operation"]),
                operand_tokens=tuple(str(t) for t in d.get("operand_tokens", [])),
                restorable_to_user=bool(d["restorable_to_user"]),
                never_restore_to_provider=bool(d["never_restore_to_provider"]),
                calculation_eligible=bool(d["calculation_eligible"]),
                created_at=int(d["created_at"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None


class SecureCalcResultStore:
    """Scoped ``<CALC_n>`` result store (parallel to Vault V2, same scope key).

    Keyed ``dlp_calc:v1:{scope.digest()}`` so a result is bound to the same
    owner scope as its operands. Wrong scope = not found.
    """

    def __init__(self, redis_client: Any = None) -> None:
        self._fake = redis_client
        self._async: Any | None = None

    def _client(self) -> Any:
        if self._fake is not None:
            return self._fake
        if self._async is None:
            self._async = get_async_redis()
        return self._async

    @staticmethod
    def _key(scope: VaultScope) -> str:
        return f"{_CALC_KEY_PREFIX}:{scope.digest()}"

    async def mint_and_store_async(
        self,
        scope: "VaultScope | None",
        *,
        raw_value: str,
        operation: str,
        operand_tokens: Iterable[str],
        created_at: int,
    ) -> "tuple[str, CalcResultEntry]":
        if scope is None or not scope.is_valid:
            raise CalcError("invalid_scope")
        key = self._key(scope)
        client = self._client()
        idx = await _await(client.hincrby(key, _CALC_COUNTER, 1))
        token = f"<CALC_{int(idx) - 1}>"
        entry = CalcResultEntry(
            schema_version=CALC_SCHEMA_VERSION,
            token=token,
            token_type=CALC_TOKEN_TYPE,
            raw_value=raw_value,
            operation=operation,
            operand_tokens=tuple(operand_tokens),
            restorable_to_user=True,
            never_restore_to_provider=True,
            calculation_eligible=False,
            created_at=created_at,
        )
        await _await(client.hset(key, token, entry.to_json()))
        await _await(client.expire(key, VAULT_V2_TTL_SECONDS))
        return token, entry

    async def resolve_for_user_async(
        self, scope: "VaultScope | None", tokens: Iterable[str]
    ) -> dict[str, str]:
        """Owner-authorized restore: ``<CALC_n>`` → raw result string."""
        if scope is None or not scope.is_valid:
            return {}
        toks = [t for t in tokens if is_calc_token(t)]
        if not toks:
            return {}
        try:
            values = await _await(self._client().hmget(self._key(scope), toks))
        except Exception as exc:
            logger.warning(
                "dlp_calc_resolve_failed",
                scope_fp=scope.fingerprint(),
                exception_type=type(exc).__name__,
            )
            return {}
        out: dict[str, str] = {}
        for tok, blob in zip(toks, values or []):
            entry = CalcResultEntry.from_json(blob)
            if entry is None or entry.token != tok:
                continue
            if entry.restorable_to_user and not entry.calculation_eligible:
                out[tok] = entry.raw_value
        return out


secure_calc_result_store = SecureCalcResultStore()


# ---------------------------------------------------------------------------
# High-level secure calculation over Vault V2 entries
# ---------------------------------------------------------------------------


def operand_eligible(entry: Any) -> bool:
    """A Vault V2 entry is a legal calculator operand iff it is a
    confidential-numeric, calculation-eligible, restorable, non-secret entry.
    """
    return (
        entry is not None
        and getattr(entry, "numeric_class", None) == CONFIDENTIAL_NUMERIC
        and bool(getattr(entry, "calculation_eligible", False))
        and not bool(getattr(entry, "never_restore", True))
        and bool(getattr(entry, "restorable_to_user", False))
    )


async def secure_calculate(
    *,
    scope: "VaultScope | None",
    operation: CalcOperation,
    operand_tokens: list[str],
    vault_entries: dict[str, Any],
    created_at: int,
    currency: bool = False,
) -> "tuple[str, CalcResultEntry]":
    """Resolve eligible operands, compute, mint + store a ``<CALC_n>``.

    ``vault_entries`` is the scoped Vault V2 lookup (token → VaultEntryV2). Any
    ineligible / missing / chained-calc token fails the WHOLE calculation
    closed (no partial result). Raw operands stay in local memory only.
    """
    if scope is None or not scope.is_valid:
        raise CalcError("invalid_scope")
    if not operand_tokens:
        raise CalcError("no_operands")
    # Reject chained <CALC_n> operands (v1) and any non-vault token shape.
    operands: list[Decimal] = []
    for tok in operand_tokens:
        if is_calc_token(tok):
            raise CalcError("chained_calc_not_supported")
        entry = vault_entries.get(tok)
        if not operand_eligible(entry):
            raise CalcError("operand_not_eligible")
        operands.append(parse_operand(entry.raw_value))
    result = compute(operation, operands)
    raw_value = format_result(result, currency=currency)
    return await secure_calc_result_store.mint_and_store_async(
        scope,
        raw_value=raw_value,
        operation=operation.value,
        operand_tokens=operand_tokens,
        created_at=created_at,
    )


@dataclass(frozen=True)
class CalcChatOutcome:
    """In-chat secure-calc result. ``canonical`` carries the token; ``raw_value``
    is restored to the OWNER display only (never logged/egressed)."""

    canonical: str
    token: str
    raw_value: str


# Minimum operand counts per operation for the in-chat path (binary ops are
# already arity-2-checked by ``compute``).
def _arity_ok(operation: CalcOperation, count: int) -> bool:
    if operation in _BINARY_EXACT2:
        return count == 2
    return count >= 2  # sum/add/average/multiply need at least two operands


async def orchestrate_secure_calc(
    *,
    scope: "VaultScope | None",
    records: Iterable[Any],
    user_text: str,
    created_at: int,
) -> "CalcChatOutcome | None":
    """In-chat entry point. Detect intent + collect ELIGIBLE operands from the
    turn's allocation records (in-memory; no provider/raw-text parsing), compute,
    mint ``<CALC_n>``, and return the canonical template + raw result.

    Returns ``None`` when there is no calc intent or too few eligible operands
    (chat proceeds normally). Raises :class:`CalcError` when a calc WAS
    requested over valid operands but cannot complete (e.g. division by zero) —
    the caller renders a safe error template, never raw, never the provider.
    """
    # Local import avoids a module import cycle (intent imports this module).
    from app.services.dlp.secure_calc_intent import detect_calc_operation, calc_template
    from app.services.dlp.vault_v2 import build_entries_from_records

    if scope is None or not scope.is_valid:
        return None
    operation = detect_calc_operation(user_text)
    if operation is None:
        return None

    entries = build_entries_from_records(records, created_at=created_at)
    eligible = [(e.token, e.raw_value) for e in entries if e.calculation_eligible]
    if not _arity_ok(operation, len(eligible)):
        return None  # not enough eligible operands → normal chat, no compute

    operands = [parse_operand(raw) for _, raw in eligible]
    operand_tokens = [tok for tok, _ in eligible]
    result = compute(operation, operands)  # may raise CalcError → safe error
    raw_value = format_result(result)
    token, _entry = await secure_calc_result_store.mint_and_store_async(
        scope,
        raw_value=raw_value,
        operation=operation.value,
        operand_tokens=operand_tokens,
        created_at=created_at,
    )
    return CalcChatOutcome(canonical=calc_template(operation, token), token=token, raw_value=raw_value)
