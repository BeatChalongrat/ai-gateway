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
# without rebuilding the image; default of 4 matches the 4-core
# reservation in docker-compose.yml.
ENV NER_WORKERS=4
CMD ["sh", "-c", "exec uvicorn ner_service.main:app --host 0.0.0.0 --port 8001 --workers ${NER_WORKERS}"]
