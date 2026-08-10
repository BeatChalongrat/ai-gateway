"""Centralized exception-to-HTTP-response mapping.

This module is the *only* place where domain exceptions are translated into
HTTP status codes and JSON bodies.  Domain code never imports FastAPI error
types; this handler bridges the gap.
"""

from __future__ import annotations

import structlog
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from app.core.responses import UTF8JSONResponse as JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ContextLengthExceededError,
    DLPViolationError,
    GatewayError,
    InternalServiceError,
    NotFoundError,
    PolicyViolationError,
    ProviderError,
    ProviderTimeoutError,
    ProviderNotImplementedError,
    ProviderUnavailableError,
    ProviderAtCapacityError,
    QuotaExceededError,
    RateLimitExceededError,
    RawEgressBlockedError,
    SearchDisabledError,
    SearchProvidersUnavailableError,
    SearchQueryBlockedError,
    UpstreamBadResponseError,
    ValidationError,
)

logger = structlog.get_logger(__name__)

# Map exception class → HTTP status code.
# Order does not matter — lookup is by exact class, then MRO walk.
_STATUS_MAP: dict[type[GatewayError], int] = {
    ValidationError: 422,
    AuthenticationError: 401,
    AuthorizationError: 403,
    NotFoundError: 404,
    ConflictError: 409,
    PolicyViolationError: 403,
    DLPViolationError: 403,
    RateLimitExceededError: 429,
    QuotaExceededError: 429,
    SearchDisabledError: 400,
    SearchProvidersUnavailableError: 503,
    # 403 like the other DLP blocks — the query itself is refused.
    SearchQueryBlockedError: 403,
    # 403 like search — the embedding/voice content itself is refused.
    RawEgressBlockedError: 403,
    ProviderTimeoutError: 504,
    ProviderUnavailableError: 503,
    # Exact-class entry beats the ProviderError→502 MRO fallback: a full
    # bulkhead is retryable-later, which is 503 semantics (+ Retry-After
    # via exc.headers).
    ProviderAtCapacityError: 503,
    ProviderNotImplementedError: 501,
    ProviderError: 502,
    UpstreamBadResponseError: 502,
    # 400, not 502: the request is the client's to fix (trim + retry).
    ContextLengthExceededError: 400,
    InternalServiceError: 500,
}


def _get_request_id(request: Request) -> str | None:
    """Extract request_id if RequestContextMiddleware has set it."""
    return getattr(request.state, "request_id", None)


def _status_for(exc: GatewayError) -> int:
    """Resolve HTTP status code by walking the exception's MRO."""
    for cls in type(exc).__mro__:
        if cls in _STATUS_MAP:
            return _STATUS_MAP[cls]
    return 500


def _build_body(
    code: str,
    message: str,
    details: dict | None,
    request_id: str | None,
) -> dict:
    body: dict = {
        "error": {
            "code": code,
            "message": message,
        },
        "request_id": request_id,
    }
    if details is not None:
        body["error"]["details"] = details
    return body


def _build_gateway_body(exc: GatewayError, request_id: str | None) -> dict:
    body = _build_body(exc.code, exc.message, exc.details, request_id)
    if isinstance(exc, DLPViolationError) and isinstance(exc.details, dict):
        for key in ("action", "categories", "findings_count"):
            if key in exc.details:
                body["error"][key] = exc.details[key]
    return body


# ---------------------------------------------------------------------------
# Exception handlers — registered on the FastAPI app in main.py
# ---------------------------------------------------------------------------


async def handle_gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
    """Handle all GatewayError subclasses."""
    status = _status_for(exc)
    request_id = _get_request_id(request)
    logger.warning(
        "gateway_error",
        error_code=exc.code,
        error_message=exc.message,
        status=status,
        request_id=request_id,
        path=request.url.path,
        method=request.method,
    )
    return JSONResponse(
        status_code=status,
        content=_build_gateway_body(exc, request_id),
        headers=getattr(exc, "headers", None),
    )


async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic / FastAPI request validation errors.

    Pydantic v2's ``errors()`` puts the raw exception (e.g. a
    ``ValueError`` raised from a ``field_validator``) under
    ``ctx.error``, which is not JSON-serializable. FastAPI's
    ``RequestValidationError.errors()`` does not accept Pydantic's
    ``include_context`` kwarg, so strip the non-serializable /
    noisy fields manually. ``url`` and ``input`` are also removed
    to keep the envelope compact and free of raw request content.
    """
    request_id = _get_request_id(request)
    _UNSAFE_KEYS = {"ctx", "url", "input"}
    errors = [
        {k: v for k, v in err.items() if k not in _UNSAFE_KEYS}
        for err in exc.errors()
    ]
    logger.warning(
        "validation_error",
        error_count=len(errors),
        request_id=request_id,
    )
    return JSONResponse(
        status_code=422,
        content=_build_body(
            code="validation_error",
            message="Request validation failed.",
            details={"errors": errors},
            request_id=request_id,
        ),
    )


async def handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Handle plain HTTPException (e.g. from deps.py 401) through the
    unified envelope so callers always see the same shape."""
    request_id = _get_request_id(request)
    return JSONResponse(
        status_code=exc.status_code,
        content=_build_body(
            code="http_error",
            message=str(exc.detail) if exc.detail else "HTTP error.",
            details=None,
            request_id=request_id,
        ),
    )


# Postgres SQLSTATEs that mean "the schema is behind the code" — a pending
# migration wasn't applied (FIX 3, Part C: defense-in-depth if the startup
# guard is disabled/bypassed). 42703 = undefined_column, 42P01 = undefined_table.
_SCHEMA_DRIFT_SQLSTATES = frozenset({"42703", "42P01"})


def _is_schema_out_of_date(exc: BaseException) -> bool:
    """Walk the exception chain (cause/context + SQLAlchemy ``.orig``) for a
    Postgres undefined-column/table error — the signature of a stale schema."""
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))
        code = getattr(cur, "sqlstate", None) or getattr(cur, "pgcode", None)
        if code in _SCHEMA_DRIFT_SQLSTATES:
            return True
        stack.append(getattr(cur, "orig", None))
        stack.append(getattr(cur, "__cause__", None))
        stack.append(getattr(cur, "__context__", None))
    return False


async def handle_unhandled_exception(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all for unexpected errors.  Never leak internals."""
    request_id = _get_request_id(request)

    # FIX 3, Part C — a missing column/table means a migration is pending.
    # Surface a clear, diagnosable error instead of an opaque internal_error.
    if _is_schema_out_of_date(exc):
        logger.error(
            "schema_out_of_date",
            exc_type=type(exc).__name__,
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )
        return JSONResponse(
            status_code=500,
            content=_build_body(
                code="schema_out_of_date",
                message=(
                    "Database schema is out of date — a pending migration must "
                    "be applied. Run 'alembic upgrade head' (or 'make migrate')."
                ),
                details=None,
                request_id=request_id,
            ),
        )

    logger.error(
        "unhandled_exception",
        exc_type=type(exc).__name__,
        request_id=request_id,
        path=request.url.path,
        method=request.method,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content=_build_body(
            code="internal_error",
            message="An internal service error occurred.",
            details=None,
            request_id=request_id,
        ),
    )
