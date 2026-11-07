FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned versions match the backend's NER requirements so the analyzer
# behaves identically across the gateway and this service.
RUN pip install --no-cache-dir \
        "fastapi==0.115.0" \
        "uvicorn[standard]==0.30.6" \
        "pydantic==2.9.2" \
        "presidio-analyzer==2.2.354" \
        "spacy==3.7.4"

# Pin the spaCy model via direct wheel URL — ``python -m spacy download``
# is unreliable when spaCy's stale model catalog 404s (it routes empty
# versions into the URL like ``releases/download/-en_core_web_sm/``).
# The wheel URL bypasses the catalog and pins the exact build, which
# is what we want for reproducible images. Both downloads stay
# best-effort so a transient network blip doesn't fail the build —
# the service falls back gracefully when no model loads (regex
# detectors still run on the gateway).
RUN (pip install --no-cache-dir "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl" \
        || echo "en_core_web_sm install skipped") \
    && (pip install --no-cache-dir "https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.7.1/en_core_web_lg-3.7.1-py3-none-any.whl" \
        || echo "en_core_web_lg install skipped")

# Thai NER (FIX37): PyThaiNLP's CRF ThaiNER. Chosen over the
# WangchanBERTa transformer model after measuring both on the FIX37
# corpus - see the rationale block in ner_service/main.py. ~10MB of
# corpus data and no torch, so the image and the p95 stay where they
# were. The corpus is downloaded at BUILD time so a cold worker does not
# fetch on its first request (N6: load at start, not on demand).
RUN pip install --no-cache-dir "pythainlp==5.0.4" "python-crfsuite==0.9.11" \
    && python -c "from pythainlp.tag.named_entity import NER; NER(engine='thainer').tag('คุณสมชาย ใจดี')" \
    && echo "thainer corpus warmed"

# spaCy Thai: still NOT installed. spaCy publishes no official Thai
# pipeline - ``explosion/spacy-models`` has no ``th_core_news_*``
# release tag, so ``_load_analyzer`` finds nothing to register for
# "th" and Presidio would answer Thai requests with the English model.
# The probe in ``main.py::_load_analyzer`` is left in place for the day
# a wheel exists; until then the CRF tagger above owns Thai, and the
# regex ``thai_name`` / ``thai_address`` detectors remain the floor
# under it (they are what runs when NER_THAI_ENABLED=0).

# Copy only the ner_service package — narrower than ``COPY .`` so the
# 7+ unrelated backend dirs (app/, migrations/, tests/) don't end up
# inside the NER image. The package lands at /app/ner_service so
# ``ner_service.main:app`` resolves under uvicorn.
COPY ner_service /app/ner_service
WORKDIR /app

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=60s \
    CMD curl -fsS http://localhost:8001/health | grep -q '"status":"ready"' || exit 1

# Multi-worker uvicorn — each worker is its own process with its own
# spaCy/Presidio analyzer (loaded via the lifespan hook in main.py).
# Worker count is driven by ${NER_WORKERS} so it can be tuned per env
# without rebuilding the image. FIX133 A1 — empty (the default) means
# computed from the container: ner_service/sizing.py, min(cpus // 3,
# RAM / 450 MB per worker), at least 2 — 4 on the live box.
ENV NER_WORKERS=
# FIX126 O6 - provenance: every build carries the commit it was built from.
# compose passes REVISION/CREATED as build args (`REVISION=$(git rev-parse
# --short HEAD) docker compose ... build`); scripts/build-images.sh sets both
# and builds from a clean `git archive` of that commit. Declared at the END
# of the final stage so a new revision never busts the cached layers above.
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.created=$CREATED

CMD ["sh", "-c", "W=$(python -m ner_service.sizing) && echo \"ner_workers=$W (NER_WORKERS='${NER_WORKERS}')\" && exec uvicorn ner_service.main:app --host 0.0.0.0 --port 8001 --workers $W"]
