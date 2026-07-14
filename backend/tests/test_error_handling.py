"""Tests for P0-03: Exception hierarchy + error handler middleware.

Verifies:
- Every domain exception maps to the correct HTTP status code
- Response body matches the ErrorResponse envelope shape
- Unhandled exceptions return 500 with no internal details
- Pydantic RequestValidationError returns structured 422
- Legacy HTTPException (deps.py) passes through the unified envelope
- Existing routes still work (regression guard)
"""

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

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
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitExceededError,
    UpstreamBadResponseError,
    ValidationError,
)
from app.main import app


# ---------------------------------------------------------------------------
# Test routes that raise specific exceptions (mounted only for testing)
# ---------------------------------------------------------------------------

_test_router = APIRouter(prefix="/_test_errors")


@_test_router.get("/gateway_error")
async def _raise_gateway_error():
    raise GatewayError("base gateway error")


@_test_router.get("/authentication")
async def _raise_auth():
    raise AuthenticationError()


@_test_router.get("/authorization")
async def _raise_authz():
    raise AuthorizationError()


@_test_router.get("/validation")
async def _raise_validation():
    raise ValidationError("Bad input", details={"field": "model", "issue": "unsupported"})


@_test_router.get("/not_found")
async def _raise_not_found():
    raise NotFoundError("Project not found", code="project_not_found")


@_test_router.get("/conflict")
async def _raise_conflict():
    raise ConflictError()


@_test_router.get("/policy_violation")
async def _raise_policy():
    raise PolicyViolationError("Blocked by finance policy")


@_test_router.get("/dlp_violation")
async def _raise_dlp():
    raise DLPViolationError("Credit card number detected")


@_test_router.get("/rate_limit")
async def _raise_rate_limit():
    raise RateLimitExceededError()


@_test_router.get("/quota_exceeded")
async def _raise_quota():
    raise QuotaExceededError()


@_test_router.get("/provider_error")
async def _raise_provider():
    raise ProviderError("OpenAI returned 500")


@_test_router.get("/provider_timeout")
async def _raise_timeout():
    raise ProviderTimeoutError()


@_test_router.get("/provider_unavailable")
async def _raise_unavailable():
    raise ProviderUnavailableError()


@_test_router.get("/upstream_bad_response")
async def _raise_upstream():
    raise UpstreamBadResponseError()


@_test_router.get("/context_length")
async def _raise_context_length():
    raise ContextLengthExceededError(
        details={"provider": "openai", "provider_message": "maximum context length is 128000 tokens"},
    )


@_test_router.get("/internal")
async def _raise_internal():
    raise InternalServiceError()


@_test_router.get("/unhandled")
async def _raise_unhandled():
    raise RuntimeError("something broke unexpectedly")


# Register test routes on the app
app.include_router(_test_router)
client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Exception hierarchy tests (pure Python, no HTTP)
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_all_subclass_gateway_error(self):
        classes = [
            AuthenticationError,
            AuthorizationError,
            ValidationError,
            NotFoundError,
            ConflictError,
            PolicyViolationError,
            DLPViolationError,
            RateLimitExceededError,
            QuotaExceededError,
            ProviderError,
            ProviderTimeoutError,
            ProviderUnavailableError,
            UpstreamBadResponseError,
            InternalServiceError,
        ]
        for cls in classes:
            assert issubclass(cls, GatewayError), f"{cls.__name__} must subclass GatewayError"

    def test_dlp_is_policy_subclass(self):
        assert issubclass(DLPViolationError, PolicyViolationError)

    def test_provider_timeout_is_provider_subclass(self):
        assert issubclass(ProviderTimeoutError, ProviderError)

    def test_provider_unavailable_is_provider_subclass(self):
        assert issubclass(ProviderUnavailableError, ProviderError)

    def test_custom_message(self):
        e = NotFoundError("Project xyz not found")
        assert e.message == "Project xyz not found"
        assert str(e) == "Project xyz not found"

    def test_custom_code(self):
        e = NotFoundError("nope", code="project_not_found")
        assert e.code == "project_not_found"

    def test_details_attached(self):
        e = ValidationError(details={"field": "model"})
        assert e.details == {"field": "model"}

    def test_default_message(self):
        e = AuthenticationError()
        assert e.message == "Authentication required."

    def test_catchable_by_base_class(self):
        with pytest.raises(GatewayError):
            raise ProviderTimeoutError()


# ---------------------------------------------------------------------------
# HTTP status code mapping tests
# ---------------------------------------------------------------------------


_EXCEPTION_STATUS_CASES = [
    ("gateway_error", 500),
    ("authentication", 401),
    ("authorization", 403),
    ("validation", 422),
    ("not_found", 404),
    ("conflict", 409),
    ("policy_violation", 403),
    ("dlp_violation", 403),
    ("rate_limit", 429),
    ("quota_exceeded", 429),
    ("provider_error", 502),
    ("provider_timeout", 504),
    ("provider_unavailable", 503),
    ("upstream_bad_response", 502),
    ("context_length", 400),
    ("internal", 500),
]


class TestStatusCodeMapping:
    @pytest.mark.parametrize("path,expected_status", _EXCEPTION_STATUS_CASES)
    def test_status_code(self, path, expected_status):
        resp = client.get(f"/_test_errors/{path}")
        assert resp.status_code == expected_status, (
            f"GET /_test_errors/{path}: expected {expected_status}, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Response envelope shape tests
# ---------------------------------------------------------------------------


class TestResponseEnvelope:
    def test_envelope_has_error_object(self):
        resp = client.get("/_test_errors/authentication")
        body = resp.json()
        assert "error" in body
        assert isinstance(body["error"], dict)
        assert "code" in body["error"]
        assert "message" in body["error"]

    def test_envelope_has_request_id(self):
        resp = client.get("/_test_errors/authentication")
        body = resp.json()
        # request_id is present but null until RequestContextMiddleware is wired (P0-06)
        assert "request_id" in body

    def test_custom_message_in_body(self):
        resp = client.get("/_test_errors/not_found")
        body = resp.json()
        assert body["error"]["message"] == "Project not found"

    def test_custom_code_in_body(self):
        resp = client.get("/_test_errors/not_found")
        body = resp.json()
        assert body["error"]["code"] == "project_not_found"

    def test_details_included_when_present(self):
        resp = client.get("/_test_errors/validation")
        body = resp.json()
        assert body["error"]["details"] == {"field": "model", "issue": "unsupported"}

    def test_details_absent_when_none(self):
        resp = client.get("/_test_errors/authentication")
        body = resp.json()
        assert "details" not in body["error"]

    def test_context_length_envelope(self):
        resp = client.get("/_test_errors/context_length")
        body = resp.json()
        assert resp.status_code == 400
        assert body["error"]["code"] == "context_length_exceeded"
        assert "context window" in body["error"]["message"]
        assert body["error"]["details"]["provider"] == "openai"
        assert "provider_message" in body["error"]["details"]


# ---------------------------------------------------------------------------
# Unhandled exception safety
# ---------------------------------------------------------------------------


class TestUnhandledExceptions:
    def test_returns_500(self):
        resp = client.get("/_test_errors/unhandled")
        assert resp.status_code == 500

    def test_does_not_leak_traceback(self):
        resp = client.get("/_test_errors/unhandled")
        body = resp.json()
        assert "traceback" not in str(body).lower()
        assert "RuntimeError" not in body["error"]["message"]
        assert body["error"]["code"] == "internal_error"

    def test_generic_message(self):
        resp = client.get("/_test_errors/unhandled")
        body = resp.json()
        assert body["error"]["message"] == "An internal service error occurred."


# ---------------------------------------------------------------------------
# Pydantic validation error (RequestValidationError)
# ---------------------------------------------------------------------------


class TestRequestValidationError:
    def test_bad_body_returns_422(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer demo-key"},
            json={"messages": "not a list"},
        )
        assert resp.status_code == 422

    def test_422_has_unified_envelope(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer demo-key"},
            json={"messages": "not a list"},
        )
        body = resp.json()
        assert body["error"]["code"] == "validation_error"
        assert "errors" in body["error"]["details"]

    def test_422_errors_list_is_pydantic_format(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer demo-key"},
            json={"messages": "not a list"},
        )
        errors = resp.json()["error"]["details"]["errors"]
        assert isinstance(errors, list)
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# Legacy HTTPException passthrough (deps.py 401)
# ---------------------------------------------------------------------------


class TestHTTPExceptionPassthrough:
    def test_missing_auth_returns_401(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401

    def test_missing_auth_has_unified_envelope(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "authentication_error"
        assert "authorization" in body["error"]["message"].lower()


# ---------------------------------------------------------------------------
# Regression: existing happy paths still work
# ---------------------------------------------------------------------------


class TestRegression:
    def test_health_still_works(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_chat_stub_still_works(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer demo-key"},
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hello"}],
                "metadata": {},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["provider"] in {"openai", "anthropic"}
