"""FIX85 — a deployment where no key has an endpoint of its own behaves
exactly as it did before keys could have one.

Every key row that exists today has ``base_url = NULL`` after the
migration, so this file is the acceptance criterion for the round: it
uses ONLY the APIs that existed before FIX85 (``get_active_key``,
``resolve_base_url``, ``set_stored_base_url``, the adapters' ``chat``,
``test_key``, ``discover_sranai``, ``list_keys``) and pins what they do
with rows that carry no endpoint. It was run on the tree BEFORE the
change and again after; both runs must be green for the same reasons.

Nothing here references the new column or the new credential type on
purpose — a test that only passes on the new code proves nothing about
compatibility.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services import provider_key_service as pks
from app.services.provider_key_service import ProviderKeyService
from app.services.providers import base_urls

PROVIDER = "sranai"
BOX_A = "http://box-a:8000/v1"
BOX_B = "http://box-b:8000/v1"
KEY_1 = "sk-fix85-one-0123456789abcdef"
KEY_2 = "sk-fix85-two-0123456789abcdef"


def _add(session, label, raw):
    svc = ProviderKeyService()
    row = svc.add_key(session, PROVIDER, raw, label=label)
    svc.update_test_result(session, row.id, "valid", 7)
    return row


@pytest.fixture()
def two_null_keys(db_session):
    pks.clear_keys_cache()
    pks._rr_counters.pop(PROVIDER, None)
    base_urls.set_stored_base_url(db_session, PROVIDER, BOX_A)  # primes the resolver
    rows = [_add(db_session, "one", KEY_1), _add(db_session, "two", KEY_2)]
    pks.clear_keys_cache()
    yield rows
    pks.clear_keys_cache()
    pks._rr_counters.pop(PROVIDER, None)


def test_round_robin_over_creation_order_is_unchanged(db_session, two_null_keys):
    svc = ProviderKeyService()
    drawn = [svc.get_active_key(db_session, PROVIDER) for _ in range(4)]
    assert drawn == [KEY_1, KEY_2, KEY_1, KEY_2]
    # the resolved value IS the key: string equality, hashing, membership
    assert drawn[0] == KEY_1 and hash(drawn[0]) == hash(KEY_1)
    assert drawn[0] in {KEY_1}
    assert f"Bearer {drawn[0]}" == f"Bearer {KEY_1}"


def test_pool_is_the_same_list_in_the_same_order(db_session, two_null_keys):
    pool = ProviderKeyService().get_all_active_keys(db_session, PROVIDER)
    assert list(pool) == [KEY_1, KEY_2]


@pytest.mark.asyncio
async def test_dispatch_goes_to_the_provider_level_endpoint(db_session, two_null_keys, monkeypatch):
    """The request an endpoint-less key produces: provider row → box."""
    from app.services.providers import openai_compatible_adapter as mod
    from app.services.providers.sranai_adapter import SranAIAdapter

    seen: list[tuple[str, str]] = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "x", "object": "chat.completion", "created": 0, "model": "m",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    class _Client:
        def __init__(self, base_url):
            self.base_url = base_url

        async def post(self, path, json=None, headers=None):
            seen.append((self.base_url, headers["Authorization"]))
            return _Resp()

    async def _fake_get_client(*, base_url, **_kw):
        return _Client(base_url)

    monkeypatch.setattr(mod, "get_provider_client", _fake_get_client)

    key = ProviderKeyService().get_active_key(db_session, PROVIDER)
    payload = ChatCompletionsRequest(model="qwen-x", messages=[{"role": "user", "content": "hi"}])
    await SranAIAdapter().chat(payload, [Message(role="user", content="hi")], resolved_model="qwen-x", api_key=key)

    assert seen == [(BOX_A, f"Bearer {KEY_1}")]
    assert seen[0][0] == base_urls.resolve_base_url(PROVIDER, "")


@pytest.mark.asyncio
@respx.mock
async def test_key_retest_probes_the_provider_level_endpoint(db_session, two_null_keys):
    from app.api.routes.admin import test_provider_key

    route = respx.get(f"{BOX_A}/models").mock(return_value=httpx.Response(200, json={"data": []}))
    result = await test_provider_key(PROVIDER, two_null_keys[0].id, db_session)
    assert route.called and result["valid"] is True
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {KEY_1}"


@pytest.mark.asyncio
@respx.mock
async def test_discovery_asks_the_provider_level_endpoint(db_session, two_null_keys):
    from app.services.discovery.provider_discovery import discover_sranai

    route = respx.get(f"{BOX_A}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "qwen-x"}]})
    )
    key = ProviderKeyService().get_all_active_keys(db_session, PROVIDER)[0]
    models = await discover_sranai(key)
    assert route.called
    assert [(m.provider, m.model) for m in models] == [(PROVIDER, "qwen-x")]


def test_a_provider_level_move_still_stales_every_endpoint_less_key(db_session, two_null_keys):
    svc = ProviderKeyService()
    base_urls.set_stored_base_url(db_session, PROVIDER, BOX_B)
    assert svc.get_active_key(db_session, PROVIDER) is None
    assert svc.has_stale_keys(db_session, PROVIDER) is True
    statuses = {k["label"]: k["last_test_status"] for k in svc.list_keys(db_session, PROVIDER)}
    assert statuses == {"one": "stale", "two": "stale"}


def test_key_status_shape_and_the_default_endpoint_are_unchanged(db_session, two_null_keys):
    status = ProviderKeyService().get_key_status(db_session, PROVIDER)
    assert status["configured"] is True
    assert status["total_keys"] == 2 and status["valid_keys"] == 2
    assert status["source"] == "provider_keys"
    assert status["base_url"] == BOX_A


def test_listing_never_carries_the_key(db_session, two_null_keys):
    listed = ProviderKeyService().list_keys(db_session, PROVIDER)
    blob = json.dumps(listed)
    assert KEY_1 not in blob and KEY_2 not in blob
    assert {k["key_masked"] for k in listed} == {"••••" + KEY_1[-4:], "••••" + KEY_2[-4:]}
