"""FIX57 Part 3 + 4 — a key verdict must not outlive what it was about.

The incident: a sranai key was stamped ``valid`` at 10:24:36 and the
provider's endpoint became a different host at 10:24:40. Four seconds.
The badge went on saying ``valid`` for days, dispatch went on drawing
that key, and the provider answered 401 — which the user was shown as
"Couldn't reach the AI provider. Please try again."

Three separate lies, tested here as three separate things:

  * the verdict survived a change that made it meaningless    (Part 3)
  * dispatch used a key nobody had checked against that box   (Part 3)
  * a rejected credential was reported as a network hiccup    (Part 4)
"""

from __future__ import annotations

import httpx
import pytest

from app.core.exceptions import ProviderError
from app.services.providers.error_handler import map_provider_error


# ---------------------------------------------------------------------------
# Part 3 — endpoint change retires the verdict
# ---------------------------------------------------------------------------


def _key_row(db_session, provider: str, label: str, status: str):
    from app.services.provider_key_service import ProviderKeyService

    svc = ProviderKeyService()
    row = svc.add_key(db_session, provider, f"sk-live-{label}-0123456789", label=label)
    svc.update_test_result(db_session, row.id, status, 12)
    return row


def test_changing_the_endpoint_marks_every_tested_key_stale(db_session):
    from app.services.provider_key_service import KEY_STATUS_STALE
    from app.services.providers import base_urls

    _key_row(db_session, "sranai", "one", "valid")
    _key_row(db_session, "sranai", "two", "valid")

    base_urls.set_stored_base_url(db_session, "sranai", "http://box-a:8000/v1")
    base_urls.set_stored_base_url(db_session, "sranai", "http://box-b:8000/v1")

    from app.repositories.provider_key_repository import ProviderKeyRepository

    rows = ProviderKeyRepository(db_session).list_by_provider("sranai")
    assert rows, "fixture wrote no keys"
    assert all(r.last_test_status == KEY_STATUS_STALE for r in rows), [
        (r.label, r.last_test_status) for r in rows
    ]


def test_first_endpoint_write_stales_nothing(db_session):
    """A provider that never had an endpoint has not had one CHANGED.

    The env seed writes the first value at boot, and turning every key on
    a fresh deployment into "needs retest" would be a false alarm on day
    one.
    """
    from app.services.providers import base_urls

    row = _key_row(db_session, "sranai", "only", "valid")
    base_urls.set_stored_base_url(db_session, "sranai", "http://box-a:8000/v1")

    db_session.refresh(row)
    assert row.last_test_status == "valid"


def test_rewriting_the_same_endpoint_stales_nothing(db_session):
    """Saving the same URL again is not a move."""
    from app.services.providers import base_urls

    base_urls.set_stored_base_url(db_session, "sranai", "http://box-a:8000/v1")
    row = _key_row(db_session, "sranai", "only", "valid")
    base_urls.set_stored_base_url(db_session, "sranai", "http://box-a:8000/v1/")

    db_session.refresh(row)
    assert row.last_test_status == "valid"


def test_an_invalid_key_is_not_softened_into_stale(db_session):
    """A key the provider actively rejected stays rejected.

    Downgrading it to "unknown" would quietly move it back onto the
    operator's list of things that might be fine.
    """
    from app.services.providers import base_urls

    bad = _key_row(db_session, "sranai", "bad", "invalid")
    base_urls.set_stored_base_url(db_session, "sranai", "http://box-a:8000/v1")
    base_urls.set_stored_base_url(db_session, "sranai", "http://box-b:8000/v1")

    db_session.refresh(bad)
    assert bad.last_test_status == "invalid"


def test_dispatch_will_not_draw_a_stale_key(db_session):
    """Without this the rule is decoration: the badge would say "needs
    retest" while requests kept going out on the same key."""
    from app.services.provider_key_service import ProviderKeyService
    from app.services.providers import base_urls

    _key_row(db_session, "sranai", "only", "valid")
    svc = ProviderKeyService()
    assert svc.get_active_key(db_session, "sranai") is not None

    base_urls.set_stored_base_url(db_session, "sranai", "http://box-a:8000/v1")
    base_urls.set_stored_base_url(db_session, "sranai", "http://box-b:8000/v1")

    assert svc.get_active_key(db_session, "sranai") is None
    # ...and the reason is reported as its own thing, so the chat path can
    # say "retest these" instead of "configure a key you can already see".
    assert svc.has_stale_keys(db_session, "sranai") is True


def test_retesting_clears_stale(db_session):
    """The badge has to be escapable, or it is just a broken provider."""
    from app.services.provider_key_service import ProviderKeyService
    from app.services.providers import base_urls

    row = _key_row(db_session, "sranai", "only", "valid")
    base_urls.set_stored_base_url(db_session, "sranai", "http://box-a:8000/v1")
    base_urls.set_stored_base_url(db_session, "sranai", "http://box-b:8000/v1")

    svc = ProviderKeyService()
    svc.update_test_result(db_session, row.id, "valid", 20)

    assert svc.get_active_key(db_session, "sranai") is not None
    assert svc.has_stale_keys(db_session, "sranai") is False


def test_stale_provider_is_not_counted_as_having_a_valid_key(db_session):
    """The public model list is filtered on this set, so a provider whose
    keys are all waiting on a retest must drop out of it."""
    from app.services.provider_key_service import ProviderKeyService
    from app.services.providers import base_urls

    _key_row(db_session, "sranai", "only", "valid")
    svc = ProviderKeyService()
    assert "sranai" in svc.providers_with_valid_keys(db_session)

    base_urls.set_stored_base_url(db_session, "sranai", "http://box-a:8000/v1")
    base_urls.set_stored_base_url(db_session, "sranai", "http://box-b:8000/v1")

    assert "sranai" not in svc.providers_with_valid_keys(db_session)


# ---------------------------------------------------------------------------
# Part 4 — a rejected credential is not a network hiccup
# ---------------------------------------------------------------------------


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://gateway.example/v1/chat/completions")
    response = httpx.Response(status, json={"error": "Unauthorized"}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.parametrize("status", [401, 403])
def test_provider_rejection_gets_its_own_code(status):
    exc = map_provider_error(_http_error(status), "sranai")
    assert isinstance(exc, ProviderError)
    assert exc.code == "provider_auth_error", (
        "a 401 used to arrive as the generic provider_error, whose message "
        "is 'Couldn't reach the AI provider. Please try again.' — the "
        "provider WAS reached, and trying again cannot help"
    )
    assert exc.details["status"] == status


def test_provider_rejection_is_not_offered_as_retryable():
    from app.core.error_catalog import describe

    info = describe("provider_auth_error")
    assert info.retryable is False
    # The sentence has to point at the key. "Try again" was not merely
    # useless here, it pointed away from the only thing that fixes it.
    assert "key" in info.message.lower() or "credential" in info.message.lower()
    assert "try again" not in info.message.lower()


def test_stale_keys_error_is_not_retryable_and_says_what_to_do():
    from app.core.error_catalog import describe

    info = describe("provider_keys_stale")
    assert info.retryable is False
    assert "endpoint" in info.message.lower()
    assert "test" in info.message.lower()


def test_rejected_credentials_still_reach_the_fallback_chain():
    """The new code must not cost us the routing behaviour the old one had.

    Under ``provider_error`` a 401 was fallback-eligible by accident of
    sharing that code; routing around a dead key to a provider whose key
    works is exactly what the chain is for, so it stays eligible on
    purpose — and account-scoped, so the walk does not try the same dead
    credential on the provider's other models.
    """
    from app.services.gateway.fallback_orchestrator import (
        classify_failure_scope,
        is_fallback_eligible,
    )

    exc = map_provider_error(_http_error(401), "sranai")
    assert is_fallback_eligible(exc) is True
    assert classify_failure_scope(exc) == "account"
