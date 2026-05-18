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
_DEFAULT_SCORE_THRESHOLD = float(os.environ.get("NER_SCORE_THRESHOLD", "0.7"))


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
        return HealthResponse(status=_status, model=_model_label)


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
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
        else _DEFAULT_SCORE_THRESHOLD
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
