"""Dedicated NER microservice.

Runs in its own container so the spaCy model (≈580MB for en_core_web_lg)
is loaded ONCE per pod, independent of the gateway's worker fleet.

The gateway calls this service via :mod:`app.services.ner_service_client`.
When this service is unreachable the client falls back to an empty
result, so the gateway degrades gracefully — DLP regex detectors keep
running, only NER coverage is lost until the service comes back.

Exposed endpoints:

* ``POST /analyze`` — body ``{"text": str, "language": "en"|"th"}``,
  returns ``{"entities": [{"text", "label", "start", "end", "score"}]}``.
* ``GET /health`` — readiness/liveness probe target; returns
  ``{"status": "ready" | "loading" | "error", "model": "..."}``.

Auth: none. The service is internal; expose only inside the cluster
network. The gateway connects via the docker-compose service name
(``ner-service:8001``) or the K8s service DNS.
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Module-level singletons. The analyzer is loaded once at startup and
# reused for every request — concurrent calls share the same instance
# because spaCy's analyze pipeline is read-only.
_analyzer: Any | None = None
_model_label: str = ""
_status: str = "loading"
_status_lock = threading.Lock()

# Default candidate models. The container ships en_core_web_lg as
# preferred and en_core_web_sm as guaranteed fallback (12MB) so even
# a partial download leaves the service functional.
_EN_CANDIDATES = ("en_core_web_lg", "en_core_web_sm")
_TH_CANDIDATES = ("th_core_news_lg", "th_core_news_sm")

_DEFAULT_ENTITIES = ("PERSON", "LOCATION", "ORGANIZATION", "GPE", "NRP")

# FIX37 — Thai NER. spaCy publishes no Thai pipeline, so Presidio has
# nothing to load for "th" and the service answered every Thai request
# with the English model (measured: zero useful spans). PyThaiNLP's CRF
# ThaiNER fills that hole.
#
# Why the CRF and not WangchanBERTa: both were measured on the FIX37
# corpus. WangchanBERTa (thainer-corpus-v2, 419MB + torch) scored
# name recall 0.80 at p50 232ms / p95 463ms per message; the CRF scored
# 0.75 at p50 3.1ms / p95 6.8ms with a ~10MB corpus and no torch. The
# inbound latency budget for this round is +200ms at p95, so the
# transformer model is out on latency and image size (~+2GB) for five
# points of raw recall that the scoring layer recovers from context.
#
# The confidence the CRF cannot give us is supplied by the gateway's
# scoring layer (``thai_ner_scoring``), so every span leaves here with a
# flat NER_TH_SCORE and is judged on context downstream.
_THAI_NER_SCORE = 0.60
_thai_tagger: Any | None = None
_thai_status: str = "not_loaded"


def _load_thai_tagger() -> None:
    """Load the CRF ThaiNER tagger once per worker. Best-effort."""
    global _thai_tagger, _thai_status
    if os.environ.get("NER_THAI_ENABLED", "1") not in ("1", "true", "True"):
        _thai_status = "disabled"
        return
    try:
        from pythainlp.tag.named_entity import NER

        tagger = NER(engine="thainer")
        tagger.tag("อุ่นเครื่องคุณสมชาย ใจดี")  # warm the corpus load
        _thai_tagger = tagger
        _thai_status = "ready"
    except Exception as exc:  # noqa: BLE001
        _thai_tagger = None
        _thai_status = f"error: {exc}"[:200]


def _analyze_thai(text: str) -> list[dict]:
    """IOB tags -> character-offset spans.

    PyThaiNLP's tokenizer is lossless (``"".join(tokens) == text``), so
    offsets come from accumulating token lengths — no re-search, and no
    risk of landing on the wrong occurrence of a repeated name.
    """
    tagger = _thai_tagger
    if tagger is None:
        return []
    out: list[dict] = []
    pos = 0
    cur_label: str | None = None
    cur_start = 0
    cur_end = 0
    for token, tag in tagger.tag(text):
        start, end = pos, pos + len(token)
        pos = end
        base = tag.split("-", 1)[-1] if "-" in tag else tag
        if tag.startswith("B-"):
            if cur_label:
                out.append({"label": cur_label, "start": cur_start, "end": cur_end})
            cur_label, cur_start, cur_end = base, start, end
        elif tag.startswith("I-") and cur_label == base:
            cur_end = end
        else:
            if cur_label:
                out.append({"label": cur_label, "start": cur_start, "end": cur_end})
            cur_label = None
    if cur_label:
        out.append({"label": cur_label, "start": cur_start, "end": cur_end})
    return out
# Per-language confidence floors. Thai NER routinely emits 0.7-0.85
# PERSON hits on conversational verbs ("ตอบสั้นๆ", "ช่วยสรุป") that
# the English model never produces because those words don't exist
# in its vocabulary. Raising the Thai floor compensates for the
# noisier base distribution. ``NER_SCORE_THRESHOLD`` is kept as a
# back-compat alias for ``NER_SCORE_THRESHOLD_EN``.
_DEFAULT_SCORE_THRESHOLD_EN = float(
    os.environ.get(
        "NER_SCORE_THRESHOLD_EN",
        os.environ.get("NER_SCORE_THRESHOLD", "0.7"),
    )
)
_DEFAULT_SCORE_THRESHOLD_TH = float(
    os.environ.get("NER_SCORE_THRESHOLD_TH", "0.85")
)


def _default_threshold_for(language: str) -> float:
    if language == "th":
        return _DEFAULT_SCORE_THRESHOLD_TH
    return _DEFAULT_SCORE_THRESHOLD_EN


def _first_installed(candidates: tuple[str, ...]) -> str | None:
    import spacy

    for name in candidates:
        try:
            spacy.util.get_package_path(name)
            return name
        except Exception:
            continue
    return None


def _load_analyzer() -> None:
    """Initialise the Presidio AnalyzerEngine on first boot.

    Loading is synchronous — the readiness probe stays at ``loading``
    until this returns. The K8s ``initialDelaySeconds`` on the
    readiness probe (10s) plus the analyzer's typical ~3-8s cold start
    means a pod is rolled into service only after the model is ready.
    """
    global _analyzer, _model_label, _status

    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        en_model = _first_installed(_EN_CANDIDATES)
        th_model = _first_installed(_TH_CANDIDATES)

        if en_model is None:
            raise RuntimeError(
                "No English spaCy model installed. Build the image with "
                "`python -m spacy download en_core_web_sm`."
            )

        models_cfg = [{"lang_code": "en", "model_name": en_model}]
        languages = ["en"]
        if th_model is not None:
            models_cfg.append({"lang_code": "th", "model_name": th_model})
            languages.append("th")

        try:
            provider = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": models_cfg,
                }
            )
            engine = AnalyzerEngine(
                nlp_engine=provider.create_engine(),
                supported_languages=languages,
            )
        except Exception:
            # Two-language config rejected — fall back to English-only.
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
            languages = ["en"]

        with _status_lock:
            _analyzer = engine
            _model_label = "en=" + en_model + (
                f", th={th_model}" if "th" in languages and th_model else ""
            )
            _status = "ready"
    except Exception as exc:
        with _status_lock:
            _analyzer = None
            _model_label = ""
            _status = f"error: {exc}"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Load on startup so the very first request doesn't pay the cold
    # start. The K8s readiness probe gates traffic on /health == "ready".
    _load_analyzer()
    _load_thai_tagger()
    yield


app = FastAPI(title="AI Gateway NER Service", lifespan=_lifespan)


class AnalyzeRequest(BaseModel):
    text: str = Field(..., max_length=200_000)
    language: str = Field(default="en")
    entities: list[str] | None = None
    score_threshold: float | None = None


class Entity(BaseModel):
    text: str
    label: str
    start: int
    end: int
    score: float


class AnalyzeResponse(BaseModel):
    entities: list[Entity]


class HealthResponse(BaseModel):
    status: str
    model: str


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    with _status_lock:
        return HealthResponse(
            status=_status, model=_model_label + f", th_ner={_thai_status}"
        )


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    # FIX37 — Thai goes to the Thai tagger. This branch is deliberately
    # ahead of the Presidio readiness check: Thai coverage must not
    # depend on the English analyzer having loaded.
    if req.language == "th" and _thai_tagger is not None:
        spans = _analyze_thai(req.text)
        wanted = {e.upper() for e in (req.entities or _DEFAULT_ENTITIES)}
        return AnalyzeResponse(
            entities=[
                Entity(
                    text=req.text[s["start"]:s["end"]],
                    label=s["label"],
                    start=s["start"],
                    end=s["end"],
                    score=_THAI_NER_SCORE,
                )
                for s in spans
                if s["label"].upper() in wanted
            ]
        )

    with _status_lock:
        analyzer = _analyzer
        status = _status
    if analyzer is None:
        # The gateway client treats a 503 as "fall back to empty list",
        # so the request still flows — just without NER coverage.
        raise HTTPException(status_code=503, detail=f"analyzer not ready: {status}")

    entities = req.entities or list(_DEFAULT_ENTITIES)
    threshold = (
        req.score_threshold
        if req.score_threshold is not None
        else _default_threshold_for(req.language)
    )

    supported = set(getattr(analyzer, "supported_languages", ["en"]) or ["en"])
    languages_to_try: list[str] = []
    if req.language in supported:
        languages_to_try.append(req.language)
    if "en" in supported and "en" not in languages_to_try:
        languages_to_try.append("en")
    if not languages_to_try:
        languages_to_try = [req.language]

    results: list[Any] = []
    for lang in languages_to_try:
        try:
            results = analyzer.analyze(
                text=req.text,
                language=lang,
                entities=entities,
                score_threshold=threshold,
            )
            if results:
                break
        except Exception:
            continue

    return AnalyzeResponse(
        entities=[
            Entity(
                text=req.text[r.start : r.end],
                label=str(r.entity_type),
                start=int(r.start),
                end=int(r.end),
                score=float(getattr(r, "score", 1.0)),
            )
            for r in results
            if float(getattr(r, "score", 1.0)) >= threshold
        ]
    )
