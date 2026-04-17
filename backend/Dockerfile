FROM python:3.11-slim

WORKDIR /app

# Install system deps for psycopg (PostgreSQL driver)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# spaCy models for Presidio NER. en_core_web_lg covers English PERSON /
# LOCATION / ORG / GPE; th_core_news_lg adds Thai PER / LOC / ORG. Both
# downloads are best-effort — spaCy 3.7.4's ``spacy download`` command
# has a known stale-catalog bug that intermittently generates 404 URLs
# like ``.../-en_core_web_lg/-en_core_web_lg.tar.gz`` (note the leading
# dash). A failed download puts ``ner_status.load_analyzer_sync()``
# into an ``error`` state at runtime — the NER toggle surfaces the
# error in the admin UI and PresidioDetector no-ops. The gateway's
# regex DLP detectors are unaffected either way.
RUN python -m spacy download en_core_web_lg || true
RUN python -m spacy download th_core_news_lg || true

COPY . .

# Run migrations then start with gunicorn managing uvicorn workers
CMD ["sh", "-c", "python -m alembic upgrade head && gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8080 --timeout 60 --graceful-timeout 30 --worker-connections 100"]
