"""FIX85 — an endpoint belongs to the key, not to the provider.

The incident: ``Typhoon`` is a key for one box and ``admin`` is a key for
another, and the product stored ONE endpoint per provider. Adding the
key for box B moved the provider's endpoint, which (correctly, FIX57)
retired box A's verdicts — and a real test then said "invalid" about a
box the key was never for.

Part A  the pair travels together and is read in one place
Part B  the stale rule's blast radius is exactly the keys that moved
Part C  a model resolves to a key whose box reported it
Part 6  a verdict says which box reached it
gate    the SSRF gate is the same one; a key never rides along an endpoint
"""

from __future__ import annotations

import json
import pickle

import httpx
import pytest
import respx

from app.core.exceptions import ModelNotServed, ProviderKeysStale, ValidationError
from app.models.model_config import ModelConfigORM
from app.services import provider_key_service as pks
from app.services.provider_key_service import ProviderKeyService
from app.services.providers import base_urls
from app.services.providers.credential import ProviderCredential, endpoint_of

PROVIDER = "sranai"
BOX_A = "http://box-a:8000/v1"
BOX_B = "http://box-b:8000/v1"
KEY_A = "sk-fix85-box-a-0123456789abcdef"
KEY_B = "sk-fix85-box-b-0123456789abcdef"
KEY_INHERIT = "sk-fix85-inherit-0123456789abcdef"


def _add(session, label, raw, base_url=None, status="valid"):
    svc = ProviderKeyService()
    row = svc.add_key(session, PROVIDER, raw, label=label, base_url=base_url)
    svc.update_test_result(session, row.id, status, 7, tested_base_url=base_url or BOX_A)
    return row


@pytest.fixture(autouse=True)
def _fresh_caches():
    pks.clear_keys_cache()
    base_urls.clear_base_url_cache()
    pks._rr_counters.pop(PROVIDER, None)
    yield
    pks.clear_keys_cache()
    base_urls.clear_base_url_cache()
    pks._rr_counters.pop(PROVIDER, None)


@pytest.fixture()
def two_boxes(db_session):
    """Provider default = box A; ``a`` inherits it, ``b`` has box B of its own."""
    base_urls.set_stored_base_url(db_session, PROVIDER, BOX_A)
    a = _add(db_session, "a", KEY_A)
    b = _add(db_session, "b", KEY_B, base_url=BOX_B)
    pks.clear_keys_cache()
    return a, b


# ---------------------------------------------------------------------------
# The credential
# ---------------------------------------------------------------------------


def test_credential_is_the_key_and_carries_the_endpoint():
    c = ProviderCredential(KEY_B, base_url=BOX_B + "/", key_id="k", label="b")
    assert c == KEY_B and hash(c) == hash(KEY_B) and c in {KEY_B}
    assert f"Bearer {c}" == f"Bearer {KEY_B}"
    assert c.base_url == BOX_B  # trailing slash stripped
    assert c.api_key == KEY_B and type(c.api_key) is str
    assert endpoint_of(c) == BOX_B and endpoint_of(KEY_B) is None and endpoint_of(None) is None


def test_credential_repr_never_prints_the_key():
    c = ProviderCredential(KEY_B, base_url=BOX_B, key_id="k", label="b")
    assert KEY_B not in repr(c) and BOX_B in repr(c)
    assert KEY_B[-4:] in repr(c)


def test_credential_survives_pickling_with_its_endpoint():
    c = ProviderCredential(KEY_B, base_url=BOX_B, key_id="k", label="b")
    back = pickle.loads(pickle.dumps(c))
    assert back == KEY_B and back.base_url == BOX_B and back.key_id == "k"


# ---------------------------------------------------------------------------
# Part A — the pair travels together
# ---------------------------------------------------------------------------


def test_each_key_comes_back_with_its_own_box(db_session, two_boxes):
    pool = ProviderKeyService().get_all_active_keys(db_session, PROVIDER)
    assert [(k, k.base_url) for k in pool] == [(KEY_A, None), (KEY_B, BOX_B)]
    assert base_urls.effective_base_url(PROVIDER, "", pool[0]) == BOX_A
    assert base_urls.effective_base_url(PROVIDER, "", pool[1]) == BOX_B


@pytest.mark.asyncio
async def test_dispatch_sends_each_key_to_its_own_box(db_session, two_boxes, monkeypatch):
    from app.schemas.common import Message
    from app.schemas.public import ChatCompletionsRequest
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

    async def _fake(*, base_url, **_):
        return _Client(base_url)

    monkeypatch.setattr(mod, "get_provider_client", _fake)
    svc = ProviderKeyService()
    payload = ChatCompletionsRequest(model="m", messages=[{"role": "user", "content": "hi"}])
    msgs = [Message(role="user", content="hi")]
    for _ in range(2):
        key = svc.get_active_key(db_session, PROVIDER)
        await SranAIAdapter().chat(payload, msgs, resolved_model="m", api_key=key)
    assert seen == [(BOX_A, f"Bearer {KEY_A}"), (BOX_B, f"Bearer {KEY_B}")]


@pytest.mark.asyncio
@respx.mock
async def test_retest_probes_the_pair_not_the_provider_default(db_session, two_boxes):
    from app.api.routes.admin import test_provider_key

    a_route = respx.get(f"{BOX_A}/models").mock(return_value=httpx.Response(200, json={"data": []}))
    b_route = respx.get(f"{BOX_B}/models").mock(return_value=httpx.Response(401, json={"error": "nope"}))
    _a, b = two_boxes
    result = await test_provider_key(PROVIDER, b.id, db_session)
    assert b_route.called and not a_route.called
    assert result["valid"] is False and result["tested_base_url"] == BOX_B
    db_session.refresh(b)
    assert b.last_test_status == "invalid" and b.last_tested_base_url == BOX_B


@pytest.mark.asyncio
@respx.mock
async def test_discovery_asks_the_key_s_own_box(db_session, two_boxes):
    from app.services.discovery.provider_discovery import discover_sranai

    respx.get(f"{BOX_A}/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "on-a"}]}))
    respx.get(f"{BOX_B}/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "on-b"}]}))
    pool = ProviderKeyService().get_all_active_keys(db_session, PROVIDER)
    assert [m.model for m in await discover_sranai(pool[0])] == ["on-a"]
    assert [m.model for m in await discover_sranai(pool[1])] == ["on-b"]


def test_adapters_never_look_the_endpoint_up_beside_the_key():
    """The defect was two lookups. Every adapter now asks ONE function
    with the key in hand; a bare ``resolve_base_url`` in an adapter is
    the old shape coming back."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "providers"
    offenders = []
    for path in root.glob("*_adapter.py"):
        text = path.read_text(encoding="utf-8")
        if "resolve_base_url(" in text:
            offenders.append(path.name)
    assert offenders == [], offenders


def test_the_two_caches_are_invalidated_together(db_session, two_boxes):
    """A provider-level endpoint write drops the resolved-key cache too,
    so an inheriting key can never be served with a stale endpoint."""
    svc = ProviderKeyService()
    assert svc.get_active_key(db_session, PROVIDER) is not None
    assert PROVIDER in pks._keys_cache
    base_urls.set_stored_base_url(db_session, PROVIDER, BOX_A)  # same value: no move
    assert PROVIDER not in pks._keys_cache


# ---------------------------------------------------------------------------
# Part B — the stale rule's blast radius
# ---------------------------------------------------------------------------


def test_moving_the_default_stales_only_the_keys_that_inherit_it(db_session, two_boxes):
    a, b = two_boxes
    base_urls.set_stored_base_url(db_session, PROVIDER, "http://box-a2:8000/v1")
    db_session.refresh(a)
    db_session.refresh(b)
    assert a.last_test_status == "stale"
    assert b.last_test_status == "valid"  # box B did not move
    # ...and dispatch still has box B's key
    assert ProviderKeyService().get_active_key(db_session, PROVIDER) == KEY_B


def test_moving_one_key_stales_that_key_only(db_session, two_boxes):
    a, b = two_boxes
    out = ProviderKeyService().set_key_base_url(db_session, b.id, "http://box-b2:8000/v1")
    assert out == {"previous": BOX_B, "current": "http://box-b2:8000/v1", "changed": True, "staled": True}
    db_session.refresh(a)
    db_session.refresh(b)
    assert b.last_test_status == "stale" and b.base_url == "http://box-b2:8000/v1"
    assert a.last_test_status == "valid"


def test_rewriting_the_same_key_endpoint_is_not_a_move(db_session, two_boxes):
    _a, b = two_boxes
    out = ProviderKeyService().set_key_base_url(db_session, b.id, BOX_B + "/")
    assert out["changed"] is False and out["staled"] is False
    db_session.refresh(b)
    assert b.last_test_status == "valid"


def test_a_rejected_key_is_not_softened_by_moving_it(db_session, two_boxes):
    _a, b = two_boxes
    ProviderKeyService().update_test_result(db_session, b.id, "invalid", 5, tested_base_url=BOX_B)
    out = ProviderKeyService().set_key_base_url(db_session, b.id, "http://box-b2:8000/v1")
    assert out["changed"] is True and out["staled"] is False
    db_session.refresh(b)
    assert b.last_test_status == "invalid"


def test_stale_still_means_nobody_asked_and_is_withheld(db_session, two_boxes):
    a, _b = two_boxes
    base_urls.set_stored_base_url(db_session, PROVIDER, "http://box-a2:8000/v1")
    svc = ProviderKeyService()
    assert svc.has_stale_keys(db_session, PROVIDER) is True
    keys = {k["label"]: k for k in svc.list_keys(db_session, PROVIDER)}
    assert keys["a"]["last_test_status"] == "stale"
    assert keys["a"]["effective_base_url"] == "http://box-a2:8000/v1"
    assert keys["a"]["endpoint_source"] == "provider"
    assert keys["b"]["endpoint_source"] == "key"
    # a retest clears it
    svc.update_test_result(db_session, a.id, "valid", 3, tested_base_url="http://box-a2:8000/v1")
    assert svc.has_stale_keys(db_session, PROVIDER) is False


def test_system_settings_edit_of_the_default_is_a_move_too(db_session, two_boxes):
    """ae949b2 closed the hole on the providers route; the system-settings
    route wrote the same row directly and staled nothing."""
    from app.api.routes.admin_system_settings import SettingUpdate, update_setting

    a, b = two_boxes
    update_setting(
        "provider_base_url:sranai",
        SettingUpdate(value="http://box-a3:8000/v1"),
        db_session,
    )
    db_session.refresh(a)
    db_session.refresh(b)
    assert a.last_test_status == "stale" and b.last_test_status == "valid"
    assert base_urls.get_stored_base_url(db_session, PROVIDER) == "http://box-a3:8000/v1"


# ---------------------------------------------------------------------------
# Part C — which box has which model
# ---------------------------------------------------------------------------


def _model_row(provider, model, served_by=None, **over):
    kw = dict(
        provider=provider, model=model, supports_streaming=True, supports_tools=False,
        supports_vision=False, max_context_tokens=0, cost_tier="standard",
        latency_tier="standard", cost_per_input_token=0.0, cost_per_output_token=0.0,
        source="auto", is_manual_override=False, is_active=True, show_in_chat=True,
        served_by_keys=served_by or {},
    )
    kw.update(over)
    return ModelConfigORM(**kw)


@pytest.mark.asyncio
async def test_discovery_records_which_key_reported_each_model(db_session, two_boxes, monkeypatch):
    from app.services.discovery import discovery_service as ds_mod
    from app.services.discovery import provider_discovery
    from app.services.discovery.discovery_service import ModelDiscoveryService
    from app.services.discovery.pricing_fetcher import PricingFetcher

    a, b = two_boxes

    async def _by_box(api_key):
        box = base_urls.effective_base_url(PROVIDER, "", api_key)
        names = {BOX_A: ["shared", "only-a"], BOX_B: ["shared", "only-b"]}[box]
        return [provider_discovery.DiscoveredModel(provider=PROVIDER, model=n) for n in names]

    patched = dict(provider_discovery.PROVIDER_DISCOVERY)
    patched[PROVIDER] = _by_box
    monkeypatch.setattr(provider_discovery, "PROVIDER_DISCOVERY", patched)
    monkeypatch.setattr(ds_mod, "PROVIDER_DISCOVERY", patched)

    async def _no_pricing(self):
        return {}

    monkeypatch.setattr(PricingFetcher, "fetch", _no_pricing)

    result = await ModelDiscoveryService().sync_provider(PROVIDER, db_session)
    assert result.models_found == 3, result.to_dict()

    rows = {r.model: r for r in db_session.query(ModelConfigORM).filter_by(provider=PROVIDER).all()}
    assert set(rows) >= {"shared", "only-a", "only-b"}
    assert set(rows["only-a"].served_by_keys) == {a.id}
    assert set(rows["only-b"].served_by_keys) == {b.id}
    assert set(rows["shared"].served_by_keys) == {a.id, b.id}
    assert rows["only-b"].served_by_keys[b.id]["base_url"] == BOX_B
    assert rows["only-a"].served_by_keys[a.id]["base_url"] == BOX_A
    assert rows["only-a"].served_by_keys[a.id]["label"] == "a"

    # the admin list says how many models each key's box reported
    served = {k["label"]: k["models_served"] for k in ProviderKeyService().list_keys(db_session, PROVIDER)}
    assert served == {"a": 2, "b": 2}


def test_a_model_resolves_to_a_key_whose_box_reported_it(db_session, two_boxes):
    a, b = two_boxes
    db_session.add(_model_row(PROVIDER, "only-b", {b.id: {"base_url": BOX_B}}))
    db_session.add(_model_row(PROVIDER, "only-a", {a.id: {"base_url": BOX_A}}))
    db_session.add(_model_row(PROVIDER, "unrecorded"))
    db_session.commit()
    pks.clear_keys_cache()
    svc = ProviderKeyService()
    for _ in range(3):
        assert svc.get_active_key(db_session, PROVIDER, model="only-b") == KEY_B
    for _ in range(3):
        assert svc.get_active_key(db_session, PROVIDER, model="only-a") == KEY_A
    # nobody recorded it: any key of the provider, round-robin, as before
    drawn = {svc.get_active_key(db_session, PROVIDER, model="unrecorded") for _ in range(4)}
    assert drawn == {KEY_A, KEY_B}


def test_a_model_reported_by_two_boxes_may_use_either(db_session, two_boxes):
    a, b = two_boxes
    db_session.add(_model_row(PROVIDER, "shared", {a.id: {"base_url": BOX_A}, b.id: {"base_url": BOX_B}}))
    db_session.commit()
    pks.clear_keys_cache()
    svc = ProviderKeyService()
    assert {svc.get_active_key(db_session, PROVIDER, model="shared") for _ in range(4)} == {KEY_A, KEY_B}


def test_same_box_two_keys_still_round_robin(db_session):
    """A provider whose keys share one endpoint (every cloud provider, and
    a self-hosted box with two keys) keeps its round-robin: the claim is
    about the BOX, and a second key on that box reaches it too."""
    base_urls.set_stored_base_url(db_session, PROVIDER, BOX_A)
    a = _add(db_session, "a", KEY_A)
    _add(db_session, "a2", KEY_INHERIT)
    db_session.add(_model_row(PROVIDER, "m", {a.id: {"base_url": BOX_A}}))
    db_session.commit()
    pks.clear_keys_cache()
    svc = ProviderKeyService()
    assert [svc.get_active_key(db_session, PROVIDER, model="m") for _ in range(4)] == [KEY_A, KEY_INHERIT, KEY_A, KEY_INHERIT]


def test_a_model_nobody_serves_is_named_not_misrouted(db_session, two_boxes):
    a, b = two_boxes
    db_session.add(_model_row(PROVIDER, "only-b", {b.id: {"base_url": BOX_B}}))
    db_session.commit()
    svc = ProviderKeyService()
    # box B's key moves → stale → the model has no reachable box
    svc.set_key_base_url(db_session, b.id, "http://box-b2:8000/v1")
    assert svc.get_active_key(db_session, PROVIDER, model="only-b") is None
    # ...while the provider itself is still usable for other models
    assert svc.get_active_key(db_session, PROVIDER, model="anything-else") == KEY_A
    exc = svc.diagnose_missing_key(db_session, PROVIDER, "only-b")
    assert isinstance(exc, ModelNotServed) and exc.code == "model_not_served"
    assert "only-b" in str(exc) and KEY_A not in str(exc) and KEY_B not in str(exc)


def test_diagnosis_keeps_the_fix57_answers_when_no_model_is_involved(db_session, two_boxes):
    svc = ProviderKeyService()
    base_urls.set_stored_base_url(db_session, PROVIDER, "http://box-a2:8000/v1")
    svc.set_key_base_url(db_session, two_boxes[1].id, "http://box-b2:8000/v1")
    assert svc.get_active_key(db_session, PROVIDER) is None
    assert isinstance(svc.diagnose_missing_key(db_session, PROVIDER, "unrecorded"), ProviderKeysStale)


def test_model_not_served_has_a_sentence_for_the_person():
    from app.core.error_catalog import describe

    info = describe("model_not_served")
    assert info.retryable is False
    assert "key" in info.message.lower() and "try again" not in info.message.lower()


# ---------------------------------------------------------------------------
# Part 6 — the verdict says which box
# ---------------------------------------------------------------------------


def test_listing_says_which_box_reached_the_verdict(db_session, two_boxes):
    _a, b = two_boxes
    ProviderKeyService().update_test_result(db_session, b.id, "invalid", 5, tested_base_url=BOX_B)
    listed = {k["label"]: k for k in ProviderKeyService().list_keys(db_session, PROVIDER)}
    assert listed["b"]["last_test_status"] == "invalid"
    assert listed["b"]["last_tested_base_url"] == BOX_B
    status = ProviderKeyService().get_key_status(db_session, PROVIDER)
    assert status["invalid_keys"] == 1 and status["stale_keys"] == 0
    assert status["endpoint_configured"] is True
    base_urls.set_stored_base_url(db_session, PROVIDER, "http://box-a2:8000/v1")
    status = ProviderKeyService().get_key_status(db_session, PROVIDER)
    assert status["stale_keys"] == 1 and status["invalid_keys"] == 1


# ---------------------------------------------------------------------------
# The gate, and what never rides together
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_key_endpoint_goes_through_the_same_ssrf_gate(db_session, two_boxes):
    from app.api.routes.admin import set_provider_key_endpoint

    _a, b = two_boxes
    with pytest.raises(ValidationError) as exc:
        await set_provider_key_endpoint(PROVIDER, b.id, {"base_url": "http://169.254.169.254/latest"}, db_session)
    assert exc.value.code == "invalid_base_url"
    with pytest.raises(ValidationError):
        await set_provider_key_endpoint(PROVIDER, b.id, {"base_url": "ftp://box/v1"}, db_session)
    db_session.refresh(b)
    assert b.base_url == BOX_B  # nothing changed


@pytest.mark.asyncio
async def test_a_cloud_provider_key_cannot_be_given_an_endpoint(db_session):
    from app.api.routes.admin import add_provider_key, set_provider_key_endpoint

    row = ProviderKeyService().add_key(db_session, "openai", "sk-fix85-cloud-0123456789abcdef", label="c")
    with pytest.raises(ValidationError):
        await set_provider_key_endpoint("openai", row.id, {"base_url": BOX_B}, db_session)
    with pytest.raises(ValidationError):
        await add_provider_key("openai", {"api_key": "sk-fix85-cloud2-0123456789abcdef", "base_url": BOX_B}, db_session)


@pytest.mark.asyncio
async def test_a_key_without_an_endpoint_needs_a_default_to_exist(db_session):
    from app.api.routes.admin import add_provider_key

    with pytest.raises(ValidationError) as exc:
        await add_provider_key(PROVIDER, {"api_key": KEY_A}, db_session)
    assert exc.value.code == "endpoint_required"


@pytest.mark.asyncio
@respx.mock
async def test_adding_a_key_with_an_endpoint_does_not_move_the_provider_default(db_session, two_boxes, monkeypatch):
    """The incident, replayed: box A is the default, a key for box B is
    added WITH its endpoint. Before: the default became box B and box A's
    key went stale. After: box B's key owns box B; nothing else moves."""
    from app.api.routes.admin import add_provider_key
    from app.services.discovery.discovery_service import ModelDiscoveryService

    a, _b = two_boxes
    respx.get("http://box-c:8000/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))

    async def _no_sync(self, provider, session):
        from app.services.discovery.discovery_service import SyncResult

        return SyncResult(provider=provider)

    monkeypatch.setattr(ModelDiscoveryService, "sync_provider", _no_sync)
    out = await add_provider_key(PROVIDER, {"api_key": "sk-fix85-box-c-0123456789abcdef", "label": "c", "base_url": "http://box-c:8000/v1"}, db_session)
    assert out["valid"] is True and out["base_url"] == "http://box-c:8000/v1"
    assert base_urls.get_stored_base_url(db_session, PROVIDER) == BOX_A
    db_session.refresh(a)
    assert a.last_test_status == "valid"
    listed = {k["label"]: k for k in ProviderKeyService().list_keys(db_session, PROVIDER)}
    assert listed["c"]["endpoint_source"] == "key" and listed["c"]["last_tested_base_url"] == "http://box-c:8000/v1"


def test_no_response_or_error_carries_a_key_beside_an_endpoint(db_session, two_boxes):
    svc = ProviderKeyService()
    blobs = [
        json.dumps(svc.list_keys(db_session, PROVIDER)),
        json.dumps(svc.get_key_status(db_session, PROVIDER)),
        str(svc.diagnose_missing_key(db_session, PROVIDER, "x")),
        repr(svc.get_all_active_keys(db_session, PROVIDER)),
    ]
    for blob in blobs:
        assert KEY_A not in blob and KEY_B not in blob, blob[:120]
