"""Web-search DLP egress hardening (Gateway Phase 9, A1/A2/A4).

Pins the intentional-raw-egress contract for /v1/search:

* credential/secret in the query → 403 ``search_query_blocked``,
  provider never called, no credit deducted;
* identity PII (phone/email/Thai ID) → passes, provider receives the
  RAW query verbatim (masking is intentionally NOT done), and a
  ``raw_egress`` audit row (categories only, no values) is written;
* tenant ``search_pii_egress = "block"`` → identity PII fails closed;
* the credit-ledger row carries NO query text (A2);
* DLP disabled → everything passes untouched.
"""

import contextlib
import uuid

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.main import app as main_app
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.request_log import RequestLogORM
from app.models.tenant import TenantORM
from app.services.search.service import search_service


SECRET_QUERY = "why does sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345 return 401"
PII_QUERY = "prognosis for patient, contact 081-234-5678 or alice@example.com"
CLEAN_QUERY = "best restaurants in Chiang Mai"


class _FakeResults:
    provider = "tavily"
    results: list = []

    def to_dict(self) -> dict:
        return {"provider": self.provider, "results": []}


@contextlib.contextmanager
def _auth(tenant_id, portal_user_id):
    prev = main_app.dependency_overrides.get(get_resolved_api_key)
    main_app.dependency_overrides[get_resolved_api_key] = lambda: ResolvedApiKey(
        key_id="test-key-id",
        tenant_id=tenant_id,
        project_id="test-project",
        key_prefix="gw_test_",
        role="admin",
        portal_user_id=portal_user_id,
    )
    try:
        yield
    finally:
        if prev is not None:
            main_app.dependency_overrides[get_resolved_api_key] = prev
        else:
            main_app.dependency_overrides.pop(get_resolved_api_key, None)


@pytest.fixture
def capture_provider(monkeypatch):
    """Mock the search-provider layer, capturing the outbound query."""
    calls: list[str] = []

    async def _fake_search(session, tenant_id, query, max_results=5):
        calls.append(query)
        return _FakeResults()

    monkeypatch.setattr(search_service, "search", _fake_search)
    return calls


async def _seed(async_db, *, search_pii_egress="allow"):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    uid = f"u-{uuid.uuid4().hex[:8]}"
    async_db.add(TenantORM(
        tenant_id=tid, name="T", slug=tid,
        search_enabled=True, search_pii_egress=search_pii_egress,
    ))
    async_db.add(PortalUserORM(
        id=uid, email=f"{uid}@e.com", password_hash="x",
        credit_balance=10_000, credit_used=0,
    ))
    await async_db.commit()
    return tid, uid


@pytest.mark.asyncio
async def test_credential_query_is_blocked_before_provider_and_billing(
    async_db, capture_provider,
):
    tid, uid = await _seed(async_db)
    with _auth(tid, uid):
        resp = TestClient(main_app).post(
            "/v1/search", json={"query": SECRET_QUERY}
        )

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "search_query_blocked"
    assert capture_provider == []  # provider never called

    # No credit was deducted for the blocked query.
    txs = (
        await async_db.execute(
            select(CreditTransactionORM).where(
                CreditTransactionORM.user_id == uid
            )
        )
    ).scalars().all()
    assert txs == []


@pytest.mark.asyncio
async def test_identity_pii_passes_raw_and_is_audited(async_db, capture_provider):
    tid, uid = await _seed(async_db)
    with _auth(tid, uid):
        resp = TestClient(main_app).post("/v1/search", json={"query": PII_QUERY})

    assert resp.status_code == 200, resp.text
    # THE intentional-passthrough pin: the provider got the query VERBATIM.
    assert capture_provider == [PII_QUERY]

    # An audit row flags the raw egress — categories only, never values.
    row = (
        await async_db.execute(
            select(RequestLogORM).where(
                RequestLogORM.tenant_id == tid,
                RequestLogORM.policy_action == "raw_egress_allow",
            )
        )
    ).scalars().first()
    assert row is not None
    assert row.provider == "web_search"
    assert '"raw_egress": true' in (row.dlp_findings_json or "")
    for fragment in ("081-234-5678", "alice@example.com", "prognosis"):
        assert fragment not in (row.dlp_findings_json or "")
        assert fragment not in (row.dlp_categories or "")


@pytest.mark.asyncio
async def test_ledger_row_contains_no_query_text(async_db, capture_provider):
    tid, uid = await _seed(async_db)
    with _auth(tid, uid):
        resp = TestClient(main_app).post("/v1/search", json={"query": PII_QUERY})
    assert resp.status_code == 200, resp.text

    tx = (
        await async_db.execute(
            select(CreditTransactionORM).where(
                CreditTransactionORM.user_id == uid
            )
        )
    ).scalars().first()
    assert tx is not None
    blob = f"{tx.request_id} {tx.description}"
    # A2: no substring of the query in reference/description.
    for fragment in ("prognosis", "patient", "081-234-5678", "alice@example.com"):
        assert fragment not in blob
    assert tx.description == "web_search"
    assert tx.request_id.startswith("search:")


@pytest.mark.asyncio
async def test_tenant_block_policy_fails_closed_on_identity_pii(
    async_db, capture_provider,
):
    tid, uid = await _seed(async_db, search_pii_egress="block")
    with _auth(tid, uid):
        resp = TestClient(main_app).post("/v1/search", json={"query": PII_QUERY})

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "search_query_blocked"
    assert capture_provider == []


@pytest.mark.asyncio
async def test_clean_query_passes_with_no_raw_egress_flag(
    async_db, capture_provider,
):
    tid, uid = await _seed(async_db)
    with _auth(tid, uid):
        resp = TestClient(main_app).post("/v1/search", json={"query": CLEAN_QUERY})

    assert resp.status_code == 200, resp.text
    assert capture_provider == [CLEAN_QUERY]
    rows = (
        await async_db.execute(
            select(RequestLogORM).where(
                RequestLogORM.tenant_id == tid,
                RequestLogORM.policy_action == "raw_egress_allow",
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_dlp_disabled_passes_everything(async_db, capture_provider, monkeypatch):
    monkeypatch.setattr(settings, "dlp_enabled", False)
    tid, uid = await _seed(async_db)
    with _auth(tid, uid):
        resp = TestClient(main_app).post("/v1/search", json={"query": SECRET_QUERY})

    assert resp.status_code == 200, resp.text
    assert capture_provider == [SECRET_QUERY]
