"""Detector registry: collects, runs, and aggregates DLP detectors.

The registry is the entry point that the DLP scanner calls.  It runs all
registered detectors over input text, merges their matches, and returns
a sorted, normalized result.

Usage::

    registry = get_default_registry()
    result = registry.scan_messages(messages, direction="input")
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Callable, Optional

from app.domain.dlp import DetectionMatch, DetectionResult
from app.services.dlp.detectors.base import BaseDetector
from app.services.dlp.language import detect_language
from app.services.dlp.preprocessor import (
    NormalizedText,
    map_offset_to_original,
    normalize_for_scan,
)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.schemas.common import Message


# Sprint 6: process-wide provider of admin-defined custom detectors. Installed
# once at startup (the loader passes its sync cache reader) and attached to
# EVERY registry that get_default_registry builds — so custom detectors apply
# whether the orchestrator (and its two registry singletons) is shared or
# rebuilt per request. Default None → zero behaviour change.
_global_custom_provider: "Optional[Callable[[], list[BaseDetector]]]" = None


def set_global_custom_provider(
    provider: "Optional[Callable[[], list[BaseDetector]]]",
) -> None:
    """Install (or clear) the process-wide custom-detector provider."""
    global _global_custom_provider
    _global_custom_provider = provider


class DetectorRegistry:
    """Ordered collection of detectors with scan-and-aggregate methods."""

    def __init__(self) -> None:
        self._detectors: list[BaseDetector] = []
        self._names: set[str] = set()
        # Sprint 6: optional provider of admin-defined custom detectors,
        # hot-reloaded at runtime. Default None → zero behaviour change (the
        # built-in count + every existing detector test stays identical).
        self._custom_provider: Optional[Callable[[], list[BaseDetector]]] = None

    def set_custom_provider(
        self, provider: Optional[Callable[[], list[BaseDetector]]]
    ) -> None:
        """Install (or clear) the dynamic custom-detector provider."""
        self._custom_provider = provider

    # -- Registration -------------------------------------------------------

    def register(self, detector: BaseDetector) -> None:
        """Add a detector.  Raises ValueError on duplicate name."""
        if detector.name in self._names:
            raise ValueError(
                f"Detector with name {detector.name!r} is already registered"
            )
        self._detectors.append(detector)
        self._names.add(detector.name)

    # -- Queries ------------------------------------------------------------

    def all(self) -> list[BaseDetector]:
        """Return all registered detectors in registration order."""
        return list(self._detectors)

    def by_category(self, category: str) -> list[BaseDetector]:
        """Return detectors matching *category*."""
        return [d for d in self._detectors if d.category == category]

    def without(self, detector_name: str) -> "DetectorRegistry":
        """Return a shallow copy of the registry excluding one detector.

        Used by the orchestrator to disable NER on a per-request basis
        when a tenant (or the global org default) has NER turned off.
        The original registry is not mutated — the singleton held on
        ``GatewayOrchestrator`` is safe to reuse across requests.
        """
        copy = DetectorRegistry()
        copy._detectors = [
            d for d in self._detectors if d.name != detector_name
        ]
        copy._names = {d.name for d in copy._detectors}
        return copy

    def __len__(self) -> int:
        return len(self._detectors)

    def __contains__(self, name: str) -> bool:
        return name in self._names

    # -- Scanning -----------------------------------------------------------

    def _run_detectors(
        self, text: str, *, language: str = "en", from_decoded: bool = False
    ) -> list[DetectionMatch]:
        """Run all detectors over raw text (no preprocessing)."""
        matches: list[DetectionMatch] = []
        for detector in self._detectors:
            kwargs: dict[str, object] = {}
            if getattr(detector, "NEEDS_LANGUAGE", False):
                kwargs["language"] = language
            if from_decoded and getattr(detector, "ACCEPTS_DECODED", False):
                kwargs["from_decoded"] = True
            matches.extend(detector.detect(text, **kwargs))

        # Sprint 6: run admin-defined custom detectors after the built-ins.
        # Each custom detector's detect() is internally crash-safe, but we
        # also guard here so a provider/instantiation fault can never break
        # the scan for the built-ins.
        if self._custom_provider is not None:
            try:
                custom = self._custom_provider()
            except Exception:  # noqa: BLE001
                custom = []
                log.warning("custom detector provider failed", exc_info=True)
            for detector in custom:
                try:
                    matches.extend(detector.detect(text))
                except Exception:  # noqa: BLE001 — isolate a bad custom detector
                    log.warning(
                        "custom detector %r raised; skipped",
                        getattr(detector, "name", "?"),
                        exc_info=True,
                    )
        return matches

    def scan_text(
        self, text: str, *, language: str = "en"
    ) -> list[DetectionMatch]:
        """Run all detectors over *text* with preprocessing, return matches sorted by start offset.

        Preprocessing applies encoding-bypass protections (NFKC normalization,
        zero-width stripping, homoglyph mapping) before scanning. Base64
        segments are decoded and scanned separately; findings are mapped
        back to the original text positions.
        """
        normalized = normalize_for_scan(text)

        # Scan the normalized text
        matches = self._run_detectors(normalized.normalized, language=language)

        # Map offsets back to original text if zero-width chars were stripped
        if normalized.offset_map is not None:
            remapped: list[DetectionMatch] = []
            for m in matches:
                orig_start = map_offset_to_original(m.start, normalized.offset_map)
                orig_end = map_offset_to_original(m.end, normalized.offset_map)
                original_text = text[orig_start:orig_end]
                remapped.append(replace(
                    m,
                    start=orig_start,
                    end=orig_end,
                    matched_text=original_text,
                ))
            matches = remapped

        # Scan decoded base64 segments for additional findings
        for segment in normalized.base64_segments:
            segment_matches = self._run_detectors(segment.decoded_text, language=language, from_decoded=True)
            for sm in segment_matches:
                # Map findings back to the base64 span in original text
                matches.append(replace(
                    sm,
                    start=segment.original_start,
                    end=segment.original_end,
                    matched_text=segment.encoded_text,
                    metadata={"detected_via": "base64_decode", "decoded_match": sm.matched_text},
                ))

        matches.sort(key=lambda m: (m.start, m.end))
        return matches

    def scan_messages(
        self,
        messages: list[Message],
        *,
        direction: str = "input",
        scan_system_messages: bool = False,
        ner_skip_roles: frozenset[str] = frozenset(),
    ) -> DetectionResult:
        """Scan all message contents and return an aggregated DetectionResult.

        Parameters
        ----------
        messages : list[Message]
            Chat messages to scan (only the ``content`` field is inspected).
        direction : str
            ``"input"`` for request messages, ``"output"`` for response content.
        scan_system_messages : bool
            When False (the default), system-role messages are excluded
            from DLP scanning. Operator-authored system prompts ("Reply
            in Thai.", "You are a banking assistant.") regularly contain
            words that NER classifies as PERSON / NRP / ORGANIZATION,
            which would otherwise pollute span_map with bindings the
            user never authored — and the model can echo those tokens
            back, causing restore() to splice the system-prompt fragment
            into the user-facing reply. Tenants who legitimately place
            PII in system prompts can opt back in via PolicyProfile.
        ner_skip_roles : frozenset[str]
            Roles for which the Presidio NER detector's findings are
            dropped (regex detectors still run). P1 Item 1: pass
            ``{"assistant"}`` on inbound scans so prior AI turns in the
            conversation history don't get NER-scanned — that content is
            model-generated (already covered by outbound scanning when it
            was produced), and English-only NER on it is a pure
            false-positive source. Empty (the default) preserves the
            existing all-detectors-on-every-message behavior.
        """
        # Filter out system messages up-front when the profile says so.
        # This is the single chokepoint for the system-message exclusion;
        # ``scan_text`` (called per remaining message below) is unaware
        # of role and therefore can't accidentally reintroduce them.
        scanned_messages = [
            m for m in messages
            if scan_system_messages or getattr(m, "role", None) != "system"
        ]
        # Detect language once across all message contents so every
        # PresidioDetector call uses the same spaCy model. Mixed-language
        # requests are rare enough that per-message detection isn't
        # worth the complexity.
        joined = " ".join(
            m.content for m in scanned_messages
            if isinstance(getattr(m, "content", None), str)
        )
        language = detect_language(joined)
        all_matches: list[DetectionMatch] = []
        for msg in scanned_messages:
            # Vision-format messages carry list-of-blocks content;
            # flatten text-block contents (rust_dlp_client.py uses
            # the same pattern) so PII scanning still covers the
            # user's prompt but image blocks pass through untouched.
            content = msg.content
            if isinstance(content, list):
                content = "\n".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            elif not isinstance(content, str):
                content = "" if content is None else str(content)
            msg_matches = self.scan_text(content, language=language)
            # P1 Item 1 (option 2): drop NER findings for roles the caller
            # excluded (assistant history) — regex PII detectors stay.
            if ner_skip_roles and getattr(msg, "role", None) in ner_skip_roles:
                msg_matches = [
                    m for m in msg_matches if m.detector_name != "presidio_ner"
                ]
            all_matches.extend(msg_matches)
        return DetectionResult(
            matches=sorted(all_matches, key=lambda m: (m.start, m.end)),
            scanned_direction=direction,
            scan_target="messages",
        )


def get_default_registry(
    disabled_detectors: list[str] | None = None,
    *,
    ner_enabled: bool = True,
) -> DetectorRegistry:
    """Return a registry with all built-in detectors, minus any disabled ones.

    Parameters
    ----------
    disabled_detectors : list[str] | None
        Detector names to exclude.  If None, reads from settings.
    ner_enabled : bool
        Controls the *NER axis* of the registry:

        * ``True`` (the default) → Presidio's PERSON / LOCATION / …
          recognizer is registered (when ``presidio_analyzer`` is
          importable) and the regex-based ``thai_name_no_prefix``
          fallback is **omitted** to avoid duplicate findings.
        * ``False`` → Presidio is skipped entirely AND the
          ``thai_name_no_prefix`` fallback is registered, so a tenant
          who has opted out of NER (or a deployment without Presidio)
          still gets coverage for un-prefixed Thai names.

        The orchestrator constructs both variants once at startup and
        selects per-request from ``ctx.ner_enabled`` — see
        ``GatewayOrchestrator._select_registry``.
    """
    from app.services.dlp.detectors.address import AddressDetector
    from app.services.dlp.detectors.api_key_secret import APIKeySecretDetector
    from app.services.dlp.detectors.bearer_token import BearerTokenDetector
    from app.services.dlp.detectors.biometric_data import BiometricDataDetector
    from app.services.dlp.detectors.credit_card import CreditCardDetector
    from app.services.dlp.detectors.criminal_record import CriminalRecordDetector
    from app.services.dlp.detectors.date_of_birth import DateOfBirthDetector
    from app.services.dlp.detectors.db_connection_string import DbConnectionStringDetector
    from app.services.dlp.detectors.email import EmailDetector
    from app.services.dlp.detectors.health_condition import HealthConditionDetector
    from app.services.dlp.detectors.iban import IBANDetector
    from app.services.dlp.detectors.income import IncomeDetector
    from app.services.dlp.detectors.ip_address import IPAddressDetector
    from app.services.dlp.detectors.line_id import LineIdDetector
    from app.services.dlp.detectors.mac_address import MacAddressDetector
    from app.services.dlp.detectors.medical_record import MedicalRecordDetector
    from app.services.dlp.detectors.passport import PassportDetector
    from app.services.dlp.detectors.password_field import PasswordFieldDetector
    from app.services.dlp.detectors.phone import PhoneDetector
    from app.services.dlp.detectors.private_key import PrivateKeyDetector
    from app.services.dlp.detectors.religion_belief import ReligionBeliefDetector
    from app.services.dlp.detectors.ssn import SSNDetector
    from app.services.dlp.detectors.thai_address import ThaiAddressDetector
    from app.services.dlp.detectors.thai_bank_account import ThaiBankAccountDetector
    from app.services.dlp.detectors.thai_name import ThaiNameDetector
    from app.services.dlp.detectors.thai_name_no_prefix import ThaiNameNoPrefixDetector
    from app.services.dlp.detectors.thai_national_id import ThaiNationalIdDetector
    from app.services.dlp.detectors.thai_promptpay import ThaiPromptPayDetector
    from app.services.dlp.detectors.thai_tax_id import ThaiTaxIdDetector
    from app.services.dlp.detectors.vehicle_plate import VehiclePlateDetector

    if disabled_detectors is None:
        from app.core.config import settings
        disabled_detectors = settings.dlp_disabled_detectors_list

    disabled_set = set(disabled_detectors)

    all_detectors = [
        EmailDetector(),
        PhoneDetector(),
        SSNDetector(),
        CreditCardDetector(),
        IBANDetector(),
        IPAddressDetector(),
        PassportDetector(),
        DateOfBirthDetector(),
        VehiclePlateDetector(),
        BearerTokenDetector(),
        APIKeySecretDetector(),
        PrivateKeyDetector(),
        PasswordFieldDetector(),
        # DLP coverage expansion.
        ThaiNationalIdDetector(),
        ThaiTaxIdDetector(),
        ThaiBankAccountDetector(),
        ThaiPromptPayDetector(),
        ThaiAddressDetector(),
        AddressDetector(),
        IncomeDetector(),
        ThaiNameDetector(),
        DbConnectionStringDetector(),
        MedicalRecordDetector(),
        MacAddressDetector(),
        # PDPA Section 26 sensitive data detectors.
        ReligionBeliefDetector(),
        HealthConditionDetector(),
        CriminalRecordDetector(),
        BiometricDataDetector(),
        # Thai-market contact identifier.
        LineIdDetector(),
    ]

    # Regex-based Thai-name fallback — now registered REGARDLESS of
    # ``ner_enabled``. Original design (May 2026) gated this behind
    # ``not ner_enabled`` on the assumption that Presidio's PERSON
    # recognizer covered bare Thai names whenever NER was on. The
    # assumption was wrong for our model setup: the backend image
    # ships ``en_core_web_sm`` only (no Thai spaCy model), so the
    # English-language NER produces non-deterministic / missed results
    # on bare Thai names like ``สมชาย ใจดี`` / ``สมหญิง รักดี``.
    #
    # The regex is context-gated (requires ``ลูกค้า`` / ``พนักงาน`` /
    # ``ชื่อ`` / … in the ±25-char window) and emits ``priority=-5``
    # so the overlap resolver always prefers Presidio (priority 0) on
    # shared spans. Net effect when both detectors run:
    #   * Presidio hit → wins via priority; regex hit dropped.
    #   * Presidio miss → regex becomes the fallback floor.
    # No duplicate masks, no churn for the operator.
    all_detectors.append(ThaiNameNoPrefixDetector())

    # Presidio NER is optional at runtime. Gate registration on the
    # underlying ``presidio_analyzer`` package being importable — the
    # detector module itself is always importable (its presidio import
    # is lazy, inside ``detect()``), so a plain ``ImportError`` guard
    # on that module would never fire. We check the heavyweight package
    # directly. This also keeps the default registry stable for the
    # detector count / set assertions in ``test_dlp_config.py`` and
    # ``test_detector_registry.py`` on environments where presidio
    # isn't installed (e.g. Python 3.14 local dev).
    #
    # Skipped entirely when ``ner_enabled=False`` — the fallback
    # ``thai_name_no_prefix`` registered above takes over the bare-name
    # coverage and we avoid loading the (potentially expensive) spaCy
    # analyzer for a registry that won't use it.
    if ner_enabled:
        try:
            import importlib
            importlib.import_module("presidio_analyzer")
            from app.services.dlp.detectors.presidio_ner import PresidioDetector
            all_detectors.append(PresidioDetector())
        except ImportError:
            pass

    registry = DetectorRegistry()
    for detector in all_detectors:
        if detector.name not in disabled_set:
            registry.register(detector)
    # Attach the process-wide custom-detector provider (None until the loader
    # installs it at startup) so admin-defined detectors run on this registry.
    registry.set_custom_provider(_global_custom_provider)
    return registry


def get_built_in_detector_count(*, ner_enabled: bool = True) -> int:
    """Number of built-in detectors (excludes admin-defined custom ones).

    Sprint 6 verification helper — custom detectors are dynamic and provided
    at runtime, so they never count toward the built-in total.
    """
    return len(get_default_registry(ner_enabled=ner_enabled))
