"""P6-02: Observability domain contract tests."""

import json
from datetime import datetime, timezone

import pytest

from app.domain.observability import RequestRecord, RequestStatus, UsageRecord


class TestRequestRecord:
    def test_defaults(self):
        r = RequestRecord(request_id="r1", tenant_id="t1", project_id="p1")
        assert r.status == RequestStatus.SUCCESS
        assert r.latency_ms == 0
        assert r.provider == ""
        assert r.error_code is None

    def test_complete_sets_status_and_latency(self):
        r = RequestRecord(request_id="r1", tenant_id="t1", project_id="p1")
        r.complete(status=RequestStatus.PROVIDER_ERROR, latency_ms=250)
        assert r.status == RequestStatus.PROVIDER_ERROR
        assert r.latency_ms == 250
        assert r.completed_at is not None

    def test_to_dict_serializable(self):
        r = RequestRecord(request_id="r1", tenant_id="t1", project_id="p1",
                          provider="openai", model="gpt-4o")
        r.complete(latency_ms=100)
        d = r.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["request_id"] == "r1"
        assert parsed["latency_ms"] == 100

    def test_to_dict_no_raw_content(self):
        r = RequestRecord(request_id="r1", tenant_id="t1", project_id="p1")
        d = r.to_dict()
        for key in d:
            assert "content" not in key.lower()
            assert "message" not in key.lower() or key == "error_message"

    def test_started_at_auto_set(self):
        r = RequestRecord(request_id="r1", tenant_id="t1", project_id="p1")
        assert r.started_at is not None
        assert r.started_at.tzinfo is not None


class TestUsageRecord:
    def test_construction(self):
        u = UsageRecord(
            request_id="r1", tenant_id="t1", project_id="p1",
            provider="openai", model="gpt-4o",
            prompt_tokens=10, completion_tokens=20, total_tokens=30,
            estimated_cost_usd=0.001, is_estimate=False,
        )
        assert u.total_tokens == 30
        assert u.is_estimate is False

    def test_defaults(self):
        u = UsageRecord(request_id="r1", tenant_id="t1", project_id="p1",
                        provider="p", model="m")
        assert u.is_estimate is True
        assert u.total_tokens == 0

    def test_frozen(self):
        u = UsageRecord(request_id="r1", tenant_id="t1", project_id="p1",
                        provider="p", model="m")
        with pytest.raises(AttributeError):
            u.total_tokens = 5  # type: ignore[misc]


class TestRequestStatus:
    def test_enum_values(self):
        assert RequestStatus.SUCCESS.value == "success"
        assert RequestStatus.BLOCKED.value == "blocked"
        assert RequestStatus.PROVIDER_ERROR.value == "provider_error"
        assert RequestStatus.AUTH_ERROR.value == "auth_error"
