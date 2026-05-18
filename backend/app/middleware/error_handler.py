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
    DLPViolationError,
    GatewayError,
    InternalServiceError,
    NotFoundError,
    PolicyViolationError,
    ProviderError,
    ProviderTimeoutError,
    ProviderNotImplementedError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitExceededError,
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
    ProviderTimeoutError: 504,
    ProviderUnavailableError: 503,
    ProviderNotImplementedError: 501,
    ProviderError: 502,
    UpstreamBadResponseError: 502,
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


async def handle_unhandled_exception(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all for unexpected errors.  Never leak internals."""
    request_id = _get_request_id(request)
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
