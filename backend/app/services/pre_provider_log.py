"""FIX130 E3 — a chat turn that fails before any provider is called still
leaves ONE ``request_logs`` row.

Before this module the only pre-provider rows were the policy blocks
(DLP / guardrail / moderation, which persist their own ``blocked`` row
before raising). Every other failure that happens before a provider is
called — ``model_not_served`` / ``provider_key_missing`` /
``provider_keys_stale`` from the key diagnosis, ``no_capable_model``
from the router, ``no_allowed_provider`` from the provider boundary, the
request hard timeout, an internal error — went to
``handle_gateway_error``, which only logs. FIX129 measured it: 12
dead-model turns, 0 rows, so ``error_rate`` and ``no_successful_turn``
(both read ``request_logs``) could not see a dead model at all.

How a row is marked: ``status = "pre_provider_error"`` with
``provider = ""``, ``error_code`` = the error's code, zero tokens and zero
cost. A status of its own (not ``policy_action``: that column is the DLP
decision and the admin request log shows it as the DLP chip) — it is
outside ``("success", "blocked")`` so ``error_rate`` counts it, it is not
``success`` so ``no_successful_turn`` counts it, and it is not
``"error"`` so ``provider_failure`` (a provider really failing) does not.
The admin request log shows any non-success status as a failure; the
log-worker inserter copies ``status`` as text; usage and billing sum
tokens and cost, which are 0 here. Nothing is charged.

Which failures write one:

* every exception that leaves the chat pipeline after the request's
  tenant/project context was resolved, while no provider call has been
  started and no row has been written for the turn;
* NOT an admission refusal — ``rate_limit_exceeded``, ``quota_exceeded``
  (quota, budget, no portal credit) or an access refusal
  (``authentication_error`` / ``authorization_error``, e.g. a suspended
  portal user or a policy-profile escalation): the system worked, the
  caller was told no, and each of those has its own ledger (rate
  limiter, quota tables, credit ledger). Writing them would let one
  throttled client trip ``error_rate``, and would add a DB write to the
  one path whose job is to refuse cheaply;
* NOT a policy block (``PolicyViolationError`` family): it already wrote
  its ``blocked`` row;
* NOT a malformed request body: FastAPI rejects it (422) before the
  pipeline runs — a client bug, not a turn.

The turn ledger rides a ContextVar opened by the ``/v1/chat/completions``
wrapper; the orchestrator notes the resolved context and the start of
provider dispatch, and ``RequestLogService`` / ``request_log_queue`` note
every row they write, so the wrapper never writes a second row for a turn
that already has one. Outside a chat request every hook is a no-op.
"""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from app.domain.entities import RequestContext

logger = structlog.get_logger(__name__)


@dataclass
class _TurnLedger:
    model: str
    stream: bool
    started: float = field(default_factory=time.perf_counter)
    ctx: "RequestContext | None" = None
    provider_called: bool = False
    row_written: bool = False


_turn: ContextVar[_TurnLedger | None] = ContextVar("fix130_pre_provider_turn", default=None)


def begin_turn(model: str, stream: bool) -> Token:
    """Open the ledger for one chat-completion request (the route wrapper)."""
    return _turn.set(_TurnLedger(model=model or "", stream=bool(stream)))


def end_turn(token: Token) -> None:
    _turn.reset(token)


def note_context(ctx: "RequestContext") -> None:
    """The request's tenant/project context is known (both chat paths)."""
    ledger = _turn.get()
    if ledger is not None:
        ledger.ctx = ctx


def note_provider_call() -> None:
    """A provider dispatch is about to start: later failures are not pre-provider."""
    ledger = _turn.get()
    if ledger is not None:
        ledger.provider_called = True


def note_row_written() -> None:
    """A ``request_logs`` row was written (or queued) for this turn."""
    ledger = _turn.get()
    if ledger is not None:
        ledger.row_written = True


def writes_a_row(exc: BaseException | None) -> bool:
    """Whether a failure of this kind gets a pre-provider row (module doc)."""
    from app.core.exceptions import (
        AuthenticationError,
        AuthorizationError,
        PolicyViolationError,
        QuotaExceededError,
        RateLimitExceededError,
    )

    return not isinstance(
        exc,
        (
            RateLimitExceededError,
            QuotaExceededError,
            AuthenticationError,
            AuthorizationError,
            PolicyViolationError,
        ),
    )


def error_code_of(exc: BaseException | None, default: str = "internal_error") -> str:
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and code else default


async def record_failure(
    *,
    model: str,
    exc: BaseException | None,
    error_code: str,
) -> bool:
    """Write the pre-provider row for the current turn if it owes one.

    Uses its OWN short session: the request session is about to be rolled
    back by its dependency (the error is propagating), and after a hard
    timeout it may be mid-statement. Never raises — a failed write is
    logged and the caller's error response goes out unchanged. Returns
    whether a row was written.
    """
    ledger = _turn.get()
    if ledger is None or ledger.ctx is None:
        return False
    if ledger.provider_called or ledger.row_written or not writes_a_row(exc):
        return False
    ctx = ledger.ctx
    from app.domain.observability import RequestRecord, RequestStatus
    from app.services.request_log_service import RequestLogService

    record = RequestRecord(
        request_id=ctx.request_id or f"req_{uuid.uuid4().hex}",
        tenant_id=ctx.tenant_id,
        project_id=ctx.project_id,
        user_id=getattr(ctx, "user_id", None),
        provider="",
        model=(model or ledger.model or "")[:100],
        status=RequestStatus.PRE_PROVIDER_ERROR,
        policy_action="allow",
        policy_profile_id=getattr(ctx, "policy_profile_id", None),
        latency_ms=int((time.perf_counter() - ledger.started) * 1000),
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        estimated_cost_usd=0.0,
        error_code=error_code[:100],
    )
    try:
        import app.core.db as _db

        factory = _db.get_async_session_factory()
        async with factory() as session:
            await RequestLogService().persist_async(session, record)
            await session.commit()
    except Exception as write_exc:  # noqa: BLE001 — never mask the turn's own error
        logger.warning(
            "pre_provider_row_write_failed",
            request_id=record.request_id,
            error_code=error_code,
            error_type=type(write_exc).__name__,
        )
        return False
    ledger.row_written = True
    logger.info(
        "pre_provider_row_written",
        request_id=record.request_id,
        tenant_id=record.tenant_id,
        model=record.model,
        error_code=error_code,
        stream=ledger.stream,
    )
    return True
