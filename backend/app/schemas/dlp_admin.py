"""Schemas for the admin custom-DLP-detector API (Sprint 6, F)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

_Category = Literal[
    "pii", "identity", "financial", "credential", "secret", "network",
    "content_policy",
]
_DetectionType = Literal["regex", "keyword_list", "semantic"]
_Severity = Literal["low", "medium", "high", "critical"]
_Action = Literal["mask", "block", "pass", "restore"]


class TestCase(BaseModel):
    input: str
    expected_match: bool


class CreateDetectorRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = None
    category: _Category
    detection_type: _DetectionType
    pattern: dict  # validated by the service against detection_type
    severity: _Severity = "medium"
    default_action: _Action = "mask"
    test_cases: list[TestCase] = Field(default_factory=list)


class UpdateDetectorRequest(BaseModel):
    description: Optional[str] = None
    category: Optional[_Category] = None
    detection_type: Optional[_DetectionType] = None
    pattern: Optional[dict] = None
    severity: Optional[_Severity] = None
    default_action: Optional[_Action] = None
    test_cases: Optional[list[TestCase]] = None
    reason: Optional[str] = None


class DisableDetectorRequest(BaseModel):
    # Optional at the schema layer so an empty/missing reason is rejected by
    # the service with a 400 (not a 422), matching the other lifecycle errors.
    reason: Optional[str] = None


class TestDetectorRequest(BaseModel):
    inputs: list[str] = Field(default_factory=list)


class CustomDetectorResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    category: str
    detection_type: str
    status: str
    severity: str
    default_action: str
    test_passed: bool
    last_match_count: int
    last_matched_at: Optional[datetime] = None
    timeout_failures: int
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def from_orm_row(cls, d) -> "CustomDetectorResponse":
        return cls(
            id=d.id, name=d.name, description=d.description, category=d.category,
            detection_type=d.detection_type, status=d.status, severity=d.severity,
            default_action=d.default_action, test_passed=d.test_passed,
            last_match_count=d.last_match_count, last_matched_at=d.last_matched_at,
            timeout_failures=d.timeout_failures, created_at=d.created_at,
            updated_at=d.updated_at, version=d.version,
        )


class TestDetectorResult(BaseModel):
    input: str
    matched: bool
    latency_ms: float


class TestDetectorResponse(BaseModel):
    detector_id: str
    results: list[TestDetectorResult]


class AuditLogEntry(BaseModel):
    id: str
    action: str
    detector_name: str
    changed_by_user_id: str
    changed_at: datetime
    reason: Optional[str] = None
    before_value: Optional[dict] = None
    after_value: Optional[dict] = None
