"""FIX 1b — the gateway must actually be WIRED with SERVICE_JWT_SECRET, and a
dormant/half-config must fail loud.

FIX 1 shipped correct verify code (`_resolve_service_jwt`) but the secret never
reached the gateway (compose set it only under chat-edge), so the feature was
dormant and chat silently fell back to the header trust. These tests pin:

* the wiring decision (dormant when chat-edge is wired but the verify secret is
  empty; fine for a gateway-only deploy);
* prod fails closed on the dormant case;
* the ROUND-TRIP that was missing — a service JWT minted the way chat-edge
  mints it (iss/aud/HS256, shared secret) validates end-to-end;
* the compose files feed BOTH services from the SAME SERVICE_JWT_SECRET var.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import jwt
import pytest

from app.api.deps import _resolve_service_jwt
from app.core.config import settings

_SECRET = "shared-service-secret-0123456789abcdefghij"  # >= 32 bytes
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Wiring decision
# ---------------------------------------------------------------------------


def test_wiring_ok_when_secret_set(monkeypatch):
    monkeypatch.setattr(settings, "service_jwt_secret", _SECRET, raising=False)
    monkeypatch.setattr(settings, "internal_service_secret", "isvc", raising=False)
    assert settings.service_jwt_wiring_issue() is None


def test_wiring_dormant_when_wired_but_secret_empty(monkeypatch):
    # chat-edge wired (INTERNAL_SERVICE_SECRET set) + verify secret empty.
    monkeypatch.setattr(settings, "service_jwt_secret", "", raising=False)
    monkeypatch.setattr(settings, "internal_service_secret", "isvc", raising=False)
    issue = settings.service_jwt_wiring_issue()
    assert issue is not None
    assert "SERVICE_JWT_SECRET" in issue
    assert "fall back" in issue.lower()


def test_wiring_ok_for_gateway_only_deploy(monkeypatch):
    # No chat-edge (no INTERNAL_SERVICE_SECRET) → nothing expected, never bricks.
    monkeypatch.setattr(settings, "service_jwt_secret", "", raising=False)
    monkeypatch.setattr(settings, "internal_service_secret", "", raising=False)
    assert settings.service_jwt_wiring_issue() is None


# ---------------------------------------------------------------------------
# Prod fails closed on the dormant case
# ---------------------------------------------------------------------------


def _make_prod_config_valid(monkeypatch):
    """Set every prod-required secret to a strong value so validate_secrets'
    only remaining lever is the service-JWT wiring."""
    strong = "a" * 40
    hex32 = "ab" * 32  # 64 hex chars = 32 bytes
    monkeypatch.setattr(settings, "app_env", "production", raising=False)
    monkeypatch.setattr(settings, "jwt_secret", strong, raising=False)
    monkeypatch.setattr(settings, "master_encryption_key", strong, raising=False)
    monkeypatch.setattr(settings, "portal_jwt_secret", strong, raising=False)
    monkeypatch.setattr(settings, "dlp_audit_key", hex32, raising=False)
    monkeypatch.setattr(settings, "session_signing_secrets", strong, raising=False)
    monkeypatch.setattr(settings, "resend_api_key", "re_live_key_xxxx", raising=False)
    monkeypatch.setattr(settings, "provider_key_master_key", hex32, raising=False)


def test_prod_fails_closed_when_service_jwt_dormant(monkeypatch):
    _make_prod_config_valid(monkeypatch)
    monkeypatch.setattr(settings, "service_jwt_secret", "", raising=False)
    monkeypatch.setattr(settings, "internal_service_secret", "isvc", raising=False)
    with pytest.raises(RuntimeError, match="SERVICE_JWT_SECRET"):
        settings.validate_secrets()


def test_prod_ok_when_service_jwt_set(monkeypatch):
    _make_prod_config_valid(monkeypatch)
    monkeypatch.setattr(settings, "service_jwt_secret", "b" * 40, raising=False)
    monkeypatch.setattr(settings, "internal_service_secret", "isvc", raising=False)
    settings.validate_secrets()  # must not raise


def test_prod_ok_gateway_only(monkeypatch):
    # Gateway-only prod (no chat-edge) boots with no service secret.
    _make_prod_config_valid(monkeypatch)
    monkeypatch.setattr(settings, "service_jwt_secret", "", raising=False)
    monkeypatch.setattr(settings, "internal_service_secret", "", raising=False)
    settings.validate_secrets()  # must not raise


# ---------------------------------------------------------------------------
# The round-trip that was missing — sign like chat-edge, verify on the gateway
# ---------------------------------------------------------------------------


def _mint_like_chat_edge(secret: str) -> str:
    """Mirror chat-edge auth.IssueServiceJWT: iss=chat-edge-api,
    aud=gateway-api, HS256, short exp."""
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode(
        {
            "iss": "chat-edge-api",
            "aud": "gateway-api",
            "iat": now,
            "exp": now + dt.timedelta(seconds=60),
        },
        secret,
        algorithm="HS256",
    )


def test_shared_secret_round_trip(monkeypatch):
    # Both halves use the SAME secret — the check FIX 1 never had.
    monkeypatch.setattr(settings, "service_jwt_secret", _SECRET, raising=False)
    token = _mint_like_chat_edge(_SECRET)
    resolved = _resolve_service_jwt(
        token, x_user_id="user-1", x_tenant_id="tenant-1",
        trusted_conversation_id="conv-1",
    )
    assert resolved is not None
    assert resolved.portal_user_id == "user-1"
    assert resolved.tenant_id == "tenant-1"
    assert resolved.internal_trusted is True


def test_mismatched_secret_is_rejected(monkeypatch):
    # Gateway secret != the signing secret → token rejected (returns None).
    monkeypatch.setattr(settings, "service_jwt_secret", _SECRET, raising=False)
    token = _mint_like_chat_edge("a-different-secret-000000000000000000")
    assert _resolve_service_jwt(
        token, x_user_id="u", x_tenant_id="t", trusted_conversation_id=None
    ) is None


# ---------------------------------------------------------------------------
# Compose wiring — the config actually reaches BOTH services from one var
# ---------------------------------------------------------------------------


# FIX126 O8-4 — the compose files live at the repo root, which the backend
# lane container does not mount. Quarantined only where they are missing.
_Q_NO_COMPOSE = pytest.mark.quarantine(
    reason="environment: repo-root docker-compose*.yml are not mounted in the backend lane container",
    ticket="FIX126-O8-4",
    condition=not (REPO_ROOT / "docker-compose.yml").is_file(),
)


@_Q_NO_COMPOSE
def test_compose_feeds_service_jwt_to_both_services():
    base = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    # Both the Python backend (gateway/verifier) AND chat-edge (signer) must
    # pull SERVICE_JWT_SECRET from the SAME env var so they always match.
    assert base.count("SERVICE_JWT_SECRET: ${SERVICE_JWT_SECRET") >= 2, (
        "SERVICE_JWT_SECRET must be wired to BOTH backend and chat-edge-api "
        "from the same var in docker-compose.yml"
    )


@_Q_NO_COMPOSE
def test_prod_compose_hard_requires_service_jwt_on_backend():
    prod = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    # Prod backend must hard-require it (the :? form), like chat-edge does.
    assert "SERVICE_JWT_SECRET: ${SERVICE_JWT_SECRET:?" in prod
