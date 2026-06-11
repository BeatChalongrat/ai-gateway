"""HTTP client for the dedicated NER microservice.

The gateway used to load the Presidio AnalyzerEngine (with the
~580MB en_core_web_lg spaCy model) inside every worker process. That
made a fresh 100-pod cluster carry 100 × spaCy models in resident
memory and serialised NER through the request event loop. The fix
factors NER into its own service (``backend/ner_service``); this
client is the gateway-side adapter.

Failure model: this client NEVER raises out of ``analyze``. Any
network error, timeout, or 5xx from the service is logged at WARNING
and translated to an empty entity list. The DLP regex detectors keep
running, so PII coverage degrades gracefully (regex stays, NER goes
dark) instead of breaking the request pipeline.

Singleton: one ``httpx.AsyncClient`` lives for the process lifetime.
Connections are pooled (max=100, keepalive=20) so a burst of
concurrent DLP scans reuses TCP connections instead of paying the
handshake cost per request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class NerEntity:
    text: str
    label: str
    start: int
    end: int
    score: float


# Per-asyncio-loop client registry. Reusing a single module-level
# ``httpx.AsyncClient`` across calls breaks under the sync-bridge
# pattern (``analyze_sync``): every sync caller spins up its own
# ``asyncio.run`` event loop, the client's underlying connections
# are bound to whichever loop created them, and a second call from
# a fresh loop hits ``unable to perform operation on <TCPTransport
# closed>``. We sidestep that by keying the client cache on the
# current loop's id and letting GC discard the entry when the loop
# itself goes away.
_clients_by_loop: dict[int, httpx.AsyncClient] = {}


def _client_for_current_loop() -> httpx.AsyncClient | None:
    """Return an ``httpx.AsyncClient`` bound to the current event loop.

    Returns ``None`` when ``settings.ner_service_url`` is empty — that's
    the signal to use the legacy in-process Presidio path
    (``presidio_ner.py`` checks for this and short-circuits).
    """
    if not settings.ner_service_url:
        return None
    import asyncio

    loop = asyncio.get_event_loop()
    key = id(loop)
    client = _clients_by_loop.get(key)
    if client is None:
        client = httpx.AsyncClient(
            base_url=settings.ner_service_url.rstrip("/"),
            timeout=settings.ner_service_timeout_seconds,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
        )
        _clients_by_loop[key] = client
    return client


async def analyze(
    text: str,
    *,
    language: str = "en",
    entities: list[str] | None = None,
    score_threshold: float | None = None,
) -> list[NerEntity]:
    """Call the NER service and return its findings.

    Returns ``[]`` if the service is not configured, unreachable, or
    returns an error — never raises. Callers can treat this as "NER
    saw nothing".

    Belt-and-braces language routing: the orchestrator's
    ``DetectorRegistry.scan_messages`` already calls
    ``language.detect_language`` once across the joined message text
    and threads the result down — but if a caller bypasses that
    (direct service call, test fixture, future async detector), we
    re-detect here so Thai text never gets analyzed by the English
    model just because the default kwarg said so.
    """
    client = _client_for_current_loop()
    if client is None:
        return []

    if language == "en":
        from app.services.dlp.language import detect_language

        detected = detect_language(text)
        if detected != "en":
            language = detected

    payload: dict[str, Any] = {"text": text, "language": language}
    if entities is not None:
        payload["entities"] = entities
    if score_threshold is not None:
        payload["score_threshold"] = score_threshold

    try:
        resp = await client.post("/analyze", json=payload)
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        logger.warning("ner_service_timeout", text_len=len(text))
        return []
    except httpx.HTTPStatusError as exc:
        # 503 == "analyzer still warming up" — common during boot.
        # Anything else is unexpected.
        level = logger.info if exc.response.status_code == 503 else logger.warning
        level(
            "ner_service_http_error",
            status=exc.response.status_code,
            detail=exc.response.text[:200],
        )
        return []
    except Exception as exc:
        logger.warning("ner_service_unreachable", error=str(exc)[:200])
        return []

    raw_entities = data.get("entities") or []
    return [
        NerEntity(
            text=str(e.get("text", "")),
            label=str(e.get("label", "")),
            start=int(e.get("start", 0)),
            end=int(e.get("end", 0)),
            score=float(e.get("score", 1.0)),
        )
        for e in raw_entities
    ]


def analyze_sync(
    text: str,
    *,
    language: str = "en",
    entities: list[str] | None = None,
    score_threshold: float | None = None,
) -> list[NerEntity]:
    """Blocking wrapper for callers that haven't been made async yet.

    The DLP scan path is currently synchronous (``BaseDetector.detect``
    returns a list, not a coroutine). Until that pipeline is converted,
    we wrap the async client call with a one-off event loop so the
    detector can stay sync while the transport itself is async.

    The loop is created fresh on each call and disposed immediately —
    cheap on the hot path, and avoids tangling per-thread loops. When
    the registry / detectors are eventually made async, callers should
    switch to :func:`analyze` directly.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    async def _analyze_and_evict() -> list[NerEntity]:
        # ``asyncio.run()`` below creates a THROWAWAY event loop per call
        # (one per request when NER is on). ``analyze`` caches an
        # ``httpx.AsyncClient`` in the module-global ``_clients_by_loop``
        # keyed by ``id(loop)``; that client's transport pins the loop, so
        # without this cleanup the loop's id is never reused and every call
        # leaks a dead-loop + client pair — the unbounded RSS growth the
        # load test caught (~2 GiB / 90s at 100c). Close + evict the
        # client for THIS loop before ``asyncio.run`` disposes it.
        run_loop = asyncio.get_event_loop()
        try:
            return await analyze(
                text,
                language=language,
                entities=entities,
                score_threshold=score_threshold,
            )
        finally:
            client = _clients_by_loop.pop(id(run_loop), None)
            if client is not None:
                try:
                    await client.aclose()
                except Exception:  # noqa: BLE001
                    pass

    if loop is None or not loop.is_running():
        return asyncio.run(_analyze_and_evict())

    # We're already inside a running loop. Push to a thread so we
    # don't deadlock on it. This is the path FastAPI's ``def`` handlers
    # take when they touch the detector chain. The thread's
    # ``asyncio.run`` loop is cleaned up by ``_analyze_and_evict`` too.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_analyze_and_evict())).result()


async def aclose() -> None:
    """Close every per-loop client. Called from FastAPI shutdown hook."""
    for client in list(_clients_by_loop.values()):
        try:
            await client.aclose()
        except Exception:
            pass
    _clients_by_loop.clear()
