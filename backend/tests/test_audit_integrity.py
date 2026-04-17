"""P10-06: Immutable audit / compliance record tests."""

import json
import pytest

from app.services.admin_audit_service import AdminAuditService
from app.services.audit_integrity import (
    AuditIntegrityService, GENESIS_HASH, _compute_hash,
)
import app.models  # noqa: F401


class TestHashChain:
    def test_compute_hash_deterministic(self):
        h1 = _compute_hash(1, "create", "r1", GENESIS_HASH)
        h2 = _compute_hash(1, "create", "r1", GENESIS_HASH)
        assert h1 == h2
        assert len(h1) == 64

    def test_different_inputs_different_hash(self):
        h1 = _compute_hash(1, "create", "r1", GENESIS_HASH)
        h2 = _compute_hash(2, "create", "r1", GENESIS_HASH)
        assert h1 != h2

    def test_chain_depends_on_previous(self):
        h1 = _compute_hash(1, "create", "r1", GENESIS_HASH)
        h2 = _compute_hash(1, "create", "r1", "different_prev")
        assert h1 != h2


class TestAuditIntegrity:
    def test_verify_empty_chain(self, db_session):
        svc = AuditIntegrityService()
        result = svc.verify_chain(db_session)
        assert result["valid"] is True
        assert result["checked"] == 0

    def test_verify_chain_with_entries(self, db_session):
        audit = AdminAuditService()
        # Create entries without chain hashes (legacy)
        audit.record(
            db_session, actor_key_id="k1", tenant_id="t1",
            action="create", resource_type="project", resource_id="p1",
        )
        audit.record(
            db_session, actor_key_id="k1", tenant_id="t1",
            action="update", resource_type="project", resource_id="p1",
        )

        svc = AuditIntegrityService()
        result = svc.verify_chain(db_session)
        # Without stored hashes, chain verification passes (no stored hash to compare)
        assert result["valid"] is True
        assert result["checked"] == 2

    def test_compute_chain_hash_for_entry(self, db_session):
        audit = AdminAuditService()
        entry = audit.record(
            db_session, actor_key_id="k1", tenant_id="t1",
            action="create", resource_type="key", resource_id="k1",
        )
        svc = AuditIntegrityService()
        h = svc.compute_chain_hash(db_session, entry)
        assert len(h) == 64
        assert isinstance(h, str)
