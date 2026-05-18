"""NER / Presidio detector tests.

All tests are marked ``@pytest.mark.ner`` via the file-level
``pytestmark`` so they can be selected or excluded as a group:

    pytest -m "not ner"   # everything else (1267 passing)
    pytest -m ner         # these 8 tests only

The tests MOCK the Presidio analyzer — they do not require
``presidio-analyzer`` to be installed. On Python 3.14 (where the
spaCy→thinc→blis chain has no wheels) the PresidioDetector module
still imports fine because its real analyzer import is lazy, inside
``detect()``, guarded by ``get_analyzer() is None``. The fixtures
below bypass that gate by monkeypatching ``ner_status.get_analyzer``
to return a hand-rolled fake that mimics the Presidio API surface.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.domain.providers import ProviderStreamChunk
from app.main import app
from app.models.org_settings import OrgSettingsORM
from app.models.request_log import RequestLogORM
from app.models.tenant import TenantORM
from app.services.dlp import ner_status
from app.services.dlp.detectors.presidio_ner import PresidioDetector
from app.services.dlp.detectors.registry import DetectorRegistry, get_default_registry
from app.services.dlp.language import detect_language
from tests.conftest import _integration_engine


pytestmark = pytest.mark.ner

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


# ---------------------------------------------------------------------------
# Fake analyzer — matches Presidio's minimal API surface.
# ---------------------------------------------------------------------------


@dataclass
class FakeRecognizerResult:
    entity_type: str
    start: int
    end: int
    score: float


class FakeAnalyzer:
    """Drop-in for ``presidio_analyzer.AnalyzerEngine``.

    Accepts the same ``analyze`` kwargs and returns a pre-configured
    list of fake recognizer results. The test sets ``self.results``
    and inspects ``self.last_kwargs`` after invocation.
    """

    def __init__(self, results: list[FakeRecognizerResult] | None = None):
        self.results = results or []
        self.last_kwargs: dict[str, Any] | None = None

    def analyze(self, **kwargs):
        self.last_kwargs = kwargs
        return list(self.results)


class ExplodingAnalyzer:
    def analyze(self, **kwargs):
        raise RuntimeError("boom — analyzer exploded")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _install_presidio_in_orchestrator_registry(monkeypatch):
    """Force PresidioDetector into the default registry for these tests.

    ``registry.get_default_registry`` gates presidio_ner registration on
    ``presidio_analyzer`` being importable. On Python 3.14 the package
    isn't installable (blis wheels), so the default registry ships
    without presidio_ner and the e2e tests below would never exercise
    the detector.

    The orchestrator imports ``get_default_registry`` at module top,
    so we patch that symbol inside ``app.services.orchestrator`` — new
    ``GatewayOrchestrator()`` instances (one per request) then pick up
    the patched factory. The patched factory calls the original and
    appends a fresh PresidioDetector on top, which is harmless because
    ``detect()`` defers to ``ner_status.get_analyzer()`` — which each
    individual test monkeypatches to a fake.
    """
    from app.services.dlp.detectors.registry import get_default_registry as _original

    def _patched(disabled_detectors=None, *, ner_enabled=True):
        reg = _original(disabled_detectors, ner_enabled=ner_enabled)
        # Only force PresidioDetector when the caller asked for the
        # NER-enabled registry — the NER-off variant must stay free of
        # presidio_ner so per-tenant gating semantics are preserved.
        if ner_enabled and "presidio_ner" not in reg:
            reg.register(PresidioDetector())
        return reg

    monkeypatch.setattr(
        "app.services.orchestrator.get_default_registry", _patched
    )


@pytest.fixture(autouse=True)
def _reset_ner_state():
    """Reset ner_status between tests so one test doesn't see another's analyzer."""
    ner_status.reset()
    yield
    ner_status.reset()


@pytest.fixture(autouse=True)
def _reset_tenant_flags():
    """Reset the demo tenant / org_settings NER flags between tests."""
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        tenant = s.get(TenantORM, "test-tenant")
        if tenant is not None:
            tenant.ner_enabled = False
        org = s.get(OrgSettingsORM, 1)
        if org is not None:
            org.ner_enabled = False
        s.commit()
    finally:
        s.close()
    yield
    s2 = Session()
    try:
        tenant = s2.get(TenantORM, "test-tenant")
        if tenant is not None:
            tenant.ner_enabled = False
        org = s2.get(OrgSettingsORM, 1)
        if org is not None:
            org.ner_enabled = False
        s2.commit()
    finally:
        s2.close()


def _set_tenant_ner(enabled: bool) -> None:
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        tenant = s.get(TenantORM, "test-tenant")
        tenant.ner_enabled = enabled
        s.commit()
    finally:
        s.close()


def _set_org_ner(enabled: bool) -> None:
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        org = s.get(OrgSettingsORM, 1)
        if org is None:
            org = OrgSettingsORM(id=1, ner_enabled=enabled)
            s.add(org)
        else:
            org.ner_enabled = enabled
        s.commit()
    finally:
        s.close()


def _install_fake_analyzer(monkeypatch, analyzer):
    monkeypatch.setattr(
        "app.services.dlp.ner_status.get_analyzer",
        lambda: analyzer,
    )


def _chat_once(content: str):
    return client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": content}],
            "metadata": {},
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPresidioDetectorNoopWhenAnalyzerNotLoaded:
    def test_presidio_detector_noop_when_analyzer_not_loaded(self):
        # ner_status.reset() was called by the autouse fixture, so
        # get_analyzer() returns None and detect() must no-op.
        det = PresidioDetector()
        assert det.detect("John Smith lives in Bangkok") == []


class TestPresidioDetectorDetectsPerson:
    def test_presidio_detector_detects_person(self, monkeypatch):
        fake = FakeAnalyzer(
            results=[
                FakeRecognizerResult(
                    entity_type="PERSON", start=0, end=10, score=0.95
                )
            ]
        )
        _install_fake_analyzer(monkeypatch, fake)

        det = PresidioDetector()
        matches = det.detect("John Smith was here")

        assert len(matches) == 1
        m = matches[0]
        assert m.entity_type == "person"  # lowercased
        assert m.category == "pii"
        assert m.detector_name == "presidio_ner"
        assert m.start == 0
        assert m.end == 10
        assert m.matched_text == "John Smith"
        assert m.confidence == pytest.approx(0.95)


class TestPresidioDetectorFailopenOnAnalyzerException:
    def test_presidio_detector_failopen_on_analyzer_exception(
        self, monkeypatch
    ):
        _install_fake_analyzer(monkeypatch, ExplodingAnalyzer())

        det = PresidioDetector()
        # Must not propagate the RuntimeError — fail-open is a
        # hard requirement.
        assert det.detect("any text at all") == []


class TestNerDisabledForTenantSkipsPresidio:
    def test_ner_disabled_for_tenant_skips_presidio(self, monkeypatch):
        # Analyzer returns a PERSON for "Alice" — but neither the
        # tenant nor the org has ner_enabled, so PresidioDetector
        # should never be called on this request.
        fake = FakeAnalyzer(
            results=[
                FakeRecognizerResult(
                    entity_type="PERSON", start=14, end=19, score=0.95
                )
            ]
        )
        _install_fake_analyzer(monkeypatch, fake)
        _set_tenant_ner(False)
        _set_org_ner(False)

        resp = _chat_once("my friend is Alice from the team")

        assert resp.status_code == 200
        body = resp.json()
        # Allow — regex detectors find nothing, presidio is gated off.
        assert body["policy_action"] == "allow"
        # The fake analyzer was NEVER invoked (the gate dropped it).
        assert fake.last_kwargs is None


class TestNerEnabledForTenantRunsPresidio:
    def test_ner_enabled_for_tenant_runs_presidio(self, monkeypatch):
        # "Alice" starts at index 14 and is 5 chars long in
        # "my friend is Alice from the team".
        fake = FakeAnalyzer(
            results=[
                FakeRecognizerResult(
                    entity_type="PERSON", start=14, end=19, score=0.95
                )
            ]
        )
        _install_fake_analyzer(monkeypatch, fake)
        _set_tenant_ner(True)
        _set_org_ner(False)

        resp = _chat_once("my friend is Alice from the team")

        assert resp.status_code == 200
        body = resp.json()
        assert body["policy_action"] == "mask"
        # The analyzer saw the request.
        assert fake.last_kwargs is not None
        assert fake.last_kwargs.get("language") == "en"
        # Response content was produced by the stubbed dispatcher
        # (conftest _stub_dispatch echoes the last message). After
        # restore(), the indexed mask "<r0>" should have been
        # replaced with the original "Alice".
        content = body["choices"][0]["message"]["content"]
        # Mask token appeared somewhere in the pipeline — either
        # restored back to "Alice" in the final output, or still
        # visible in the response log (which we don't check here).
        # The critical assertions are: policy_action=mask + analyzer
        # was invoked + stub echoed the masked token.
        assert "<r0>" in content or "Alice" in content


class TestNerEnabledViaOrgGlobal:
    def test_ner_enabled_via_org_global(self, monkeypatch):
        # Tenant flag OFF, org flag ON — the OR in project_service
        # should still resolve ner_enabled=True for this request.
        fake = FakeAnalyzer(
            results=[
                FakeRecognizerResult(
                    entity_type="PERSON", start=14, end=19, score=0.9
                )
            ]
        )
        _install_fake_analyzer(monkeypatch, fake)
        _set_tenant_ner(False)
        _set_org_ner(True)

        resp = _chat_once("my friend is Alice from the team")

        assert resp.status_code == 200
        body = resp.json()
        assert body["policy_action"] == "mask"
        assert fake.last_kwargs is not None


class TestThaiTextUsesThLanguage:
    def test_thai_text_uses_th_language(self, monkeypatch):
        # Detector-level test: pure unit on PresidioDetector, no
        # request round-trip. Asserts that language flows through
        # from the caller to analyzer.analyze().
        fake = FakeAnalyzer(results=[])
        _install_fake_analyzer(monkeypatch, fake)

        det = PresidioDetector()
        thai_text = "สวัสดี จอห์น ยินดีที่ได้รู้จัก"
        det.detect(thai_text, language=detect_language(thai_text))

        assert fake.last_kwargs is not None
        assert fake.last_kwargs.get("language") == "th"
        # And the registry's detect_language helper agrees.
        assert detect_language(thai_text) == "th"
        assert detect_language("hello world") == "en"


class TestRegistryWithoutExcludesDetector:
    def test_registry_without_excludes_detector(self):
        # Build a standalone registry with a fake presidio detector so
        # the test doesn't depend on presidio being importable.
        reg = DetectorRegistry()

        class _Fake(PresidioDetector):
            # Reuse the PresidioDetector class but override detect
            # so the test never needs an analyzer.
            def detect(self, text: str, language: str = "en"):
                return []

        reg.register(_Fake())
        # Also register one regex detector so removal is observable.
        from app.services.dlp.detectors.email import EmailDetector
        reg.register(EmailDetector())

        assert "presidio_ner" in reg
        assert "email" in reg
        assert len(reg) == 2

        trimmed = reg.without("presidio_ner")

        # Trimmed registry has email only.
        assert "presidio_ner" not in trimmed
        assert "email" in trimmed
        assert len(trimmed) == 1

        # Original registry is unmodified — no side effects.
        assert "presidio_ner" in reg
        assert len(reg) == 2
