"""Detector registry: collects, runs, and aggregates DLP detectors.

The registry is the entry point that the DLP scanner calls.  It runs all
registered detectors over input text, merges their matches, and returns
a sorted, normalized result.

Usage::

    registry = get_default_registry()
    result = registry.scan_messages(messages, direction="input")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.dlp import DetectionMatch, DetectionResult
from app.services.dlp.detectors.base import BaseDetector
from app.services.dlp.language import detect_language

if TYPE_CHECKING:
    from app.schemas.common import Message


class DetectorRegistry:
    """Ordered collection of detectors with scan-and-aggregate methods."""

    def __init__(self) -> None:
        self._detectors: list[BaseDetector] = []
        self._names: set[str] = set()

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

    def scan_text(
        self, text: str, *, language: str = "en"
    ) -> list[DetectionMatch]:
        """Run all detectors over *text*, return matches sorted by start offset.

        Detectors whose class defines ``NEEDS_LANGUAGE = True`` receive
        the ``language`` kwarg (currently only ``PresidioDetector``).
        Regex detectors ignore it to keep their signatures unchanged.
        """
        matches: list[DetectionMatch] = []
        for detector in self._detectors:
            if getattr(detector, "NEEDS_LANGUAGE", False):
                matches.extend(detector.detect(text, language=language))
            else:
                matches.extend(detector.detect(text))
        matches.sort(key=lambda m: (m.start, m.end))
        return matches

    def scan_messages(
        self,
        messages: list[Message],
        *,
        direction: str = "input",
    ) -> DetectionResult:
        """Scan all message contents and return an aggregated DetectionResult.

        Parameters
        ----------
        messages : list[Message]
            Chat messages to scan (only the ``content`` field is inspected).
        direction : str
            ``"input"`` for request messages, ``"output"`` for response content.
        """
        # Detect language once across all message contents so every
        # PresidioDetector call uses the same spaCy model. Mixed-language
        # requests are rare enough that per-message detection isn't
        # worth the complexity.
        joined = " ".join(
            m.content for m in messages
            if isinstance(getattr(m, "content", None), str)
        )
        language = detect_language(joined)
        all_matches: list[DetectionMatch] = []
        for msg in messages:
            all_matches.extend(
                self.scan_text(msg.content, language=language)
            )
        return DetectionResult(
            matches=sorted(all_matches, key=lambda m: (m.start, m.end)),
            scanned_direction=direction,
            scan_target="messages",
        )


def get_default_registry(
    disabled_detectors: list[str] | None = None,
) -> DetectorRegistry:
    """Return a registry with all built-in detectors, minus any disabled ones.

    Parameters
    ----------
    disabled_detectors : list[str] | None
        Detector names to exclude.  If None, reads from settings.
    """
    from app.services.dlp.detectors.api_key_secret import APIKeySecretDetector
    from app.services.dlp.detectors.bearer_token import BearerTokenDetector
    from app.services.dlp.detectors.credit_card import CreditCardDetector
    from app.services.dlp.detectors.date_of_birth import DateOfBirthDetector
    from app.services.dlp.detectors.db_connection_string import DbConnectionStringDetector
    from app.services.dlp.detectors.email import EmailDetector
    from app.services.dlp.detectors.iban import IBANDetector
    from app.services.dlp.detectors.ip_address import IPAddressDetector
    from app.services.dlp.detectors.mac_address import MacAddressDetector
    from app.services.dlp.detectors.medical_record import MedicalRecordDetector
    from app.services.dlp.detectors.passport import PassportDetector
    from app.services.dlp.detectors.password_field import PasswordFieldDetector
    from app.services.dlp.detectors.phone import PhoneDetector
    from app.services.dlp.detectors.private_key import PrivateKeyDetector
    from app.services.dlp.detectors.ssn import SSNDetector
    from app.services.dlp.detectors.thai_address import ThaiAddressDetector
    from app.services.dlp.detectors.thai_bank_account import ThaiBankAccountDetector
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
        DbConnectionStringDetector(),
        MedicalRecordDetector(),
        MacAddressDetector(),
    ]

    # Presidio NER is optional at runtime. Gate registration on the
    # underlying ``presidio_analyzer`` package being importable — the
    # detector module itself is always importable (its presidio import
    # is lazy, inside ``detect()``), so a plain ``ImportError`` guard
    # on that module would never fire. We check the heavyweight package
    # directly. This also keeps the default registry stable for the
    # detector count / set assertions in ``test_dlp_config.py`` and
    # ``test_detector_registry.py`` on environments where presidio
    # isn't installed (e.g. Python 3.14 local dev).
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
    return registry
