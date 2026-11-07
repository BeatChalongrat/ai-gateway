"""Module-level state and loader for the Presidio NER analyzer stage.

Presidio is an optional runtime dependency. The registry registers
``PresidioDetector`` unconditionally (the detector no-ops when
``get_analyzer()`` returns None), and this module owns the lifecycle
of the shared analyzer instance:

    not_loaded ──(enable toggled on)──► loading ──┬──► ready
                                                   └──► error
    ready / error ──(enable toggled off)──► not_loaded

Loading is done from a background task scheduled by
``PATCH /admin/settings`` (see ``admin_dlp.patch_settings``). The
analyzer is heavy (~1s on first init, ~hundreds of MB resident), so we
keep exactly one instance per process and swap it in under a lock.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Literal

NerStatusLiteral = Literal["not_loaded", "loading", "ready", "error"]


@dataclass
class NerStatus:
    status: NerStatusLiteral = "not_loaded"
    message: str | None = None


_state = NerStatus()
_state_lock = threading.Lock()

# The cached AnalyzerEngine instance, or None when not loaded / errored.
# Typed as Any so importing this module never requires presidio — the
# real type is presidio_analyzer.AnalyzerEngine.
_analyzer: Any | None = None
_analyzer_lock = threading.Lock()


def get_status() -> NerStatus:
    with _state_lock:
        return NerStatus(status=_state.status, message=_state.message)


def set_status(status: NerStatusLiteral, message: str | None = None) -> None:
    with _state_lock:
        _state.status = status
        _state.message = message


def get_analyzer() -> Any | None:
    """Return the cached AnalyzerEngine, or None if NER isn't loaded.

    Detectors that need the analyzer should call this on every
    ``detect()`` invocation — the return value flips to None when the
    admin toggles NER off via ``reset()``.
    """
    with _analyzer_lock:
        return _analyzer


def reset() -> None:
    """Drop the cached analyzer and return status to ``not_loaded``.

    Called when ``ner_enabled`` is toggled off via the admin API so that
    memory is reclaimed and a subsequent re-enable reloads fresh state.
    """
    global _analyzer
    with _analyzer_lock:
        _analyzer = None
    set_status("not_loaded", None)


def begin_load() -> None:
    """Mark the analyzer as loading. Callers should then run the loader."""
    set_status("loading", None)


def load_analyzer_sync() -> None:
    """Initialise a real Presidio AnalyzerEngine and cache it.

    Presidio is an optional dependency — if it is not installed, or a
    spaCy model is missing, the load fails gracefully and
    ``PresidioDetector`` continues to no-op. Failures are recorded on
    the status object so the admin UI can surface them.
    """
    global _analyzer
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        # Pick the best English spaCy model that's actually installed.
        # The Dockerfile best-effort installs ``en_core_web_lg``
        # (preferred; ~580 MB; high accuracy) and ``en_core_web_sm``
        # (~12 MB; reliable fallback) — spaCy 3.7.4 has a stale-catalog
        # bug that makes _lg fail to download intermittently, so we
        # cannot assume it's present. ``en_core_web_sm`` is the
        # always-available fallback.
        import spacy

        def _first_installed(candidates: tuple[str, ...]) -> str | None:
            for name in candidates:
                try:
                    spacy.util.get_package_path(name)
                    return name
                except Exception:
                    continue
            return None

        en_model = _first_installed(("en_core_web_lg", "en_core_web_sm"))
        th_model = _first_installed(("th_core_news_lg", "th_core_news_sm"))

        if en_model is None:
            raise RuntimeError(
                "No English spaCy model available. Install one with "
                "`python -m spacy download en_core_web_sm`."
            )

        # Two-language config when a Thai model is available, otherwise
        # English-only. AnalyzerEngine still handles mixed-language
        # text under English-only mode via the underlying spaCy model.
        try:
            models_cfg = [{"lang_code": "en", "model_name": en_model}]
            if th_model is not None:
                models_cfg.append({"lang_code": "th", "model_name": th_model})
            provider = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": models_cfg,
                }
            )
            engine = AnalyzerEngine(
                nlp_engine=provider.create_engine(),
                supported_languages=(["en", "th"] if th_model else ["en"]),
            )
        except Exception:
            # Second attempt: English-only, in case the two-language
            # config was rejected by NlpEngineProvider for any reason.
            provider = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "en", "model_name": en_model}],
                }
            )
            engine = AnalyzerEngine(
                nlp_engine=provider.create_engine(),
                supported_languages=["en"],
            )

        with _analyzer_lock:
            _analyzer = engine
        set_status("ready", f"loaded model(s): en={en_model}" + (f", th={th_model}" if th_model else ""))
    except Exception as exc:  # pragma: no cover - exercised only with presidio
        with _analyzer_lock:
            _analyzer = None
        set_status("error", f"NER load failed: {exc}")
