FROM python:3.11-slim

WORKDIR /app

# Install system deps for psycopg (PostgreSQL driver)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
# Install base + dev deps (respx, etc). Test deps are tiny and having
# them in the image lets `make test` work against the running
# container without a separate install step.
RUN pip install --no-cache-dir -r requirements-dev.txt gunicorn

# spaCy models for Presidio NER.
#
# ``spacy download`` is broken on spaCy 3.7.4 for our image — its
# catalog lookup constructs a URL like
# ``/releases/download/-en_core_web_sm/-en_core_web_sm.tar.gz`` (note
# the leading dashes) that 404s. We pin direct wheel URLs from the
# spacy-models GitHub releases instead. The URLs are stable once a
# release tag is cut, so this avoids the download-time catalog lookup
# entirely.
#
# ``en_core_web_sm`` (~12 MB) is the minimum viable English model for
# Presidio PERSON/LOCATION/ORG detection. The large variant
# (``en_core_web_lg``, ~580 MB) gives better accuracy but bloats the
# image — intentionally left optional. ``th_core_news_lg`` is similarly
# optional; ``load_analyzer_sync`` falls back to English-only when Thai
# is absent. Installation is best-effort (|| true) so the gateway still
# boots even on a blocked network; the startup check in
# ``presidio_ner.py`` logs a clear WARNING when no model is present.
RUN pip install --no-cache-dir \
    "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl" \
    || true

COPY . .

# Run migrations then start with gunicorn managing uvicorn workers.
# GUNICORN_WORKERS is the single worker-count knob (also read by
# scripts/start.sh and the loadtest compose) — remember the per-provider
# concurrency caps are per-worker semaphores, so a provider's effective
# ceiling is cap × GUNICORN_WORKERS.
CMD ["sh", "-c", "python -m alembic upgrade head && gunicorn app.main:app -w ${GUNICORN_WORKERS:-4} -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8080 --timeout 60 --graceful-timeout 30 --worker-connections 100"]
