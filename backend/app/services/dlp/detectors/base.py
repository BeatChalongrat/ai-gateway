"""Abstract base class for DLP detectors.

Every detector implements a single method: detect(text) -> list[DetectionMatch].
Detectors are stateless, tenant-unaware, and policy-free.  They find patterns;
they do not decide what to do about them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.dlp import DetectionMatch, SensitivityLevel


class BaseDetector(ABC):
    """Abstract detector that scans text for a specific class of sensitive data.

    Subclasses must set these class attributes:

        name : str               — unique detector identifier (e.g. "email")
        category : str           — broad bucket ("pii", "financial", "secret")
        default_sensitivity : SensitivityLevel — severity assigned to matches

    And implement:

        detect(text: str) -> list[DetectionMatch]

    Optional class attribute:

        priority : int           — specificity hint for overlap resolution
                                   (see DetectionMatch.priority). Default 0.
                                   Raise it on detectors that should beat
                                   generic over-matchers on the same span
                                   (e.g. ``thai_national_id`` over ``phone``).
    """

    name: str
    category: str
    default_sensitivity: SensitivityLevel
    priority: int = 0

    @abstractmethod
    def detect(self, text: str) -> list[DetectionMatch]:
        """Scan *text* and return all matches found.

        Each match must have accurate ``start`` / ``end`` character offsets
        such that ``text[start:end] == match.matched_text``.
        """

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} category={self.category!r}>"
